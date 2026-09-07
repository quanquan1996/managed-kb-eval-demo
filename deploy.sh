#!/usr/bin/env bash
# Deploy the demo. Written to be pasted into AWS CloudShell.
# Safe to re-run: the stack is updated in place and the index is rebuilt each
# time, so this is also how you reload the corpus after changing it.
set -euo pipefail

STACK=${STACK:-managed-kb-eval}
REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}
EMBEDDING_MODEL_TYPE=${EMBEDDING_MODEL_TYPE:-MANAGED}
EMBEDDING_MODEL_ARN=${EMBEDDING_MODEL_ARN:-}
HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$HERE/cloudformation/managed-kb-eval.yaml"

echo "Stack:      $STACK"
echo "Region:     $REGION"
echo "Embeddings: $EMBEDDING_MODEL_TYPE"
echo

PARAMS="ParameterKey=EmbeddingModelType,ParameterValue=$EMBEDDING_MODEL_TYPE \
ParameterKey=EmbeddingModelArn,ParameterValue=$EMBEDDING_MODEL_ARN"

if aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" >/dev/null 2>&1; then
  echo "Stack exists. Applying any template changes."
  if ERR=$(aws cloudformation update-stack --stack-name "$STACK" --region "$REGION" \
      --template-body "file://$TEMPLATE" --parameters $PARAMS \
      --capabilities CAPABILITY_IAM 2>&1 >/dev/null); then
    aws cloudformation wait stack-update-complete --stack-name "$STACK" --region "$REGION" \
      || { echo "Update failed:" >&2; echo "$ERR" >&2; exit 1; }
  elif ! grep -q "No updates are to be performed" <<<"$ERR"; then
    echo "$ERR" >&2
    exit 1
  fi
else
  echo "Creating the bucket and the managed knowledge base. About two minutes."
  aws cloudformation create-stack --stack-name "$STACK" --region "$REGION" \
    --template-body "file://$TEMPLATE" --parameters $PARAMS \
    --capabilities CAPABILITY_IAM --query StackId --output text >/dev/null

  if ! aws cloudformation wait stack-create-complete --stack-name "$STACK" --region "$REGION"; then
    echo >&2
    echo "Deployment failed. Reason:" >&2
    aws cloudformation describe-stack-events --stack-name "$STACK" --region "$REGION" \
      --query "StackEvents[?ResourceStatus=='CREATE_FAILED'].[LogicalResourceId,ResourceStatusReason]" \
      --output text >&2
    exit 1
  fi
fi

BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='CorpusBucketName'].OutputValue" --output text)

echo
echo "Uploading the corpus to s3://$BUCKET/docs/"
aws s3 sync "$HERE/sample-data/docs/" "s3://$BUCKET/docs/" --region "$REGION" --delete

echo
"$HERE/scripts/run.sh" ingest.py

cat <<'EOF'

Index ready. Ask it something:

  ./ask.sh "华东一台 EM-300，2023 年 5 月出厂，反复上报 E207，要换表吗？需要报备吗？"

Then run the evaluation. It scores plain vector retrieval, managed reranking and
agentic multi-hop retrieval against the same eleven questions:

  ./eval.sh

When you are finished:

  ./cleanup.sh
EOF
