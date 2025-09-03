import boto3
import time
import json
import requests

ecs = boto3.client("ecs")
ec2 = boto3.client("ec2")
elbv2 = boto3.client("elbv2")
cf = boto3.client("cloudformation")

def get_cf_output(stack_name, key):
    """Get CloudFormation output value by key"""
    stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    outputs = stack["Outputs"]
    for output in outputs:
        if output["OutputKey"] == key:
            return output["OutputValue"]
    raise Exception(f"Output key {key} not found in stack {stack_name}")

def generate_session_id(public_ip):
    """Generate unique session ID using EC2 public IP"""
    return public_ip.replace('.', '-')

def lambda_handler(event, context):
    print("Received event:", json.dumps(event, indent=2))

    detail = event["detail"]
    cluster = detail["cluster"]
    task_arn = detail["task_arn"]
    response_url = detail["response_url"]

    try:
        start_time = time.time()

        for attempt in range(100):
            task = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])["tasks"][0]
            last_status = task["lastStatus"]
            print(f"[Attempt {attempt}] Task status: {last_status}")

            if last_status == "RUNNING":
                break
            time.sleep(5)
        else:
            send_response(response_url, "Task is taking too long to start. Try again in a minute.")
            return

        elapsed = time.time() - start_time
        print(f"Total wait time: {elapsed:.1f} seconds")

        container_instance_arn = task["containerInstanceArn"]
        # Get the actual EC2 instance ID from the container instance
        container_instances = ecs.describe_container_instances(
            cluster=cluster,
            containerInstances=[container_instance_arn]
        )
        instance_id = container_instances["containerInstances"][0]["ec2InstanceId"]
        instance_desc = ec2.describe_instances(InstanceIds=[instance_id])
        public_ip = instance_desc["Reservations"][0]["Instances"][0]["PublicIpAddress"]

        tutorial_port = int(detail.get("port"))
        query_string = detail.get("query_string", "")
        custom_response_blocks = detail.get("custom_response_blocks", "")
        stack_name = detail.get("stack")
        user = detail.get("user", "unknown")

        # Get ALB configuration from CloudFormation stack
        try:
            domain_name = get_cf_output(stack_name, "DomainName")
            tutorial_name = get_cf_output(stack_name, "TutorialName")
            alb_listener_arn = get_cf_output(stack_name, "ALBHTTPSListenerArn")

            # Generate unique session ID using public IP
            session_id = generate_session_id(public_ip)
            print(f"Generated session ID: {session_id} (from IP: {public_ip})")

            # Create a dedicated target group for this user session
            user_target_group_name = f"{stack_name}-{session_id}"[:32]  # ALB name limit
            print(f"Creating target group: {user_target_group_name}")

            vpc_id = get_cf_output(stack_name, "VPCId")
            user_target_group_response = elbv2.create_target_group(
                Name=user_target_group_name,
                Protocol='HTTP',
                Port=tutorial_port,
                VpcId=vpc_id,
                TargetType='instance',
                HealthCheckProtocol='HTTP',
                HealthCheckPort=str(tutorial_port),
                HealthCheckPath='/',
                HealthCheckIntervalSeconds=30,
                HealthCheckTimeoutSeconds=5,
                HealthyThresholdCount=2,
                UnhealthyThresholdCount=5,
                Tags=[
                    {'Key': 'session-id', 'Value': session_id},
                    {'Key': 'user', 'Value': user},
                    {'Key': 'stack', 'Value': stack_name}
                ]
            )
            user_target_group_arn = user_target_group_response['TargetGroups'][0]['TargetGroupArn']

            # Register the EC2 instance with the user-specific target group
            print(f"Registering instance {instance_id} with user target group {user_target_group_arn}")
            elbv2.register_targets(
                TargetGroupArn=user_target_group_arn,
                Targets=[
                    {
                        'Id': instance_id,
                        'Port': tutorial_port
                    }
                ]
            )

            # Create ALB listener rule for session-based routing
            print(f"Creating ALB listener rule for session path: /{session_id}/*")
            priority = hash(session_id) % 49000 + 1000

            elbv2.create_rule(
                ListenerArn=alb_listener_arn,
                Conditions=[
                    {
                        'Field': 'path-pattern',
                        'Values': [f'/{session_id}/*']
                    }
                ],
                Priority=priority,
                Actions=[
                    {
                        'Type': 'forward',
                        'TargetGroupArn': user_target_group_arn
                    }
                ],
                Tags=[
                    {'Key': 'session-id', 'Value': session_id},
                    {'Key': 'user', 'Value': user},
                    {'Key': 'stack', 'Value': stack_name}
                ]
            )

            print(f"Successfully created session-based routing for user {user}")

            # Generate HTTPS URL with session path
            tutorial_url = f"https://{tutorial_name}.{domain_name}/{session_id}/{query_string}"

        except Exception as e:
            print(f"Error with ALB session setup: {e}")
            # Fallback to direct HTTP URL if ALB fails
            tutorial_url = f"http://{public_ip}:{tutorial_port}{query_string}"

        # Send custom response if provided, otherwise default
        if custom_response_blocks:
            send_custom_response(response_url, custom_response_blocks, tutorial_url)
        else:
            send_response(response_url, f"Your container is ready at `{tutorial_url}`")

    except Exception as e:
        print("Error:", e)
        send_response(response_url, f"Error retrieving container IP: {e}")


def send_response(url, message):
    try:
        response = requests.post(url, json={
            "response_type": "ephemeral",
            "text": message
        })
        print(f"Slack response status: {response.status_code}")
    except Exception as e:
        print("Failed to post to Slack:", e)


def send_custom_response(url, blocks_json, tutorial_url):
    """Send a custom blocks response to Slack with variable substitution"""
    try:
        # Parse the blocks JSON and substitute variables
        blocks = json.loads(blocks_json)

        # Replace placeholders in the blocks
        blocks_str = json.dumps(blocks)
        blocks_str = blocks_str.replace("{{TUTORIAL_URL}}", tutorial_url)
        blocks = json.loads(blocks_str)

        response = requests.post(url, json={
            "response_type": "ephemeral",
            "blocks": blocks
        })
        print(f"Slack response status: {response.status_code}")
    except Exception as e:
        print("Failed to post custom response to Slack:", e)
        # Fallback to simple text response
        send_response(url, f"Your container is ready at `{tutorial_url}`")
