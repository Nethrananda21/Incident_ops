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
