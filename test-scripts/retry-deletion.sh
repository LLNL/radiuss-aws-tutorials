#!/bin/bash

set -e

echo "Post-hook: Checking for failed stack deletions..."

# TaskCat sets these environment variables
REGION="${AWS_DEFAULT_REGION:-us-east-1}"

# Find stacks that failed to delete
FAILED_STACKS=$(aws cloudformation list-stacks \
  --region $REGION \
  --stack-status-filter DELETE_FAILED \
  --query 'StackSummaries[?contains(StackName, `tCaT-radiuss-aws-tutorials`)].StackName' \
  --output text)

if [ -z "$FAILED_STACKS" ]; then
  echo "No failed stack deletions found. Exiting."
  exit 0
fi

echo "Found failed stacks: $FAILED_STACKS"

# Wait for AWS to catch up
echo "Waiting 120 seconds for AWS eventual consistency..."
sleep 120

# Retry deletion for each failed stack
for STACK in $FAILED_STACKS; do
  echo "Retrying deletion for stack: $STACK"

  # Try to delete the stack again
  aws cloudformation delete-stack \
    --stack-name "$STACK" \
    --region $REGION \
    || echo "Warning: Failed to initiate deletion for $STACK"

  echo "Waiting for stack deletion: $STACK"
  aws cloudformation wait stack-delete-complete \
    --stack-name "$STACK" \
    --region $REGION \
    --max-attempts 60 \
    && echo "Stack $STACK deleted successfully" \
    || echo "Stack $STACK deletion still failed (manual cleanup needed)"
done

echo "Post-hook completed."
