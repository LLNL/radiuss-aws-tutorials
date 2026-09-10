import hashlib
import json
from datetime import datetime, timedelta, timezone

import boto3


def get_cf_output(stack_name, key, default=None):
    """Get CloudFormation output value by key"""
    cf = boto3.client("cloudformation")
    stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    outputs = stack["Outputs"]
    for output in outputs:
        if output["OutputKey"] == key:
            return output["OutputValue"]
    if default is not None:
        return default
    raise Exception(f"Output key {key} not found in stack {stack_name}")


def delete_session_listener_rules(elbv2, listener_arn, session_id):
    target_group_arns = set()
    paginator = elbv2.get_paginator("describe_rules")
    for page in paginator.paginate(ListenerArn=listener_arn):
        for rule in page["Rules"]:
            for condition in rule.get("Conditions", []):
                if condition.get("Field") == "host-header" and any(
                    value.startswith(f"{session_id}.") for value in condition.get("Values", [])
                ):
                    print(f"Deleting ALB listener rule for session {session_id}")
                    target_group_arns.update(
                        action["TargetGroupArn"]
                        for action in rule.get("Actions", [])
                        if action.get("Type") == "forward" and action.get("TargetGroupArn")
                    )
                    elbv2.delete_rule(RuleArn=rule["RuleArn"])
                    break
    return target_group_arns


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

        task_arns = []
        paginator = ecs.get_paginator("list_tasks")
        for page in paginator.paginate(cluster=cluster_name, desiredStatus="RUNNING"):
            task_arns.extend(page["taskArns"])

        if not task_arns:
            print("No running tasks found")
            return {"cleaned_up": 0}

        stopped_count = 0
        terminated_instances = []

        tasks = []
        for offset in range(0, len(task_arns), 100):
            tasks.extend(ecs.describe_tasks(cluster=cluster_name, tasks=task_arns[offset: offset + 100])["tasks"])

        for task in tasks:
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

                    # Clean up ALB session resources before stopping the task so a
                    # failed cleanup remains visible to the next scheduled run.
                    if instance_id:
                        if stack_name:
                            instance_desc = ec2.describe_instances(InstanceIds=[instance_id])
                            public_ip = instance_desc["Reservations"][0]["Instances"][0]["PublicIpAddress"]
                            session_id = public_ip.replace(".", "-")

                            print(f"Cleaning up session resources for: {session_id}")

                            listener_arns = [get_cf_output(stack_name, "ALBHTTPSListenerArn")]
                            secondary_listener_arn = get_cf_output(stack_name, "SecondaryALBHTTPSListenerArn", "")
                            if secondary_listener_arn:
                                listener_arns.append(secondary_listener_arn)
                            target_group_arns = set()
                            target_group_digest = hashlib.sha256(
                                f"{stack_name}:{task_arn}".encode("utf-8")
                            ).hexdigest()[:12]
                            target_group_name = f"{stack_name[:19]}-{target_group_digest}"[:32]
                            try:
                                target_groups = elbv2.describe_target_groups(Names=[target_group_name])
                                target_group_arns.update(
                                    target_group["TargetGroupArn"] for target_group in target_groups["TargetGroups"]
                                )
                            except elbv2.exceptions.TargetGroupNotFoundException:
                                pass

                            for listener_arn in listener_arns:
                                target_group_arns.update(delete_session_listener_rules(elbv2, listener_arn, session_id))

                            for target_group_arn in target_group_arns:
                                try:
                                    print(f"Deleting target group: {target_group_arn}")
                                    elbv2.delete_target_group(TargetGroupArn=target_group_arn)
                                except elbv2.exceptions.TargetGroupNotFoundException:
                                    print(f"Target group {target_group_arn} already deleted")

                            print(f"Successfully cleaned up ALB session resources for {session_id}")

                    ecs.stop_task(
                        cluster=cluster_name, task=task_arn, reason=f"Automatic cleanup after {timeout_hours} hours"
                    )
                    stopped_count += 1

                    if instance_id:
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
