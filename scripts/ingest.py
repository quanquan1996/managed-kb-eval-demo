"""Build the index, then wait until it actually answers queries.

CloudFormation creates the data source but never ingests it, so a freshly
deployed stack answers every question with "no results". This starts the
ingestion job and waits twice: first for the job to report COMPLETE, then for a
real Retrieve call to come back non-empty. The second wait matters, because for
a short window after COMPLETE the index returns an empty list rather than an
error.
"""
import sys
import time

import common

common.ensure_sdk()

import boto3  # noqa: E402  imported after the bootstrap decision

JOB_TIMEOUT = 900
SEARCH_TIMEOUT = 300
PROBE = "错误码 E207"


def main():
    outputs = common.stack_outputs()
    kb_id = outputs["KnowledgeBaseId"]
    ds_id = outputs["DataSourceId"]
    region = common.region()

    control = boto3.client("bedrock-agent", region_name=region)
    runtime = boto3.client("bedrock-agent-runtime", region_name=region)

    job = control.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
    job_id = job["ingestionJob"]["ingestionJobId"]
    print(f"Ingestion job {job_id} started. Indexing the corpus.")

    deadline = time.time() + JOB_TIMEOUT
    status = None
    while time.time() < deadline:
        described = control.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id
        )["ingestionJob"]
        status = described["status"]
        if status in ("COMPLETE", "FAILED", "STOPPED"):
            stats = described.get("statistics", {})
            print(f"  status {status}  {stats}")
            break
        print(f"  status {status}")
        time.sleep(15)
    else:
        sys.exit(f"Ingestion still {status} after {JOB_TIMEOUT}s. Check the console.")

    if status != "COMPLETE":
        reasons = described.get("failureReasons") or []
        sys.exit(f"Ingestion {status}. {' '.join(reasons)}")

    # COMPLETE is not the same as searchable. Poll the real thing.
    print("Waiting for the index to serve queries.")
    deadline = time.time() + SEARCH_TIMEOUT
    while time.time() < deadline:
        hits = runtime.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": PROBE},
            retrievalConfiguration={
                "managedSearchConfiguration": {
                    "numberOfResults": 3,
                    "rerankingModelType": "NONE",
                }
            },
        ).get("retrievalResults", [])
        if hits:
            print(f"  searchable: probe '{PROBE}' returned {len(hits)} chunks.")
            return
        print("  index returned 0 chunks, retrying")
        time.sleep(10)

    sys.exit(
        f"Index still returns nothing after {SEARCH_TIMEOUT}s, though the job "
        "reported COMPLETE. Re-run ./deploy.sh."
    )


if __name__ == "__main__":
    main()
