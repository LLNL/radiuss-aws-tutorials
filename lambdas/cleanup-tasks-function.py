import boto3
import json
from datetime import datetime, timezone, timedelta

def lambda_handler(event, context):
    ecs = boto3.client('ecs')
    ec2 = boto3.client('ec2')

    cluster_name = event['cluster_name']
    timeout_hours = event.get('timeout_hours', 6)

    try:
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=timeout_hours)
        cutoff_iso = cutoff_time.isoformat()

        print(f"Cleaning up tasks older than {cutoff_iso} ({timeout_hours} hours)")

        tasks_response = ecs.list_tasks(cluster=cluster_name, desiredStatus='RUNNING')

        if not tasks_response['taskArns']:
            print("No running tasks found")
            return {'cleaned_up': 0}

        tasks_details = ecs.describe_tasks(
            cluster=cluster_name,
            tasks=tasks_response['taskArns']
        )

        stopped_count = 0
        terminated_instances = []

        for task in tasks_details['tasks']:
            task_arn = task['taskArn']
            created_at = task['createdAt']

            if created_at < cutoff_time:
                print(f"Stopping old task: {task_arn} (created: {created_at})")

                try:
                    instance_id = None
                    if 'containerInstanceArn' in task:
                        container_instance_arn = task['containerInstanceArn']
                        container_instances = ecs.describe_container_instances(
                            cluster=cluster_name,
                            containerInstances=[container_instance_arn]
                        )
                        if container_instances['containerInstances']:
                            instance_id = container_instances['containerInstances'][0]['ec2InstanceId']

                    # Stop the task
                    ecs.stop_task(
                        cluster=cluster_name,
                        task=task_arn,
                        reason=f'Automatic cleanup after {timeout_hours} hours'
                    )

                    stopped_count += 1

                    # Terminate the specific instance that had this task
                    if instance_id:
                        print(f"Terminating instance {instance_id} that had task {task_arn}")
                        ec2.terminate_instances(InstanceIds=[instance_id])
                        terminated_instances.append(instance_id)
                    else:
                        print(f"Could not find instance ID for task {task_arn}")

                except Exception as e:
                    print(f"Error stopping task {task_arn}: {e}")

        print(f"Task cleanup complete. Stopped {stopped_count} tasks, terminated {len(terminated_instances)} instances.")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': True,
                'stopped_tasks': stopped_count,
                'terminated_instances': terminated_instances,
                'timeout_hours': timeout_hours
            })
        }
    except Exception as e:
        print(f"Cleanup failed: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'success': False,
                'error': str(e)
            })
        }
