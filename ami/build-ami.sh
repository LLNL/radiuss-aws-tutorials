#!/bin/bash
set -e

if [ $# -ne 8 ]; then
    echo "Usage: $0 <base-ami-id> <ami-name> <subnet-id> <security-group-id> <key-pair-name> <docker-image> <docker-tag> <instance-type>"
    exit 1
fi

BASE_AMI_ID=$1
AMI_NAME=$2
SUBNET_ID=$3
SECURITY_GROUP_ID=$4
KEY_PAIR_NAME=$5
DOCKER_IMAGE=$6
DOCKER_TAG=$7
INSTANCE_TYPE=$8

# Generate unique pipeline name
PIPELINE_NAME="build-$(date +%s)-$(echo $AMI_NAME | tr '/' '-' | tr ':' '-')"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region || echo "us-east-1")

echo "Creating AMI: $AMI_NAME for Docker image: $DOCKER_IMAGE..."

# Create component
COMPONENT_ARN=$(aws imagebuilder create-component \
  --name "${PIPELINE_NAME}-component" \
  --semantic-version "1.0.0" \
  --platform "Linux" \
  --data "name: ${PIPELINE_NAME}-component
description: Pull Docker image ${DOCKER_IMAGE}
schemaVersion: 1.0
phases:
  - name: build
    steps:
      - name: PullDockerImage
        action: ExecuteBash
        inputs:
          commands:
            - sudo systemctl start docker
            - sudo systemctl enable docker
            - sudo docker pull ${DOCKER_IMAGE}
            - sudo docker tag ${DOCKER_IMAGE} ${DOCKER_TAG}" \
  --query 'componentBuildVersionArn' --output text)

# Create recipe
RECIPE_ARN=$(aws imagebuilder create-image-recipe \
  --name "${PIPELINE_NAME}-recipe" \
  --semantic-version "1.0.0" \
  --parent-image "$BASE_AMI_ID" \
  --components "[{\"componentArn\": \"$COMPONENT_ARN\"}]" \
  --query 'imageRecipeArn' --output text)

# Create distribution config to name the AMI properly
DIST_ARN=$(aws imagebuilder create-distribution-configuration \
  --name "${PIPELINE_NAME}-distribution" \
  --distributions "[{
    \"region\": \"$REGION\",
    \"amiDistributionConfiguration\": {
      \"name\": \"$AMI_NAME-{{ imagebuilder:buildDate }}\",
      \"description\": \"AMI with Docker image: $DOCKER_IMAGE\",
      \"amiTags\": {
        \"Name\": \"$AMI_NAME\",
        \"DockerImage\": \"$DOCKER_IMAGE\",
        \"BuildDate\": \"{{ imagebuilder:buildDate }}\",
        \"Environment\": \"tutorial\",
        \"CreatedBy\": \"imagebuilder-script\"
      }
    }
  }]" \
  --query 'distributionConfigurationArn' --output text)

# Create infrastructure config
INFRA_ARN=$(aws imagebuilder create-infrastructure-configuration \
  --name "${PIPELINE_NAME}-infra" \
  --instance-types "$INSTANCE_TYPE" \
  --instance-profile-name "EC2InstanceProfileForImageBuilder" \
  --subnet-id "$SUBNET_ID" \
  --security-group-ids "$SECURITY_GROUP_ID" \
  --key-pair "$KEY_PAIR_NAME" \
  --terminate-instance-on-failure \
  --query 'infrastructureConfigurationArn' --output text)

# Create pipeline with distribution config
PIPELINE_ARN=$(aws imagebuilder create-image-pipeline \
  --name "$PIPELINE_NAME" \
  --image-recipe-arn "$RECIPE_ARN" \
  --infrastructure-configuration-arn "$INFRA_ARN" \
  --distribution-configuration-arn "$DIST_ARN" \
  --image-tests-configuration '{"imageTestsEnabled": false}' \
  --query 'imagePipelineArn' --output text)

# Start build immediately
EXECUTION_ARN=$(aws imagebuilder start-image-pipeline-execution \
  --image-pipeline-arn "$PIPELINE_ARN" \
  --query 'imageBuildVersionArn' --output text)

echo "AMI build started: $EXECUTION_ARN"
echo "Final AMI will be named: $AMI_NAME-{{ imagebuilder:buildDate }}"
echo ""
echo "Waiting for build to complete..."

# Poll every minute until done
while true; do
    STATUS=$(aws imagebuilder get-image --image-build-version-arn "$EXECUTION_ARN" --query 'image.state.status' --output text 2>/dev/null)
    echo "$(date): Status = $STATUS"

    if [ "$STATUS" = "AVAILABLE" ]; then
        AMI_ID=$(aws imagebuilder get-image --image-build-version-arn "$EXECUTION_ARN" --query 'image.outputResources.amis[0].image' --output text)
        echo ""
        echo "Build completed successfully!"
        echo "New AMI ID: $AMI_ID"
        echo "AMI Name: $AMI_NAME"
        break

    elif [ "$STATUS" = "FAILED" ] || [ "$STATUS" = "CANCELLED" ]; then
        echo ""
        echo "Build failed with status: $STATUS"
        echo "Check the Image Builder console for details: https://console.aws.amazon.com/imagebuilder/"
        exit 1
    fi

    sleep 60
done

echo ""
echo "Cleaning up pipeline resources..."

aws imagebuilder delete-image-pipeline --image-pipeline-arn "$PIPELINE_ARN" >/dev/null 2>&1
aws imagebuilder delete-infrastructure-configuration --infrastructure-configuration-arn "$INFRA_ARN" >/dev/null 2>&1
aws imagebuilder delete-distribution-configuration --distribution-configuration-arn "$DIST_ARN" >/dev/null 2>&1
aws imagebuilder delete-image-recipe --image-recipe-arn "$RECIPE_ARN" >/dev/null 2>&1
aws imagebuilder delete-component --component-build-version-arn "$COMPONENT_ARN" >/dev/null 2>&1

echo "Cleanup completed!"

if [ "$STATUS" = "AVAILABLE" ]; then
    echo ""
    echo "Your AMI is ready: $AMI_ID"
else
    echo ""
    echo "Build failed - pipeline resources have been cleaned up."
fi

# Store AMI ID in Parameter Store for later retrieval
PARAM_NAME="/hpcic-tutorials/amis/$AMI_NAME"
echo "Storing AMI ID in Parameter Store: $PARAM_NAME"
aws ssm put-parameter \
  --name "$PARAM_NAME" \
  --value "$AMI_ID" \
  --type "String" \
  --description "AMI ID for $AMI_NAME tutorial" \
  --overwrite

aws ssm add-tags-to-resource \
  --resource-type "Parameter" \
  --resource-id "$PARAM_NAME" \
  --tags "Key=CreatedBy,Value=ami-build-script" "Key=AMIName,Value=$AMI_NAME" "Key=DockerImage,Value=$DOCKER_IMAGE"

echo "AMI ID stored in Parameter Store as $PARAM_NAME"
