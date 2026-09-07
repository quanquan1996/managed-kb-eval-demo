"""Ask one question and watch the managed retrieval pipeline work.

Prints the trace steps as they stream in, then the answer and its sources. Use
this to get a feel for the API before reading the evaluation numbers.
"""
import argparse

import common

common.ensure_sdk()

import boto3  # noqa: E402
import retrieval  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="the question, in any language the corpus uses")
    parser.add_argument(
        "--mode",
        default="agentic",
        choices=retrieval.MODES,
        help="agentic plans and answers; plain and rerank only return chunks",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--show-chunks", action="store_true", help="print the retrieved text too"
    )
    # ask.py defaults to a generous top_k because here the goal is a good answer,
    # not a discriminating measurement. eval.py deliberately uses a smaller one.
    args = parser.parse_args()

    outputs = common.stack_outputs()
    client = boto3.client("bedrock-agent-runtime", region_name=common.region())

    print(f"Mode: {retrieval.MODE_LABELS[args.mode]}")
    print(f"Q:    {args.question}")
    print()

    outcome = retrieval.run(
        client, args.mode, outputs["KnowledgeBaseId"], args.question, top_k=args.top_k
    )

    if outcome["steps"]:
        print("Pipeline steps reported by the service:")
        for step, status in outcome["steps"]:
            print(f"  {step:<24} {status}")
        print()

    if outcome["answer"]:
        print("Answer:")
        print(outcome["answer"])
        print()

    print(f"Sources ({len(outcome['results'])} chunks):")
    for name in sorted(outcome["docs"]):
        print(f"  {name}")

    if args.show_chunks:
        print()
        for index, item in enumerate(outcome["results"], 1):
            score = item.get("score")
            suffix = f"  score={score:.4f}" if score is not None else ""
            print(f"--- chunk {index}{suffix}")
            print(common.result_text(item)[:600])

    print()
    print(f"Elapsed: {outcome['seconds']:.1f}s")


if __name__ == "__main__":
    main()
