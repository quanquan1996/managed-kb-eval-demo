"""Score the three retrieval modes against the ground truth in questions.json.

Two things are measured separately, because they fail for different reasons:

  document recall  did retrieval surface every document the question needs?
  fact coverage    is each required fact present in the text that came back?

For the agentic mode fact coverage is scored twice, once against the retrieved
evidence and once against the answer the service generated. A gap between the
two is a generation problem, not a retrieval problem, and the distinction is
what tells you which knob to turn.
"""
import argparse
import json
import re
import statistics
import sys

import common

common.ensure_sdk()

import boto3  # noqa: E402
import retrieval  # noqa: E402

# Phrases a grounded answer uses when the corpus does not contain the answer.
ABSTAIN = (
    "未提及", "未找到", "没有找到", "没有提及", "未包含", "不包含", "未涉及",
    "无法确定", "无法回答", "无相关", "没有相关", "未提供", "没有提供",
    "知识库中没有", "文档中没有", "抱歉",
)


def normalise(text):
    return re.sub(r"\s+", "", text or "")


def facts_covered(text, expected_facts):
    """Fraction of AND-ed requirements satisfied. Each requirement is OR-ed."""
    if not expected_facts:
        return None, []
    haystack = normalise(text)
    missing = []
    hits = 0
    for alternatives in expected_facts:
        if any(normalise(a) in haystack for a in alternatives):
            hits += 1
        else:
            missing.append(alternatives[0])
    return hits / len(expected_facts), missing


def abstained(answer):
    return any(marker in (answer or "") for marker in ABSTAIN)


def score(question, outcome):
    expected = set(question["expected_docs"])
    row = {
        "id": question["id"],
        "type": question["type"],
        "mode": outcome["mode"],
        "seconds": outcome["seconds"],
        "chunks": len(outcome["results"]),
        "docs": sorted(outcome["docs"]),
        "steps": outcome["steps"],
    }

    if expected:
        found = expected & outcome["docs"]
        row["doc_recall"] = len(found) / len(expected)
        row["missing_docs"] = sorted(expected - found)
    else:
        row["doc_recall"] = None
        row["missing_docs"] = []

    row["evidence_coverage"], row["missing_facts"] = facts_covered(
        outcome["evidence"], question["expected_facts"]
    )

    if outcome["answer"]:
        row["answer_coverage"], row["answer_missing"] = facts_covered(
            outcome["answer"], question["expected_facts"]
        )
        row["answer"] = outcome["answer"]
        if question["type"] == "out_of_scope":
            row["abstained"] = abstained(outcome["answer"])
    else:
        row["answer_coverage"] = None
        row["answer_missing"] = []

    scores = [r.get("score") for r in outcome["results"] if r.get("score") is not None]
    row["top_score"] = max(scores) if scores else None
    return row


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def percent(value):
    return "  n/a" if value is None else f"{value * 100:5.1f}%"


def report(rows, questions):
    by_type = {q["id"]: q["type"] for q in questions}
    # Only report on modes that actually produced rows, so --modes and a failing
    # mode both degrade to a narrower table instead of a crash.
    modes = [m for m in retrieval.MODES if any(r["mode"] == m for r in rows)]
    if not modes:
        print("\nNo results to report.")
        return

    lookup = {(r["id"], r["mode"]): r for r in rows}

    def column(question, mode, field):
        row = lookup.get((question["id"], mode))
        return percent(row[field]) if row else "     -"

    print()
    print("=" * 78)
    print("Per-question document recall (share of required documents surfaced)")
    print("=" * 78)
    header = f"{'id':<4} {'type':<13}" + "".join(
        f"{retrieval.MODE_LABELS[m][:22]:>24}" for m in modes
    )
    print(header)
    for question in questions:
        if question["type"] == "out_of_scope":
            continue
        line = f"{question['id']:<4} {question['type']:<13}"
        for mode in modes:
            line += f"{column(question, mode, 'doc_recall'):>24}"
        print(line)

    print()
    print("=" * 78)
    print("Fact coverage in the retrieved evidence")
    print("=" * 78)
    print(header)
    for question in questions:
        if not question["expected_facts"]:
            continue
        line = f"{question['id']:<4} {question['type']:<13}"
        for mode in modes:
            line += f"{column(question, mode, 'evidence_coverage'):>24}"
        print(line)

    print()
    print("=" * 78)
    print("Aggregate")
    print("=" * 78)
    print(
        f"{'mode':<26}{'doc recall':>12}{'facts (evidence)':>19}"
        f"{'facts (answer)':>17}{'median s':>10}"
    )
    for mode in modes:
        subset = [r for r in rows if r["mode"] == mode]
        answerable = [r for r in subset if by_type[r["id"]] != "out_of_scope"]
        median = statistics.median(r["seconds"] for r in subset) if subset else 0.0
        print(
            f"{retrieval.MODE_LABELS[mode]:<26}"
            f"{percent(mean(r['doc_recall'] for r in answerable)):>12}"
            f"{percent(mean(r['evidence_coverage'] for r in answerable)):>19}"
            f"{percent(mean(r['answer_coverage'] for r in answerable)):>17}"
            f"{median:>10.1f}"
        )

    print()
    print("Split by question kind (document recall):")
    for kind in ("single", "multi"):
        if not any(by_type[r["id"]] == kind for r in rows):
            continue
        line = f"  {kind:<8}"
        for mode in modes:
            subset = [
                r for r in rows if r["mode"] == mode and by_type[r["id"]] == kind
            ]
            line += f"{retrieval.MODE_LABELS[mode][:18]}={percent(mean(r['doc_recall'] for r in subset))}  "
        print(line)

    oob = [r for r in rows if by_type[r["id"]] == "out_of_scope" and "abstained" in r]
    if oob:
        print()
        print("Out-of-scope questions, agentic mode (did it decline to invent?):")
        for row in oob:
            verdict = "declined" if row["abstained"] else "ANSWERED ANYWAY"
            print(f"  {row['id']}  {verdict}")

    traced = [r for r in rows if r["mode"] == "agentic" and r["steps"]]
    if traced:
        print()
        print("Agentic trace steps actually observed:")
        for row in traced:
            counts = {}
            for step, _ in row["steps"]:
                counts[step] = counts.get(step, 0) + 1
            summary = ", ".join(f"{k} x{v}" for k, v in counts.items())
            print(f"  {row['id']:<4} {summary}")

    print()
    print("Notable misses:")
    any_miss = False
    for row in rows:
        if row["missing_docs"] or row["missing_facts"]:
            any_miss = True
            bits = []
            if row["missing_docs"]:
                bits.append("docs " + ", ".join(row["missing_docs"]))
            if row["missing_facts"]:
                bits.append("facts " + ", ".join(row["missing_facts"]))
            print(f"  {row['id']:<4} {row['mode']:<8} missing {'; '.join(bits)}")
    if not any_miss:
        print("  none")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--modes",
        default=",".join(retrieval.MODES),
        help="comma separated subset of: " + ", ".join(retrieval.MODES),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help=(
            "chunks per retrieval. Keep it small: this corpus has only five "
            "documents, so at --top-k 10 every mode retrieves essentially the "
            "whole corpus and every mode scores 100%%, which measures nothing"
        ),
    )
    parser.add_argument("--only", help="comma separated question ids")
    parser.add_argument("--json", dest="json_out", help="also write raw rows here")
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = set(modes) - set(retrieval.MODES)
    if unknown:
        sys.exit(f"unknown mode(s): {', '.join(sorted(unknown))}")

    questions = common.load_questions()
    if args.only:
        wanted = {q.strip() for q in args.only.split(",")}
        questions = [q for q in questions if q["id"] in wanted]

    outputs = common.stack_outputs()
    kb_id = outputs["KnowledgeBaseId"]
    client = boto3.client("bedrock-agent-runtime", region_name=common.region())

    print(f"Knowledge base : {kb_id}")
    print(f"Region         : {common.region()}")
    print(f"Embedding model: {outputs.get('EmbeddingModelType', 'MANAGED')}")
    print(f"Questions      : {len(questions)}   top_k={args.top_k}")
    print()

    rows = []
    for question in questions:
        print(f"[{question['id']}] {question['question']}")
        for mode in modes:
            try:
                outcome = retrieval.run(
                    client, mode, kb_id, question["question"], top_k=args.top_k
                )
            except Exception as err:
                print(f"  {mode:<8} ERROR {err}")
                continue
            row = score(question, outcome)
            rows.append(row)
            recall = percent(row["doc_recall"])
            evidence = percent(row["evidence_coverage"])
            print(
                f"  {mode:<8} {row['seconds']:5.1f}s  {row['chunks']:>2} chunks  "
                f"docs={len(row['docs'])}  recall={recall}  facts={evidence}"
            )
        print()

    report(rows, questions)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
        print(f"\nRaw rows written to {args.json_out}")


if __name__ == "__main__":
    main()
