import json
from datetime import datetime, timedelta, timezone

import boto3


def get_cf_output(stack_name, key):
    """Get CloudFormation output value by key"""
    cf = boto3.client("cloudformation")
    stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    outputs = stack["Outputs"]
    for output in outputs:
        if output["OutputKey"] == key:
            return output["OutputValue"]
    raise Exception(f"Output key {key} not found in stack {stack_name}")


def lambda_handler(event, context):
    ecs = boto3.client("ecs")
    ec2 = boto3.client("ec2")
    elbv2 = boto3.client("elbv2")

    cluster_name = event["cluster_name"]
    stack_name = event["stack_name"]
    timeout_hours = event.get("timeout_hours", 6)

    try:
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=timeout_hours)
        cutoff_iso = cutoff_time.isoformat()

        print(f"Cleaning up tasks older than {cutoff_iso} ({timeout_hours} hours)")

        tasks_response = ecs.list_tasks(cluster=cluster_name, desiredStatus="RUNNING")

        if not tasks_response["taskArns"]:
            print("No running tasks found")
            return {"cleaned_up": 0}

        tasks_details = ecs.describe_tasks(cluster=cluster_name, tasks=tasks_response["taskArns"])

        stopped_count = 0
        terminated_instances = []

        for task in tasks_details["tasks"]:
            task_arn = task["taskArn"]
            created_at = task["createdAt"]

            if created_at < cutoff_time:
                print(f"Stopping old task: {task_arn} (created: {created_at})")

                try:
                    instance_id = None
                    if "containerInstanceArn" in task:
                        container_instance_arn = task["containerInstanceArn"]
                        container_instances = ecs.describe_container_instances(
                            cluster=cluster_name, containerInstances=[container_instance_arn]
                        )
                        if container_instances["containerInstances"]:
                            instance_id = container_instances["containerInstances"][0]["ec2InstanceId"]

                    # Stop the task
                    ecs.stop_task(
                        cluster=cluster_name, task=task_arn, reason=f"Automatic cleanup after {timeout_hours} hours"
                    )

                    stopped_count += 1

                    # Clean up ALB session resources and terminate the specific instance that had this task
                    if instance_id:
                        # Try to clean up session-based ALB resources
                        if stack_name:
                            try:
                                # Get the public IP to generate session ID
                                instance_desc = ec2.describe_instances(InstanceIds=[instance_id])
                                public_ip = instance_desc["Reservations"][0]["Instances"][0]["PublicIpAddress"]

                                # Generate session ID from IP (same logic as notify function)
                                session_id = public_ip.replace(".", "-")

                                if session_id:

                                    print(f"Cleaning up session resources for: {session_id}")

                                    # Find and delete ALB listener rules for this session
                                    alb_listener_arn = get_cf_output(stack_name, "ALBHTTPSListenerArn")
                                    listener_rules = elbv2.describe_rules(ListenerArn=alb_listener_arn)

                                    for rule in listener_rules["Rules"]:
                                        # Check if this rule is for our session
                                        for condition in rule.get("Conditions", []):
                                            if condition.get("Field") == "host-header":
                                                for value in condition.get("Values", []):
                                                    if value.startswith(f"{session_id}."):
                                                        print(f"Deleting ALB listener rule for session {session_id}")
                                                        elbv2.delete_rule(RuleArn=rule["RuleArn"])
                                                        break

                                    # Find and delete target group for this session
                                    user_target_group_name = f"{stack_name}-{session_id}"[:32]
                                    try:
                                        target_groups = elbv2.describe_target_groups(Names=[user_target_group_name])
                                        for tg in target_groups["TargetGroups"]:
                                            print(f"Deleting target group: {user_target_group_name}")
                                            elbv2.delete_target_group(TargetGroupArn=tg["TargetGroupArn"])
                                    except elbv2.exceptions.TargetGroupNotFoundException:
                                        print(f"Target group {user_target_group_name} not found (already deleted?)")

                                    print(f"Successfully cleaned up ALB session resources for {session_id}")

                            except Exception as alb_error:
                                print(f"Error cleaning up ALB session resources: {alb_error}")

                        print(f"Terminating instance {instance_id} that had task {task_arn}")
                        ec2.terminate_instances(InstanceIds=[instance_id])
                        terminated_instances.append(instance_id)
                    else:
                        print(f"Could not find instance ID for task {task_arn}")

                except Exception as e:
                    print(f"Error stopping task {task_arn}: {e}")

        print(
            f"Task cleanup complete. Stopped {stopped_count} tasks, terminated {len(terminated_instances)} instances."
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "success": True,
                    "stopped_tasks": stopped_count,
                    "terminated_instances": terminated_instances,
                    "timeout_hours": timeout_hours,
                }
            ),
        }
    except Exception as e:
        print(f"Cleanup failed: {e}")
        return {"statusCode": 500, "body": json.dumps({"success": False, "error": str(e)})}
