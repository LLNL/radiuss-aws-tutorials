import base64
import json
import time
import urllib.parse

import boto3
from botocore.exceptions import ClientError

cf = boto3.client("cloudformation")
ecs = boto3.client("ecs")
events = boto3.client("events")
secretsmanager = boto3.client("secretsmanager")


def get_secret_password(secret_name, password_key):
    """Get password from AWS Secrets Manager, return None if not found"""
    try:
        response = secretsmanager.get_secret_value(SecretId=secret_name)
        secret = json.loads(response["SecretString"])
        return secret.get(password_key)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        else:
            raise e


def get_cf_output(outputs, key):
    for output in outputs:
        if output["OutputKey"] == key:
            return output["OutputValue"]
    raise Exception(f"Output key {key} not found")


def lambda_handler(event, context):
    raw_body = event.get("body", "")
    if event.get("isBase64Encoded", False):
        raw_body = base64.b64decode(raw_body).decode("utf-8")

    print("RAW EVENT BODY:", raw_body)

    stack_name = event["pathParameters"]["stack_name"]
    print(f"stack_name = {stack_name}")
    if not stack_name:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Stack name is required"}),
        }

    params = urllib.parse.parse_qs(raw_body)
    response_url = params.get("response_url", [""])[0]
    user = params.get("user_name", ["unknown"])[0]
    provided_password = params.get("text", [""])[0].strip()

    try:
        # Look up stack outputs
        stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
        outputs = stack["Outputs"]

        # Check password if required
        password_secrets_name = get_cf_output(outputs, "PasswordSecretsName")
        password_secrets_key = get_cf_output(outputs, "PasswordSecretsKey")

        if password_secrets_name and password_secrets_key:
            required_password = get_secret_password(password_secrets_name, password_secrets_key)

            if required_password and provided_password != required_password:
                return {
                    "statusCode": 200,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"response_type": "ephemeral", "text": "Permission denied."}),
                }

        cluster = get_cf_output(outputs, "ClusterName")
        task_def = get_cf_output(outputs, "TaskDefinitionArn")
        capacity_provider = get_cf_output(outputs, "CapacityProviderName")

        # Check if user already has a running task
        running_tasks = ecs.list_tasks(cluster=cluster, desiredStatus="RUNNING")

        for task_arn in running_tasks["taskArns"]:
            tags = ecs.list_tags_for_resource(resourceArn=task_arn)
            user_tags = [tag["value"] for tag in tags["tags"] if tag["key"] == "slack-user"]
            if user in user_tags:
                return {
                    "statusCode": 200,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps(
                        {
                            "response_type": "ephemeral",
                            "text": (
                                f"You already have a running task for `{stack_name}`."
                                "Only one task per user is allowed."
                            ),
                        }
                    ),
                }

        # Launch ECS task using capacity provider
        run_response = ecs.run_task(
            cluster=cluster,
            taskDefinition=task_def,
            count=1,
            capacityProviderStrategy=[{"capacityProvider": capacity_provider, "weight": 1}],
            tags=[
                {"key": "task-id", "value": f"task-{int(time.time())}"},
                {"key": "slack-user", "value": user},
                {"key": "launch-type", "value": "slack"},
            ],
        )

        task_arn = run_response["tasks"][0]["taskArn"]
        tutorial_port = get_cf_output(outputs, "TutorialPort")
        tutorial_query_string = get_cf_output(outputs, "TutorialQueryString")
        custom_response_blocks = get_cf_output(outputs, "CustomResponseBlocks")

        # Emit event for async follow-up
        events.put_events(
            Entries=[
                {
                    "Source": "custom.slackbot",
                    "DetailType": "TaskStarted",
                    "Detail": json.dumps(
                        {
                            "task_arn": task_arn,
                            "cluster": cluster,
                            "stack": stack_name,
                            "response_url": response_url,
                            "user": user,
                            "port": tutorial_port,
                            "query_string": tutorial_query_string,
                            "custom_response_blocks": custom_response_blocks,
                        }
                    ),
                }
            ]
        )

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "response_type": "ephemeral",
                    "text": "Launching an AWS instance for you, you'll get a message when it's ready.",
                }
            ),
        }

    except Exception as e:
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"response_type": "ephemeral", "text": f"Failed to launch task: {e}"}),
        }
