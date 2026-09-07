#!/usr/bin/env bash
# Delete everything this demo created, so it stops costing anything.
set -euo pipefail

STACK=${STACK:-managed-kb-eval}
REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}
HERE="$(cd "$(dirname "$0")" && pwd)"

if ! aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" >/dev/null 2>&1; then
  echo "Stack '$STACK' does not exist in $REGION. Nothing to do."
  exit 0
fi

BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='CorpusBucketName'].OutputValue" --output text 2>/dev/null || true)

# CloudFormation refuses to delete a bucket that still has objects in it, and
# this template has no helper Lambda to empty it, so empty it from here.
if [[ -n "${BUCKET:-}" && "$BUCKET" != "None" ]]; then
  echo "Emptying s3://$BUCKET"
  aws s3 rm "s3://$BUCKET" --recursive --region "$REGION" >/dev/null || true
fi

echo "Deleting stack $STACK. This also deletes the knowledge base and its index."
aws cloudformation delete-stack --stack-name "$STACK" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name "$STACK" --region "$REGION"

rm -rf "$HERE/.venv-sdk"

echo "Done. Nothing left behind."
