#!/usr/bin/env bash
# Show what exists right now: stack outputs, knowledge base state, last ingestion.
set -euo pipefail

STACK=${STACK:-managed-kb-eval}
REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}

aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].{Status:StackStatus,Outputs:Outputs[].{Key:OutputKey,Value:OutputValue}}" \
  --output table
