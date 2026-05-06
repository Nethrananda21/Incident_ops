from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROUTE_FIELDS = {"ticket_id", "number", "short_description", "description", "urgency", "impact"}


def post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_tickets(path: Path, difficulty: str | None, limit: int | None) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tickets = payload["tickets"]
    if difficulty:
        tickets = [ticket for ticket in tickets if ticket["difficulty"] == difficulty]
    if limit is not None:
        tickets = tickets[:limit]
    return tickets


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_difficulty: dict[str, Counter[str]] = defaultdict(Counter)
    by_route = Counter()
    failures = []
    total_latency = 0
    for item in results:
        difficulty = item["difficulty"]
        actual = item.get("actual", {})
        expected_escalation = bool(item["expected_escalation_required"])
        actual_escalation = bool(actual.get("escalation_required"))
        correct = actual_escalation == expected_escalation
        by_difficulty[difficulty]["total"] += 1
        by_difficulty[difficulty]["correct"] += int(correct)
        by_difficulty[difficulty]["expected_escalate"] += int(expected_escalation)
        by_difficulty[difficulty]["actual_escalate"] += int(actual_escalation)
        by_route[str(actual.get("route_path", "error"))] += 1
        total_latency += int(actual.get("routing_latency_ms", 0) or 0)
        if not correct:
            failures.append(
                {
                    "ticket_id": item["ticket_id"],
                    "difficulty": difficulty,
                    "expected_escalation_required": expected_escalation,
                    "actual_escalation_required": actual_escalation,
                    "route_path": actual.get("route_path"),
                    "confidence_score": actual.get("confidence_score"),
                    "retrieval_similarity": actual.get("confidence_components", {}).get("retrieval_similarity"),
                    "verifier_score": actual.get("confidence_components", {}).get("verifier_score"),
                    "short_description": item["short_description"],
                }
            )

    total = len(results)
    correct = sum(counter["correct"] for counter in by_difficulty.values())
    return {
        "total": total,
        "correct": correct,
        "decision_accuracy": round(correct / total, 4) if total else 0,
        "average_latency_ms": round(total_latency / total, 1) if total else 0,
        "route_path_counts": dict(by_route),
        "by_difficulty": {
            difficulty: {
                "total": counter["total"],
                "correct": counter["correct"],
                "decision_accuracy": round(counter["correct"] / counter["total"], 4) if counter["total"] else 0,
                "expected_escalations": counter["expected_escalate"],
                "actual_escalations": counter["actual_escalate"],
            }
            for difficulty, counter in sorted(by_difficulty.items())
        },
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate escalation-vs-resolution accuracy for generated tickets.")
    parser.add_argument("--input", default="data/eval_ticket_set_100.json")
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--output", default="output/evaluation/eval_ticket_set_results.json")
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--pause-seconds", type=float, default=0.0)
    args = parser.parse_args()

    tickets = load_tickets(Path(args.input), args.difficulty, args.limit)
    results: list[dict[str, Any]] = []
    started = time.perf_counter()
    route_url = f"{args.api_base.rstrip('/')}/v1/tickets/route"

    for index, ticket in enumerate(tickets, start=1):
        payload = {key: ticket[key] for key in ROUTE_FIELDS if key in ticket}
        try:
            actual = post_json(route_url, payload, timeout=args.timeout)
            error = None
        except (HTTPError, URLError, TimeoutError) as exc:
            actual = {"route_path": "error", "escalation_required": True, "routing_latency_ms": 0}
            error = str(exc)
        result = {
            "ticket_id": ticket["ticket_id"],
            "difficulty": ticket["difficulty"],
            "short_description": ticket["short_description"],
            "expected_escalation_required": ticket["expected_escalation_required"],
            "expected_decision": ticket["expected_decision"],
            "actual": actual,
            "error": error,
        }
        results.append(result)
        expected = "ESC" if ticket["expected_escalation_required"] else "RES"
        actual_label = "ESC" if actual.get("escalation_required") else "RES"
        print(
            f"[{index:03d}/{len(tickets):03d}] {ticket['ticket_id']} {ticket['difficulty']} "
            f"expected={expected} actual={actual_label} route={actual.get('route_path')} "
            f"latency={actual.get('routing_latency_ms')}ms",
            flush=True,
        )
        if args.pause_seconds:
            time.sleep(args.pause_seconds)

    report = {
        "input": args.input,
        "api_base": args.api_base,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "summary": summarize(results),
        "results": results,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2), flush=True)
    print(f"wrote report -> {output}", flush=True)
    return 0 if report["summary"]["decision_accuracy"] >= 0.80 else 2


if __name__ == "__main__":
    raise SystemExit(main())
