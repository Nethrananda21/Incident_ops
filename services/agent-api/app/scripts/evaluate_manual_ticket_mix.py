from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.clickhouse_repo import ClickHouseRepository
from app.config import get_settings


ROUTE_FIELDS = {"ticket_id", "number", "short_description", "description", "urgency", "impact"}
DATASET_SOURCE = "6StringNinja/synthetic-servicenow-incidents"
UNIQUE_BASE_IDS = [f"INC{index:05d}" for index in range(30)]
SIMILAR_BASE_IDS = ["INC00000", "INC00001", "INC00003", "INC00009", "INC00016"]

OOD_CASES = [
    (
        "MAN-OOD-001",
        "Quantum invoice hologram renderer fails in orbit",
        "The merchant reports that a quantum hologram invoice renderer on a satellite relay is producing invisible invoices during lunar settlement.",
    ),
    (
        "MAN-OOD-002",
        "Warehouse robot swarm changes shelf temperature policy",
        "Autonomous warehouse robots are negotiating shelf temperature policy through a robotics controller that is not part of merchant onboarding or IT support.",
    ),
    (
        "MAN-OOD-003",
        "Smart coffee machine requests blockchain notarization",
        "A break-room coffee machine is asking staff to notarize espresso batches on a blockchain network before brewing.",
    ),
    (
        "MAN-OOD-004",
        "AR headset renders fantasy pricing portal",
        "An augmented-reality headset renders a fictional pricing portal unrelated to the Sales UI, Customer UI, merchant agreement, or equipment configuration flow.",
    ),
    (
        "MAN-OOD-005",
        "Drone telemetry predicts cafeteria menu failure",
        "A drone telemetry dashboard is predicting cafeteria menu shortages and has no overlap with merchant onboarding systems or support history.",
    ),
]

POLICY_CASES = [
    (
        "MAN-POL-001",
        "Unauthorized bulk export of merchant onboarding records",
        "Audit logs show a bulk export of merchant onboarding records from an account that is not assigned to the region.",
    ),
    (
        "MAN-POL-002",
        "Merchant agreement exposes another merchant data",
        "A merchant reviewer reports seeing another merchant's agreement details while reviewing an onboarding case in Customer UI.",
    ),
    (
        "MAN-POL-003",
        "Production merchant database corruption warning",
        "The production onboarding database reports checksum corruption while Sales Agents are submitting new merchant records.",
    ),
    (
        "MAN-POL-004",
        "Privileged onboarding role granted without ticket",
        "A privileged onboarding administrator role was granted to an account with no matching access approval ticket.",
    ),
    (
        "MAN-POL-005",
        "Suspicious OAuth app has merchant record access",
        "A newly consented OAuth application has read access to merchant onboarding records and no registered owner.",
    ),
    (
        "MAN-POL-006",
        "Duplicate payment charges reported after terminal activation",
        "New merchants report duplicate payment charges after terminal activation even though onboarding shows one completed setup.",
    ),
    (
        "MAN-POL-007",
        "Production restore requested without approval",
        "A team requests production restore for onboarding data, but recovery point approval is missing.",
    ),
    (
        "MAN-POL-008",
        "Onboarding queue depth growing and workers crashing",
        "The production onboarding queue depth is growing quickly and processing workers are crashing with unknown errors.",
    ),
    (
        "MAN-POL-009",
        "Incorrect processing rates applied to live merchants",
        "The fee engine appears to apply incorrect processing rates to live merchants after a service bundle was changed.",
    ),
    (
        "MAN-POL-010",
        "Compliance review requested for merchant data exposure",
        "Compliance asks for human review because merchant onboarding evidence may contain regulated customer data exposure.",
    ),
]


@dataclass(frozen=True)
class BaseTicket:
    ticket_id: str
    short_description: str
    description: str


def post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_base_tickets() -> dict[str, BaseTicket]:
    settings = get_settings()
    repo = ClickHouseRepository(settings)
    ids = tuple(sorted(set(UNIQUE_BASE_IDS + SIMILAR_BASE_IDS)))
    rows = repo.client.query(
        """
        SELECT ticket_id, short_description, description
        FROM tickets
        WHERE source = %(source)s
          AND ticket_id IN %(ids)s
        ORDER BY ticket_id
        """,
        parameters={"source": DATASET_SOURCE, "ids": ids},
    ).result_rows
    tickets = {
        str(row[0]): BaseTicket(
            ticket_id=str(row[0]),
            short_description=str(row[1]),
            description=str(row[2]),
        )
        for row in rows
    }
    missing = sorted(set(ids) - set(tickets))
    if missing:
        raise RuntimeError(f"missing seeded dataset tickets: {', '.join(missing)}")
    return tickets


def build_unique_case(index: int, base: BaseTicket) -> dict[str, Any]:
    merchant = f"MRC-{7100 + index}"
    return {
        "ticket_id": f"MAN-UNI-{index:03d}",
        "number": f"MANUNI{index:04d}",
        "cohort": "unique_in_domain",
        "base_ticket_id": base.ticket_id,
        "short_description": f"Manual case {index}: {base.short_description} for new merchant record",
        "description": (
            f"{base.description} The issue was reproduced by support for merchant {merchant} after a browser refresh "
            "and retry. This is a new manually created evaluation ticket, not a reopened incident."
        ),
        "urgency": 3,
        "impact": 3,
        "expected_escalation_required": None,
        "expected_route_path": "generative_rag",
        "expected_semantic_cache_hit": False,
        "expected_decision": "llm_orchestrated_rag",
    }


def build_similar_case(index: int, base: BaseTicket) -> dict[str, Any]:
    return {
        "ticket_id": f"MAN-SIM-{index:03d}",
        "number": f"MANSIM{index:04d}",
        "cohort": "similar_cache",
        "base_ticket_id": base.ticket_id,
        "short_description": base.short_description,
        "description": base.description,
        "urgency": 3,
        "impact": 3,
        "expected_escalation_required": False,
        "expected_route_path": "semantic_cache",
        "expected_semantic_cache_hit": True,
        "expected_matched_ticket_id": base.ticket_id,
        "expected_decision": "cache_resolution",
    }


def build_manual_cases(output_path: Path | None = None) -> list[dict[str, Any]]:
    bases = load_base_tickets()
    tickets = [build_unique_case(index + 1, bases[ticket_id]) for index, ticket_id in enumerate(UNIQUE_BASE_IDS)]
    tickets.extend(build_similar_case(index + 1, bases[ticket_id]) for index, ticket_id in enumerate(SIMILAR_BASE_IDS))
    tickets.extend(
        {
            "ticket_id": ticket_id,
            "number": ticket_id.replace("-", ""),
            "cohort": "ood_human",
            "short_description": short_description,
            "description": description,
            "urgency": 3,
            "impact": 3,
            "expected_escalation_required": True,
            "expected_route_path": "human_review_required",
            "expected_semantic_cache_hit": False,
            "expected_decision": "human_review_llm_scope_decision",
        }
        for ticket_id, short_description, description in OOD_CASES
    )
    tickets.extend(
        {
            "ticket_id": ticket_id,
            "number": ticket_id.replace("-", ""),
            "cohort": "policy_human",
            "short_description": short_description,
            "description": description,
            "urgency": 2,
            "impact": 2,
            "expected_escalation_required": True,
            "expected_route_path": "human_review_required",
            "expected_semantic_cache_hit": False,
            "expected_decision": "human_review_policy",
        }
        for ticket_id, short_description, description in POLICY_CASES
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "metadata": {
                        "name": "IncidentOps 50-ticket manual routing benchmark",
                        "total_tickets": len(tickets),
                        "distribution": {
                            "unique_in_domain": 30,
                            "similar_cache": 5,
                            "ood_human": 5,
                            "policy_human": 10,
                        },
                        "source_dataset": DATASET_SOURCE,
                        "purpose": (
                            "Validate semantic-cache reuse, LLM-orchestrated RAG, human escalation, and ticket-detail "
                            "matched-ticket attachment."
                        ),
                    },
                    "tickets": tickets,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return tickets


def is_route_correct(ticket: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected = ticket.get("expected_route_path")
    if not expected:
        return True
    return actual.get("route_path") == expected


def is_semantic_cache_correct(ticket: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected = bool(ticket.get("expected_semantic_cache_hit", False))
    if bool(actual.get("semantic_cache_hit")) != expected:
        return False
    expected_match = ticket.get("expected_matched_ticket_id")
    if expected_match and actual.get("matched_ticket_id") != expected_match:
        return False
    return True


def detail_attachment_ok(actual: dict[str, Any], detail: dict[str, Any] | None) -> bool:
    matched_ticket_id = actual.get("matched_ticket_id")
    if not matched_ticket_id:
        return True
    if not detail:
        return False
    matched = detail.get("matched_ticket") or {}
    routing = detail.get("routing") or {}
    return matched.get("ticket_id") == matched_ticket_id and routing.get("matched_ticket_id") == matched_ticket_id


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    counters = Counter()
    by_cohort: dict[str, Counter[str]] = defaultdict(Counter)
    route_counts = Counter()
    failures = []
    latency_total = 0
    strict_decision_correct = 0
    for item in results:
        ticket = item["ticket"]
        actual = item.get("actual", {})
        detail = item.get("detail")
        cohort = ticket["cohort"]
        expected_escalation_value = ticket.get("expected_escalation_required")
        decision_required = expected_escalation_value is not None
        expected_escalation = bool(expected_escalation_value)
        actual_escalation = bool(actual.get("escalation_required"))
        decision_ok = True if not decision_required else actual_escalation == expected_escalation
        route_ok = is_route_correct(ticket, actual)
        cache_ok = is_semantic_cache_correct(ticket, actual)
        attachment_ok = detail_attachment_ok(actual, detail)
        case_ok = decision_ok and route_ok and cache_ok and attachment_ok
        strict_expected_escalation = cohort in {"ood_human", "policy_human"}
        strict_decision_correct += int(actual_escalation == strict_expected_escalation)
        route_counts[str(actual.get("route_path", "error"))] += 1
        latency_total += int(actual.get("routing_latency_ms", 0) or 0)
        for key, value in {
            "total": 1,
            "decision_correct": int(decision_ok),
            "decision_required_correct": int(decision_required and decision_ok),
            "route_correct": int(route_ok),
            "cache_correct": int(cache_ok),
            "attachment_correct": int(attachment_ok),
            "case_correct": int(case_ok),
            "decision_required": int(decision_required),
            "expected_escalations": int(expected_escalation),
            "actual_escalations": int(actual_escalation),
            "auto_resolutions": int(not actual_escalation),
            "strict_decision_correct": int(actual_escalation == strict_expected_escalation),
        }.items():
            counters[key] += value
            by_cohort[cohort][key] += value
        if not case_ok or item.get("error") or item.get("detail_error"):
            failures.append(
                {
                    "ticket_id": ticket["ticket_id"],
                    "cohort": cohort,
                    "expected_route_path": ticket.get("expected_route_path"),
                    "actual_route_path": actual.get("route_path"),
                    "expected_escalation_required": expected_escalation_value,
                    "actual_escalation_required": actual_escalation,
                    "expected_semantic_cache_hit": ticket.get("expected_semantic_cache_hit"),
                    "actual_semantic_cache_hit": actual.get("semantic_cache_hit"),
                    "expected_matched_ticket_id": ticket.get("expected_matched_ticket_id"),
                    "actual_matched_ticket_id": actual.get("matched_ticket_id"),
                    "confidence_score": actual.get("confidence_score"),
                    "retrieval_similarity": (actual.get("confidence_components") or {}).get("retrieval_similarity"),
                    "verifier_score": (actual.get("confidence_components") or {}).get("verifier_score"),
                    "detail_attachment_ok": attachment_ok,
                    "error": item.get("error"),
                    "detail_error": item.get("detail_error"),
                }
            )

    total = counters["total"]
    unlabeled_decisions = total - counters["decision_required"]
    return {
        "total": total,
        "scoring_notes": [
            "case_accuracy is structural: route branch, cache behavior, and matched-ticket attachment.",
            "unique_in_domain tickets are confidence-gated and do not have a mandatory auto-resolve label.",
            "strict_auto_resolution_accuracy treats unique_in_domain and similar_cache as should-resolve, and OOD/policy as should-escalate through the LLM human-review branch.",
        ],
        "case_accuracy": round(counters["case_correct"] / total, 4) if total else 0,
        "structural_case_accuracy": round(counters["case_correct"] / total, 4) if total else 0,
        "decision_accuracy": (
            round(counters["decision_required_correct"] / counters["decision_required"], 4)
            if counters["decision_required"]
            else None
        ),
        "decision_required_cases": counters["decision_required"],
        "decision_unlabeled_cases": unlabeled_decisions,
        "strict_auto_resolution_accuracy": round(strict_decision_correct / total, 4) if total else 0,
        "route_path_accuracy": round(counters["route_correct"] / total, 4) if total else 0,
        "semantic_cache_accuracy": round(counters["cache_correct"] / total, 4) if total else 0,
        "detail_attachment_accuracy": round(counters["attachment_correct"] / total, 4) if total else 0,
        "expected_escalations": counters["expected_escalations"],
        "actual_escalations": counters["actual_escalations"],
        "auto_resolution_rate": round(counters["auto_resolutions"] / total, 4) if total else 0,
        "average_latency_ms": round(latency_total / total, 1) if total else 0,
        "route_path_counts": dict(route_counts),
        "by_cohort": {
            cohort: {
                "total": values["total"],
                "case_accuracy": round(values["case_correct"] / values["total"], 4),
                "decision_accuracy": (
                    round(values["decision_required_correct"] / values["decision_required"], 4)
                    if values["decision_required"]
                    else None
                ),
                "decision_required_cases": values["decision_required"],
                "decision_unlabeled_cases": values["total"] - values["decision_required"],
                "strict_auto_resolution_accuracy": round(values["strict_decision_correct"] / values["total"], 4),
                "route_path_accuracy": round(values["route_correct"] / values["total"], 4),
                "semantic_cache_accuracy": round(values["cache_correct"] / values["total"], 4),
                "detail_attachment_accuracy": round(values["attachment_correct"] / values["total"], 4),
                "expected_escalations": values["expected_escalations"],
                "actual_escalations": values["actual_escalations"],
                "auto_resolution_rate": round(values["auto_resolutions"] / values["total"], 4),
            }
            for cohort, values in sorted(by_cohort.items())
        },
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the 50-ticket manual IncidentOps routing benchmark.")
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--dataset-output", default="data/manual_eval_ticket_set_50.json")
    parser.add_argument("--output", default="output/evaluation/manual_eval_ticket_set_50_results.json")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--pause-seconds", type=float, default=0.0)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    tickets = build_manual_cases(Path(args.dataset_output))
    if args.limit is not None:
        tickets = tickets[: args.limit]

    route_url = f"{args.api_base.rstrip()}/v1/tickets/route"
    detail_base = f"{args.api_base.rstrip()}/v1/tickets/detail"
    results: list[dict[str, Any]] = []
    started = time.perf_counter()

    for index, ticket in enumerate(tickets, start=1):
        payload = {key: ticket[key] for key in ROUTE_FIELDS if key in ticket}
        try:
            actual = post_json(route_url, payload, timeout=args.timeout)
            error = None
        except (HTTPError, URLError, TimeoutError) as exc:
            actual = {"route_path": "error", "escalation_required": True, "semantic_cache_hit": False}
            error = str(exc)
        try:
            detail = get_json(f"{detail_base}/{ticket['ticket_id']}", timeout=args.timeout)
            detail_error = None
        except (HTTPError, URLError, TimeoutError) as exc:
            detail = None
            detail_error = str(exc)
        results.append(
            {
                "ticket": ticket,
                "actual": actual,
                "detail": detail,
                "error": error,
                "detail_error": detail_error,
            }
        )
        expected_value = ticket.get("expected_escalation_required")
        expected = "ANY" if expected_value is None else "ESC" if expected_value else "RES"
        actual_label = "ESC" if actual.get("escalation_required") else "RES"
        print(
            f"[{index:02d}/{len(tickets):02d}] {ticket['ticket_id']} {ticket['cohort']} "
            f"expected={expected}/{ticket['expected_route_path']} "
            f"actual={actual_label}/{actual.get('route_path')} "
            f"cache={actual.get('semantic_cache_hit')} match={actual.get('matched_ticket_id')} "
            f"conf={actual.get('confidence_score')} sim={(actual.get('confidence_components') or {}).get('retrieval_similarity')} "
            f"latency={actual.get('routing_latency_ms')}ms",
            flush=True,
        )
        if args.pause_seconds:
            time.sleep(args.pause_seconds)

    report = {
        "api_base": args.api_base,
        "dataset_output": args.dataset_output,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "summary": summarize(results),
        "results": results,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2), flush=True)
    print(f"wrote dataset -> {args.dataset_output}", flush=True)
    print(f"wrote report -> {output}", flush=True)
    return 0 if report["summary"]["case_accuracy"] >= 0.90 else 2


if __name__ == "__main__":
    raise SystemExit(main())
