import boto3
import time
import json
import requests

ecs = boto3.client("ecs")
ec2 = boto3.client("ec2")

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

        tutorial_port = detail.get("port")
        query_string = detail.get("query_string", "")
        custom_response_blocks = detail.get("custom_response_blocks", "")

        # Send custom response if provided, otherwise default
        if custom_response_blocks:
            send_custom_response(response_url, custom_response_blocks, public_ip, tutorial_port, query_string)
        else:
            send_response(response_url, f"Your container is ready at `http://{public_ip}:{tutorial_port}{query_string}`")

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


def send_custom_response(url, blocks_json, public_ip, host_port, query_string):
    """Send a custom blocks response to Slack with variable substitution"""
    try:
        # Parse the blocks JSON and substitute variables
        blocks = json.loads(blocks_json)

        # Replace placeholders in the blocks
        blocks_str = json.dumps(blocks)
        blocks_str = blocks_str.replace("{{PUBLIC_IP}}", public_ip)
        blocks_str = blocks_str.replace("{{HOST_PORT}}", str(host_port))
        blocks_str = blocks_str.replace("{{QUERY_STRING}}", query_string)
        blocks = json.loads(blocks_str)

        response = requests.post(url, json={
            "response_type": "ephemeral",
            "blocks": blocks
        })
        print(f"Slack response status: {response.status_code}")
    except Exception as e:
        print("Failed to post custom response to Slack:", e)
        # Fallback to simple text response
        send_response(url, f"Your container is ready at `http://{public_ip}:{host_port}{query_string}`")
