"""The three retrieval modes under evaluation.

All three hit the same managed knowledge base. They differ only in how much of
the managed retrieval pipeline is switched on:

  plain    Retrieve with reranking off. One vector search, one round trip.
  rerank   Retrieve with the service-managed reranker on top of the same search.
  agentic  AgenticRetrieveStream: the service plans sub-queries, retrieves
           several times, expands promising documents and optionally writes the
           answer.
"""
import time

from common import doc_names, result_text

MODES = ("plain", "rerank", "agentic")

MODE_LABELS = {
    "plain": "Retrieve, rerank NONE",
    "rerank": "Retrieve, rerank MANAGED",
    "agentic": "AgenticRetrieveStream",
}


def _retrieve(client, kb_id, question, top_k, reranking):
    """A MANAGED knowledge base takes managedSearchConfiguration.

    Passing vectorSearchConfiguration here is the single easiest mistake to
    make, because every pre-managed example on the internet uses it.
    """
    response = client.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": question},
        retrievalConfiguration={
            "managedSearchConfiguration": {
                "numberOfResults": top_k,
                "rerankingModelType": reranking,
            }
        },
    )
    return response.get("retrievalResults", [])


def _agentic(client, kb_id, question, top_k, generate, max_iterations):
    """Consume the event stream, keeping the trace steps for reporting."""
    response = client.agentic_retrieve_stream(
        messages=[{"role": "user", "content": {"text": question}}],
        retrievers=[
            {
                "configuration": {
                    "knowledgeBase": {
                        "knowledgeBaseId": kb_id,
                        "retrievalOverrides": {"maxNumberOfResults": top_k},
                    }
                }
            }
        ],
        agenticRetrieveConfiguration={
            "foundationModelType": "MANAGED",
            "rerankingModelType": "MANAGED",
            "maxAgentIteration": max_iterations,
        },
        generateResponse=generate,
    )

    results, steps, answer = [], [], ""
    for event in response["stream"]:
        if "traceEvent" in event:
            attributes = event["traceEvent"].get("attributes", {})
            step = attributes.get("step")
            if step:
                steps.append((step, attributes.get("status", "")))
        elif "result" in event:
            payload = event["result"]
            results = payload.get("results", [])
            answer = (payload.get("generatedResponse") or {}).get("answer", "")
        else:
            # Modelled exceptions arrive as stream members rather than as raised
            # errors, so an unhandled one would otherwise pass silently.
            for key, value in event.items():
                if key.endswith("Exception"):
                    raise RuntimeError(f"{key}: {value.get('message')}")
    return results, steps, answer


def run(client, mode, kb_id, question, top_k=10, generate=True, max_iterations=5):
    """Execute one question in one mode. Returns a uniform dict."""
    started = time.perf_counter()
    steps, answer = [], ""

    if mode == "plain":
        results = _retrieve(client, kb_id, question, top_k, "NONE")
    elif mode == "rerank":
        results = _retrieve(client, kb_id, question, top_k, "MANAGED")
    elif mode == "agentic":
        results, steps, answer = _agentic(
            client, kb_id, question, top_k, generate, max_iterations
        )
    else:
        raise ValueError(f"unknown mode {mode}")

    elapsed = time.perf_counter() - started
    docs = set()
    for item in results:
        docs |= doc_names(item)

    return {
        "mode": mode,
        "seconds": elapsed,
        "results": results,
        "docs": docs,
        "evidence": "\n".join(result_text(r) for r in results),
        "answer": answer,
        "steps": steps,
    }
