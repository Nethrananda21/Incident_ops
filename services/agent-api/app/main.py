from __future__ import annotations

import asyncio
import json
import time
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.agent import RoutingAgent
from app.clickhouse_repo import ClickHouseRepository
from app.config import get_settings
from app.llm import NvidiaLLM
from app.schemas import ReviewDecision, RouteResponse, TicketRequest

app = FastAPI(
    title="Agentic Routing & Privacy Pipeline",
    version="0.1.0",
    description="Privacy-first enterprise ticket routing and resolution API.",
)

REQUEST_COUNT = 0
ERROR_COUNT = 0
STARTED_AT = time.time()


def get_repo() -> ClickHouseRepository:
    return ClickHouseRepository(get_settings())


@lru_cache
def get_agent() -> RoutingAgent:
    settings = get_settings()
    return RoutingAgent(settings=settings, repo=get_repo(), llm=NvidiaLLM(settings))


@app.get("/v1/health")
def health() -> dict:
    settings = get_settings()
    repo_ok = False
    try:
        repo_ok = get_repo().ping()
    except Exception:
        repo_ok = False
    return {
        "status": "ok" if repo_ok else "degraded",
        "clickhouse": repo_ok,
        "nvidia_configured": bool(settings.nvidia_api_key),
        "model": settings.nvidia_llm_model,
        "uptime_seconds": int(time.time() - STARTED_AT),
    }


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    return (
        f"agent_api_requests_total {REQUEST_COUNT}\n"
        f"agent_api_errors_total {ERROR_COUNT}\n"
        f"agent_api_uptime_seconds {int(time.time() - STARTED_AT)}\n"
    )


@app.post("/v1/tickets/route", response_model=RouteResponse)
async def route_ticket(request: TicketRequest) -> dict:
    global REQUEST_COUNT, ERROR_COUNT
    REQUEST_COUNT += 1
    try:
        return await get_agent().route(request)
    except Exception as exc:
        ERROR_COUNT += 1
        raise HTTPException(status_code=500, detail=f"routing failed: {exc}") from exc


@app.get("/v1/dashboard")
def dashboard() -> dict:
    repo = get_repo()
    settings = get_settings()
    ticket_total = int(repo.client.command("SELECT count() FROM tickets"))
    decision_total = int(repo.client.command("SELECT count() FROM routing_decisions"))
    audit_total = int(repo.client.command("SELECT count() FROM privacy_audit"))
    category_rows = repo.client.query(
        """
        SELECT category, count() AS c
        FROM tickets
        GROUP BY category
        ORDER BY c DESC, category ASC
        """
    ).result_rows
    group_rows = repo.client.query(
        """
        SELECT assignment_group, count() AS c
        FROM tickets
        GROUP BY assignment_group
        ORDER BY c DESC, assignment_group ASC
        LIMIT 8
        """
    ).result_rows
    decision_row = repo.client.query(
        """
        SELECT
            count(),
            if(count() = 0, 0, avg(confidence_score)),
            if(count() = 0, 0, avg(retrieval_similarity)),
            if(count() = 0, 0, avg(verifier_score)),
            sum(escalation_required),
            sum(semantic_cache_hit),
            if(count() = 0, 0, avg(latency_ms))
        FROM routing_decisions
        """
    ).result_rows[0]
    route_path_rows = repo.client.query(
        """
        SELECT route_path, count() AS c
        FROM routing_decisions
        GROUP BY route_path
        ORDER BY c DESC, route_path ASC
        """
    ).result_rows
    recent_rows = repo.client.query(
        """
        SELECT ticket_id, short_description, category, assignment_group, urgency, impact, created_at
        FROM tickets
        ORDER BY created_at DESC, ticket_id DESC
        LIMIT 8
        """
    ).result_rows
    privacy_rows = repo.client.query(
        """
        SELECT entity_type, count() AS c
        FROM privacy_audit
        GROUP BY entity_type
        ORDER BY c DESC, entity_type ASC
        """
    ).result_rows
    return {
        "tickets_total": ticket_total,
        "routing_decisions_total": decision_total,
        "privacy_findings_total": audit_total,
        "categories": [{"name": row[0], "count": int(row[1])} for row in category_rows],
        "assignment_groups": [{"name": row[0], "count": int(row[1])} for row in group_rows],
        "routing": {
            "decisions": int(decision_row[0]),
            "avg_confidence": round(float(decision_row[1]), 4),
            "avg_retrieval_similarity": round(float(decision_row[2]), 4),
            "avg_verifier_score": round(float(decision_row[3]), 4),
            "escalations": int(decision_row[4]),
            "semantic_cache_hits": int(decision_row[5]),
            "avg_latency_ms": round(float(decision_row[6]), 1),
            "route_paths": [{"name": row[0], "count": int(row[1])} for row in route_path_rows],
        },
        "privacy_by_type": [{"entity_type": row[0], "count": int(row[1])} for row in privacy_rows],
        "recent_tickets": [ticket_from_row(row) for row in recent_rows],
        "thresholds": {
            "fast_path_similarity": settings.fast_path_similarity_threshold,
            "rag_similarity": settings.rag_similarity_threshold,
            "confidence": settings.routing_confidence_threshold,
        },
    }


@app.get("/v1/tickets/recent")
def recent_tickets(limit: int = Query(default=30, ge=1, le=200)) -> dict:
    rows = get_repo().client.query(
        """
        SELECT ticket_id, short_description, category, assignment_group, urgency, impact, created_at,
               sanitized_text, resolution, source
        FROM tickets
        ORDER BY created_at DESC, ticket_id DESC
        LIMIT %(limit)s
        """,
        parameters={"limit": limit},
    ).result_rows
    return {
        "tickets": [
            ticket_from_row(row)
            | {
                "sanitized_text": row[7],
                "resolution": row[8],
                "source": row[9],
            }
            for row in rows
        ]
    }


@app.get("/v1/tickets/stream")
async def stream_tickets(
    interval_ms: int = Query(default=1500, ge=400, le=10000),
    window: int = Query(default=200, ge=1, le=1000),
) -> StreamingResponse:
    async def events():
        index = 0
        while True:
            rows = get_repo().client.query(
                """
                SELECT ticket_id, short_description, category, assignment_group, urgency, impact, created_at,
                       sanitized_text, source
                FROM tickets
                ORDER BY created_at DESC, ticket_id DESC
                LIMIT %(window)s
                """,
                parameters={"window": window},
            ).result_rows
            if rows:
                row = rows[index % len(rows)]
                payload = ticket_from_row(row) | {
                    "sanitized_text": row[7],
                    "source": row[8],
                    "stream_sequence": index + 1,
                    "streamed_at": int(time.time()),
                }
                yield f"event: ticket\ndata: {json.dumps(payload)}\n\n"
                index += 1
            else:
                yield "event: heartbeat\ndata: {}\n\n"
            await asyncio.sleep(interval_ms / 1000)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/v1/escalations")
def escalations(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    rows = get_repo().client.query(
        """
        SELECT ticket_id, assigned_category, confidence_score, classification_confidence,
               retrieval_similarity, verifier_score, privacy_risk, suggested_resolution,
               latency_ms, route_path, semantic_cache_hit, matched_ticket_id, created_at
        FROM routing_decisions
        WHERE escalation_required = 1
        ORDER BY created_at DESC
        LIMIT %(limit)s
        """,
        parameters={"limit": limit},
    ).result_rows
    return {
        "escalations": [
            {
                "ticket_id": row[0],
                "assigned_category": row[1],
                "confidence_score": float(row[2]),
                "classification_confidence": float(row[3]),
                "retrieval_similarity": float(row[4]),
                "verifier_score": float(row[5]),
                "privacy_risk": float(row[6]),
                "suggested_resolution": row[7],
                "latency_ms": int(row[8]),
                "route_path": row[9],
                "semantic_cache_hit": bool(row[10]),
                "matched_ticket_id": row[11] or None,
                "created_at": str(row[12]),
            }
            for row in rows
        ]
    }


@app.get("/v1/privacy/audit/recent")
def recent_privacy_audit(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    rows = get_repo().client.query(
        """
        SELECT audit_id, stream_id, ticket_id, entity_type, placeholder, confidence,
               policy_version, detector_version, created_at
        FROM privacy_audit
        ORDER BY created_at DESC
        LIMIT %(limit)s
        """,
        parameters={"limit": limit},
    ).result_rows
    return {
        "findings": [
            {
                "audit_id": row[0],
                "stream_id": row[1],
                "ticket_id": row[2],
                "entity_type": row[3],
                "placeholder": row[4],
                "confidence": float(row[5]),
                "policy_version": row[6],
                "detector_version": row[7],
                "created_at": str(row[8]),
            }
            for row in rows
        ]
    }


@app.get("/v1/knowledge")
def knowledge(limit: int = Query(default=80, ge=1, le=300)) -> dict:
    rows = get_repo().client.query(
        """
        SELECT ticket_id, short_description, category, assignment_group, sanitized_text,
               resolution, source, created_at
        FROM tickets
        ORDER BY created_at DESC, ticket_id DESC
        LIMIT %(limit)s
        """,
        parameters={"limit": limit},
    ).result_rows
    return {
        "documents": [
            {
                "ticket_id": row[0],
                "short_description": row[1],
                "category": row[2],
                "assignment_group": row[3],
                "sanitized_text": row[4],
                "resolution": row[5],
                "source": row[6],
                "created_at": str(row[7]),
            }
            for row in rows
        ]
    }


@app.get("/v1/evaluation")
def evaluation() -> dict:
    repo = get_repo()
    tickets = int(repo.client.command("SELECT count() FROM tickets"))
    labeled_categories = int(repo.client.command("SELECT uniqExact(category) FROM tickets"))
    decision_row = repo.client.query(
        """
        SELECT
            count(),
            if(count() = 0, 0, avg(confidence_score)),
            if(count() = 0, 0, avg(retrieval_similarity)),
            if(count() = 0, 0, avg(verifier_score)),
            if(count() = 0, 0, sum(escalation_required) / count()),
            if(count() = 0, 0, sum(semantic_cache_hit) / count()),
            if(count() = 0, 0, avg(latency_ms))
        FROM routing_decisions
        """
    ).result_rows[0]
    privacy_total = int(repo.client.command("SELECT count() FROM privacy_audit"))
    return {
        "dataset_rows": tickets,
        "labeled_categories": labeled_categories,
        "routed_tickets": int(decision_row[0]),
        "avg_confidence": round(float(decision_row[1]), 4),
        "avg_retrieval_similarity": round(float(decision_row[2]), 4),
        "avg_verifier_score": round(float(decision_row[3]), 4),
        "escalation_rate": round(float(decision_row[4]), 4),
        "semantic_cache_hit_rate": round(float(decision_row[5]), 4),
        "avg_latency_ms": round(float(decision_row[6]), 1),
        "privacy_findings": privacy_total,
    }


@app.get("/v1/tickets/search")
def search_tickets(
    q: str = "",
    category: str = "",
    assignment_group: str = "",
    source: str = "",
    route_path: str = "",
    status: str = "",
    urgency: int | None = Query(default=None, ge=1, le=3),
    impact: int | None = Query(default=None, ge=1, le=3),
    limit: int = Query(default=80, ge=1, le=300),
) -> dict:
    repo = get_repo()
    status_expr = (
        "if(isNull(d.ticket_id), 'unrouted', "
        "if(d.escalation_required = 1, 'human_review_required', "
        "if(d.semantic_cache_hit = 1, 'semantic_cache_resolved', 'resolved')))"
    )
    clauses = ["1 = 1"]
    params: dict[str, object] = {"limit": limit}
    if q.strip():
        clauses.append(
            """
            (
                positionCaseInsensitive(t.ticket_id, %(q)s) > 0
                OR positionCaseInsensitive(t.number, %(q)s) > 0
                OR positionCaseInsensitive(t.short_description, %(q)s) > 0
                OR positionCaseInsensitive(t.sanitized_text, %(q)s) > 0
            )
            """
        )
        params["q"] = q.strip()
    if category:
        clauses.append("t.category = %(category)s")
        params["category"] = category
    if assignment_group:
        clauses.append("t.assignment_group = %(assignment_group)s")
        params["assignment_group"] = assignment_group
    if source:
        clauses.append("t.source = %(source)s")
        params["source"] = source
    if route_path:
        clauses.append("ifNull(d.route_path, 'unrouted') = %(route_path)s")
        params["route_path"] = route_path
    if status:
        clauses.append(f"{status_expr} = %(status)s")
        params["status"] = status
    if urgency is not None:
        clauses.append("t.urgency = %(urgency)s")
        params["urgency"] = urgency
    if impact is not None:
        clauses.append("t.impact = %(impact)s")
        params["impact"] = impact

    rows = repo.client.query(
        f"""
        WITH latest_tickets AS (
            SELECT
                ticket_id,
                argMax(number, created_at) AS number,
                argMax(short_description, created_at) AS short_description,
                argMax(description, created_at) AS description,
                argMax(sanitized_text, created_at) AS sanitized_text,
                argMax(category, created_at) AS category,
                argMax(assignment_group, created_at) AS assignment_group,
                argMax(resolution, created_at) AS resolution,
                argMax(urgency, created_at) AS urgency,
                argMax(impact, created_at) AS impact,
                argMax(source, created_at) AS source,
                max(created_at) AS latest_created_at
            FROM tickets
            GROUP BY ticket_id
        ),
        latest_decisions AS (
            SELECT
                ticket_id,
                argMax(assigned_category, created_at) AS assigned_category,
                argMax(confidence_score, created_at) AS confidence_score,
                argMax(retrieval_similarity, created_at) AS retrieval_similarity,
                argMax(verifier_score, created_at) AS verifier_score,
                argMax(escalation_required, created_at) AS escalation_required,
                argMax(route_path, created_at) AS route_path,
                argMax(semantic_cache_hit, created_at) AS semantic_cache_hit,
                argMax(matched_ticket_id, created_at) AS matched_ticket_id,
                argMax(latency_ms, created_at) AS latency_ms,
                max(created_at) AS latest_routed_at
            FROM routing_decisions
            GROUP BY ticket_id
        )
        SELECT
            t.ticket_id,
            t.number,
            t.short_description,
            t.category,
            t.assignment_group,
            t.urgency,
            t.impact,
            t.source,
            t.latest_created_at,
            {status_expr} AS status,
            ifNull(d.route_path, 'unrouted') AS route_path,
            ifNull(d.confidence_score, 0) AS confidence_score,
            ifNull(d.retrieval_similarity, 0) AS retrieval_similarity,
            ifNull(d.verifier_score, 0) AS verifier_score,
            ifNull(d.escalation_required, 0) AS escalation_required,
            ifNull(d.semantic_cache_hit, 0) AS semantic_cache_hit,
            ifNull(d.matched_ticket_id, '') AS matched_ticket_id,
            ifNull(d.latency_ms, 0) AS latency_ms
        FROM latest_tickets AS t
        LEFT JOIN latest_decisions AS d ON t.ticket_id = d.ticket_id
        WHERE {' AND '.join(clauses)}
        ORDER BY t.latest_created_at DESC, t.ticket_id DESC
        LIMIT %(limit)s
        """,
        parameters=params,
    ).result_rows

    facets = {
        "categories": facet(repo, "category"),
        "assignment_groups": facet(repo, "assignment_group"),
        "sources": facet(repo, "source"),
        "route_paths": [
            row[0]
            for row in repo.client.query(
                "SELECT route_path FROM routing_decisions GROUP BY route_path ORDER BY route_path ASC"
            ).result_rows
        ],
        "statuses": ["unrouted", "resolved", "semantic_cache_resolved", "human_review_required"],
    }
    return {
        "filters": {
            "q": q,
            "category": category,
            "assignment_group": assignment_group,
            "source": source,
            "route_path": route_path,
            "status": status,
            "urgency": urgency,
            "impact": impact,
            "limit": limit,
        },
        "count": len(rows),
        "facets": facets,
        "tickets": [
            {
                "ticket_id": row[0],
                "number": row[1],
                "short_description": row[2],
                "category": row[3],
                "assignment_group": row[4],
                "urgency": int(row[5]),
                "impact": int(row[6]),
                "source": row[7],
                "created_at": str(row[8]),
                "status": row[9],
                "route_path": row[10],
                "confidence_score": float(row[11]),
                "retrieval_similarity": float(row[12]),
                "verifier_score": float(row[13]),
                "escalation_required": bool(row[14]),
                "semantic_cache_hit": bool(row[15]),
                "matched_ticket_id": row[16] or None,
                "latency_ms": int(row[17]),
            }
            for row in rows
        ],
    }


@app.get("/v1/tickets/detail/{ticket_id}")
def ticket_detail(ticket_id: str) -> dict:
    repo = get_repo()
    ticket_result = repo.client.query(
        """
        SELECT ticket_id, number, short_description, description, sanitized_text, category,
               assignment_group, resolution, urgency, impact, source, created_at
        FROM tickets
        WHERE ticket_id = %(ticket_id)s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        parameters={"ticket_id": ticket_id},
    )
    if not ticket_result.result_rows:
        raise HTTPException(status_code=404, detail="ticket not found")
    ticket = ticket_result.result_rows[0]

    decision_result = repo.client.query(
        """
        SELECT ticket_id, assigned_category, suggested_resolution, confidence_score,
               classification_confidence, retrieval_similarity, verifier_score, privacy_risk,
               escalation_required, route_path, semantic_cache_hit, matched_ticket_id,
               model_name, latency_ms, created_at
        FROM routing_decisions
        WHERE ticket_id = %(ticket_id)s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        parameters={"ticket_id": ticket_id},
    )
    decision = decision_result.result_rows[0] if decision_result.result_rows else None

    matched_ticket = None
    if decision and decision[11]:
        matched_result = repo.client.query(
            """
            SELECT ticket_id, short_description, category, assignment_group, resolution, source, created_at
            FROM tickets
            WHERE ticket_id = %(ticket_id)s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            parameters={"ticket_id": decision[11]},
        )
        if matched_result.result_rows:
            row = matched_result.result_rows[0]
            matched_ticket = {
                "ticket_id": row[0],
                "short_description": row[1],
                "category": row[2],
                "assignment_group": row[3],
                "resolution": row[4],
                "source": row[5],
                "created_at": str(row[6]),
            }

    audit_rows = repo.client.query(
        """
        SELECT audit_id, stream_id, entity_type, placeholder, confidence,
               policy_version, detector_version, created_at
        FROM privacy_audit
        WHERE ticket_id = %(ticket_id)s
        ORDER BY created_at DESC
        LIMIT 20
        """,
        parameters={"ticket_id": ticket_id},
    ).result_rows

    status = "unrouted"
    if decision:
        if bool(decision[8]):
            status = "human_review_required"
        elif bool(decision[10]):
            status = "semantic_cache_resolved"
        else:
            status = "resolved"

    return {
        "ticket": {
            "ticket_id": ticket[0],
            "number": ticket[1],
            "short_description": ticket[2],
            "description": ticket[3],
            "sanitized_text": ticket[4],
            "category": ticket[5],
            "assignment_group": ticket[6],
            "stored_resolution": ticket[7],
            "urgency": int(ticket[8]),
            "impact": int(ticket[9]),
            "source": ticket[10],
            "created_at": str(ticket[11]),
        },
        "status": status,
        "routing": None if not decision else {
            "ticket_id": decision[0],
            "assigned_category": decision[1],
            "suggested_resolution": split_resolution(decision[2]),
            "confidence_score": float(decision[3]),
            "confidence_components": {
                "classification": float(decision[4]),
                "retrieval_similarity": float(decision[5]),
                "verifier_score": float(decision[6]),
                "privacy_risk": float(decision[7]),
            },
            "escalation_required": bool(decision[8]),
            "route_path": decision[9],
            "semantic_cache_hit": bool(decision[10]),
            "matched_ticket_id": decision[11] or None,
            "model_name": decision[12],
            "latency_ms": int(decision[13]),
            "created_at": str(decision[14]),
        },
        "matched_ticket": matched_ticket,
        "privacy_audit": [
            {
                "audit_id": row[0],
                "stream_id": row[1],
                "entity_type": row[2],
                "placeholder": row[3],
                "confidence": float(row[4]),
                "policy_version": row[5],
                "detector_version": row[6],
                "created_at": str(row[7]),
            }
            for row in audit_rows
        ],
    }


@app.get("/v1/tickets/status/{ticket_id}")
def ticket_status(ticket_id: str) -> dict:
    result = get_repo().client.query(
        """
        SELECT ticket_id, assigned_category, confidence_score, escalation_required,
               suggested_resolution, route_path, semantic_cache_hit, matched_ticket_id, created_at
        FROM routing_decisions
        WHERE ticket_id = %(ticket_id)s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        parameters={"ticket_id": ticket_id},
    )
    if not result.result_rows:
        raise HTTPException(status_code=404, detail="ticket not found")
    row = result.result_rows[0]
    return {
        "ticket_id": row[0],
        "assigned_category": row[1],
        "confidence_score": row[2],
        "escalation_required": bool(row[3]),
        "suggested_resolution": row[4],
        "route_path": row[5],
        "semantic_cache_hit": bool(row[6]),
        "matched_ticket_id": row[7] or None,
        "created_at": str(row[8]),
    }


@app.get("/v1/privacy/audit/{stream_id}")
def privacy_audit(stream_id: str) -> dict:
    result = get_repo().client.query(
        """
        SELECT audit_id, entity_type, placeholder, confidence, policy_version, detector_version, created_at
        FROM privacy_audit
        WHERE stream_id = %(stream_id)s
        ORDER BY created_at ASC
        """,
        parameters={"stream_id": stream_id},
    )
    return {
        "stream_id": stream_id,
        "findings": [
            {
                "audit_id": row[0],
                "entity_type": row[1],
                "placeholder": row[2],
                "confidence": row[3],
                "policy_version": row[4],
                "detector_version": row[5],
                "created_at": str(row[6]),
            }
            for row in result.result_rows
        ],
    }


@app.post("/v1/review/escalations")
def review_escalation(decision: ReviewDecision) -> dict:
    return {
        "status": "accepted",
        "ticket_id": decision.ticket_id,
        "decision": decision.decision,
        "reviewer": decision.reviewer,
    }


def ticket_from_row(row) -> dict:
    return {
        "ticket_id": row[0],
        "short_description": row[1],
        "category": row[2],
        "assignment_group": row[3],
        "urgency": int(row[4]),
        "impact": int(row[5]),
        "created_at": str(row[6]),
    }


def split_resolution(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def facet(repo: ClickHouseRepository, column: str) -> list[str]:
    allowed = {"category", "assignment_group", "source"}
    if column not in allowed:
        return []
    return [
        row[0]
        for row in repo.client.query(
            f"SELECT {column} FROM tickets GROUP BY {column} ORDER BY {column} ASC LIMIT 100"
        ).result_rows
        if row[0]
    ]
