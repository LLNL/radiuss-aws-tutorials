# RADIUSS AWS Tutorials

This CloudFormation stack deploys containerized tutorials on EC2 using AWS ECS.
Tasks (docker containers) can be launched via CLI commands or Slack bot integration.

EC2 instances are launched automatically when tasks need them. You can choose to manually pre-warm instances to speed up launch time. Tasks and their instances are automatically terminated after a configurable timeout.

To create an AMI see `ami/README.md`.

Notes:
- You must have your AWS credentials configured in `~/.aws/credentials`
- You can set your region with `region = us-east-1` in `~/.aws/config`.
- The HTTPS set up with the ALB limits number of instances to 100.

# AWS CLI commands

## Create a stack
Parameters are configured in JSON files in the `parameters/` directory:
- `parameters/raja.json` - Raja tutorial configuration
- `parameters/mfem.json` - MFEM tutorial configuration
- `parameters/axom.json` - Axom tutorial configuration

Choose the tutorial you want to deploy:
``` bash
# Choose tutorial (raja, mfem, or axom)
TUTORIAL_NAME=raja
```

This creates or updates a cloudformation stack and waits until changes are complete:
```bash
# Get AMI ID from Parameter Store
export AMI_ID=$(aws ssm get-parameter --name "/hpcic-tutorials/amis/$TUTORIAL_NAME-tutorial" --query 'Parameter.Value' --output text)
envsubst < parameters/$TUTORIAL_NAME.json > /tmp/params.json

aws cloudformation deploy \
  --template-file dockerized-tutorial-template.yml \
  --parameter-overrides file:///tmp/params.json \
  --stack-name $TUTORIAL_NAME-tutorial \
  --capabilities CAPABILITY_NAMED_IAM
```

## Lambdas
Add lambdas to S3 bucket (`hpcic-tutorials-lambdas` in us-east-1).
``` bash
cd lambdas
./submit-lambdas.sh
```

If you update these lambdas be sure to update the `S3ObjectVersion` in cloud formation stack. You can retrieve this with e.g.:
``` bash
aws s3api list-object-versions \
    --bucket hpcic-tutorials-lambdas \
    --prefix slackbot-ec2/ \
    --query 'Versions[?IsLatest==`true`].[Key,VersionId]' \
    --output table
```

## Launch tasks from CLI
Launch tasks from CLI and wait for tutorial URLs to be returned:
``` bash
eval "$(aws cloudformation describe-stacks \
  --stack-name ${TUTORIAL_NAME}-tutorial \
  --query "Stacks[0].Outputs[?OutputKey=='LaunchTasksCommand'].OutputValue" \
  --output text)"

# Launch a single task
launch_tasks 1

# Launch multiple tasks
launch_tasks 3
```

## Get container URLs
To get URLs for all running tasks:
``` bash
eval "$(aws cloudformation describe-stacks \
  --stack-name ${TUTORIAL_NAME}-tutorial \
  --query "Stacks[0].Outputs[?OutputKey=='GetContainerUrlCommand'].OutputValue" \
  --output text)"
```

To get URLs for CLI launched tasks only:
``` bash
eval "$(aws cloudformation describe-stacks \
  --stack-name ${TUTORIAL_NAME}-tutorial \
  --query "Stacks[0].Outputs[?OutputKey=='GetCliTaskUrlCommand'].OutputValue" \
  --output text)"
```

## Cleanup tasks and rules
Delete all tasks:
``` bash
eval "$(aws cloudformation describe-stacks \
  --stack-name ${TUTORIAL_NAME}-tutorial \
  --query 'Stacks[0].Outputs[?OutputKey==`CleanupCommand`].OutputValue' \
  --output text)"
```
Delete all non-default ALB rules:
``` bash
# Get the ALB listener ARN
ALB_LISTENER_ARN=$(aws cloudformation describe-stacks \
  --stack-name "${TUTORIAL_NAME}-tutorial" \
  --query "Stacks[0].Outputs[?OutputKey=='ALBHTTPSListenerArn'].OutputValue" \
  --output text)

# Delete all non-default rules
aws elbv2 describe-rules --listener-arn $ALB_LISTENER_ARN \
  --query 'Rules[?IsDefault==`false`].RuleArn' \
  --output text | tr '\t' '\n' | while read -r rule_arn; do
    if [ -n "$rule_arn" ]; then
      echo "Deleting rule: $rule_arn"
      aws elbv2 delete-rule --rule-arn "$rule_arn"
    fi
  done

# Clean up all session-based target groups
STACK_PREFIX="${TUTORIAL_NAME}-tutorial"
aws elbv2 describe-target-groups \
  --query "TargetGroups[?starts_with(TargetGroupName, \`$STACK_PREFIX-\`)].TargetGroupArn" \
  --output text | tr '\t' '\n' | while read -r arn; do
    if [ -n "$arn" ]; then
      # Get the target group name for display
      name=$(echo "$arn" | sed 's/.*targetgroup\/\([^/]*\)\/.*/\1/')
      echo "Deleting target group: $name"
      aws elbv2 delete-target-group --target-group-arn "$arn"
    fi
  done
```

## Scale instances manually
``` bash
# Get Auto Scaling Group name
ASG_NAME=$(aws cloudformation describe-stack-resources \
  --stack-name ${TUTORIAL_NAME}-tutorial \
  --logical-resource-id AutoScalingGroup \
  --query 'StackResources[0].PhysicalResourceId' \
  --output text)

# Scale to N instances (e.g. 0 to shut down, 100 to pre-warm)
aws autoscaling set-desired-capacity \
  --auto-scaling-group-name $ASG_NAME \
  --desired-capacity 0

# Check current utilization
CLUSTER_NAME=$(aws cloudformation describe-stack-resources \
  --stack-name ${TUTORIAL_NAME}-tutorial \
  --logical-resource-id Cluster \
  --query 'StackResources[0].PhysicalResourceId' \
  --output text)
RUNNING_TASKS=$(aws ecs list-tasks --cluster $CLUSTER_NAME --desired-status RUNNING --query 'length(taskArns[])')
INSTANCES=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $ASG_NAME --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`]' --output json | jq length)
echo "Running tasks: $RUNNING_TASKS, Available instances: $INSTANCES"
```

## Delete stack
``` bash
aws cloudformation delete-stack --stack-name ${TUTORIAL_NAME}-tutorial
```

## Delete AMIs
See `ami/README.md` for instructions.

# Slackbot integration
Go to the [Slack API](https://api.slack.com/). Choose "Your apps" and create or choose existing app, then go to slash commands. You just need to make a command name, description, and set the request URL to:

``` bash
aws cloudformation describe-stacks \
  --stack-name ${TUTORIAL_NAME}-tutorial \
  --query "Stacks[0].Outputs[?OutputKey=='SlackCommandUrl'].OutputValue" \
  --output text
```

Note that this URL remains the same even when the stack is updated, only need to redo this step if you delete and re-create the stack.

## Adding password
``` bash
# Create secret with key-value pair
aws secretsmanager create-secret \
    --name "raja-tutorial-secret" \
    --description "Password for RAJA tutorial Slack access" \
    --secret-string '{"raja-tutorial-slack-password":"YOUR_PASSWORD_HERE"}'
```
Update the secret name and key in the parameters json file, e.g. `raja.json`.

# License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

All new contributions must be made under the MIT License.

See [LICENSE](LICENSE),
[COPYRIGHT](COPYRIGHT), and
[NOTICE](NOTICE) for details.

SPDX-License-Identifier: (MIT)

LLNL-CODE-793462
