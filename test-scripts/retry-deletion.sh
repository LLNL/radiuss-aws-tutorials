#!/bin/bash

set -e

echo "Checking for taskcat stacks..."

REGION="${AWS_DEFAULT_REGION:-us-east-1}"

# Find all taskcat stacks for this project
FAILED_STACKS=$(aws cloudformation list-stacks \
  --region $REGION \
  --query 'StackSummaries[?contains(StackName, `tCaT-radiuss-aws-tutorials`)].StackName' \
  --output text)

if [ -z "$FAILED_STACKS" ]; then
  echo "No stacks found. Exiting."
  exit 0
fi

echo "Found stacks: $FAILED_STACKS"

# Wait for AWS to catch up
echo "Waiting 120 seconds for AWS eventual consistency..."
sleep 120

for STACK in $FAILED_STACKS; do
  echo "Deleting $STACK"

  aws cloudformation delete-stack \
    --stack-name "$STACK" \
    --region $REGION \
    || echo "Warning: Failed to initiate deletion for $STACK"

  echo "Waiting for stack deletion: $STACK"
  aws cloudformation wait stack-delete-complete \
    --stack-name "$STACK" \
    --region $REGION \
    && echo "Stack $STACK deleted successfully" \
    || echo "Stack $STACK deletion still failed (manual cleanup needed)"
done

echo "Deletions completed."
