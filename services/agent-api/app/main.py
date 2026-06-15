from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from functools import lru_cache
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from app.agent import RoutingAgent, sanitized_ticket_fields
from app.clickhouse_repo import ClickHouseRepository
from app.config import get_settings
from app.llm import NvidiaLLM
from app.schemas import ReviewDecision, RouteResponse, TicketRequest

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("incidentops.api")

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Privacy-first enterprise ticket routing and resolution API.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_patterns)

REQUEST_COUNT = 0
ERROR_COUNT = 0
REQUEST_LATENCY_TOTAL_SECONDS = 0.0
STARTED_AT = time.time()


@lru_cache
def get_repo() -> ClickHouseRepository:
    return ClickHouseRepository(get_settings())


@lru_cache
def get_agent() -> RoutingAgent:
    settings = get_settings()
    return RoutingAgent(settings=settings, repo=get_repo(), llm=NvidiaLLM(settings))


def add_operational_headers(response, request_id: str) -> None:
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"


def secure_json_response(payload: dict, *, status_code: int, request_id: str) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    add_operational_headers(response, request_id)
    return response


def safe_validation_errors(exc: RequestValidationError) -> list[dict]:
    errors = []
    for item in exc.errors():
        cleaned = dict(item)
        cleaned.pop("input", None)
        errors.append(cleaned)
    return errors


@app.middleware("http")
async def operational_middleware(request: Request, call_next):
    global REQUEST_COUNT, ERROR_COUNT, REQUEST_LATENCY_TOTAL_SECONDS
    request_id = request.headers.get("x-request-id") or str(uuid4())
    request.state.request_id = request_id

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            content_length_value = int(content_length)
        except ValueError:
            content_length_value = 0
    else:
        content_length_value = 0
    if content_length_value > settings.max_request_body_bytes:
        ERROR_COUNT += 1
        return secure_json_response(
            {"detail": "request body too large", "request_id": request_id},
            status_code=413,
            request_id=request_id,
        )

    REQUEST_COUNT += 1
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    REQUEST_LATENCY_TOTAL_SECONDS += elapsed
    if response.status_code >= 500:
        ERROR_COUNT += 1
    add_operational_headers(response, request_id)
    LOGGER.info(
        "request completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(elapsed * 1000, 2),
        },
    )
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    payload = {"detail": exc.detail, "request_id": request_id}
    return secure_json_response(payload, status_code=exc.status_code, request_id=request_id)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return secure_json_response(
        {"detail": "invalid request payload", "errors": safe_validation_errors(exc), "request_id": request_id},
        status_code=422,
        request_id=request_id,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    LOGGER.exception("unhandled API error", extra={"request_id": request_id})
    return secure_json_response(
        {"detail": "internal server error", "request_id": request_id},
        status_code=500,
        request_id=request_id,
    )


@app.get("/v1/health")
def health() -> dict:
    settings = get_settings()
    repo_ok = False
    try:
        repo_ok = get_repo().ping()
    except Exception:
        repo_ok = False
    uptime = int(time.time() - STARTED_AT)
    return {
        "status": "ok" if repo_ok else "degraded",
        "clickhouse": repo_ok,
        "nvidia_configured": bool(settings.nvidia_api_key),
        "model": settings.nvidia_llm_model,
        "uptime": uptime,
        "uptime_seconds": uptime,
    }


@app.get("/v1/live")
def live() -> dict:
    uptime = int(time.time() - STARTED_AT)
    return {"status": "ok", "uptime_seconds": uptime, "version": get_settings().app_version}


@app.get("/v1/ready")
def ready() -> JSONResponse:
    settings = get_settings()
    repo_ok = False
    try:
        repo_ok = get_repo().ping()
    except Exception:
        LOGGER.exception("readiness check failed")
    payload = {
        "status": "ready" if repo_ok else "not_ready",
        "clickhouse": repo_ok,
        "nvidia_configured": bool(settings.nvidia_api_key),
        "model": settings.nvidia_llm_model,
    }
    return JSONResponse(payload, status_code=200 if repo_ok else 503)


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    avg_latency = REQUEST_LATENCY_TOTAL_SECONDS / REQUEST_COUNT if REQUEST_COUNT else 0.0
    return (
        f"agent_api_requests_total {REQUEST_COUNT}\n"
        f"agent_api_errors_total {ERROR_COUNT}\n"
        f"agent_api_request_latency_seconds_sum {REQUEST_LATENCY_TOTAL_SECONDS:.6f}\n"
        f"agent_api_request_latency_seconds_avg {avg_latency:.6f}\n"
        f"agent_api_uptime_seconds {int(time.time() - STARTED_AT)}\n"
    )


@app.post("/v1/tickets/route", response_model=RouteResponse)
async def route_ticket(request: TicketRequest) -> dict:
    settings = get_settings()
    try:
        return await asyncio.wait_for(get_agent().route(request), timeout=settings.route_timeout_seconds)
    except asyncio.TimeoutError as exc:
        LOGGER.warning("ticket routing timed out")
        raise HTTPException(status_code=504, detail="ticket routing timed out") from exc
    except Exception as exc:
        LOGGER.exception("ticket routing failed")
        raise HTTPException(status_code=500, detail="ticket routing failed") from exc


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
    route_latency_rows = repo.client.query(
        """
        SELECT route_path, if(count() = 0, 0, avg(latency_ms)) AS avg_ms
        FROM routing_decisions
        GROUP BY route_path
        ORDER BY route_path ASC
        """
    ).result_rows
    component_row = repo.client.query(
        """
        SELECT
            if(count() = 0, 0, avg(classification_confidence)),
            if(count() = 0, 0, avg(retrieval_similarity)),
            if(count() = 0, 0, avg(verifier_score)),
            if(count() = 0, 0, avg(greatest(0, 1 - privacy_risk)))
        FROM routing_decisions
        """
    ).result_rows[0]
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
    route_distribution = {row[0]: int(row[1]) for row in route_path_rows}
    route_latency_ms = {row[0]: round(float(row[1]), 1) for row in route_latency_rows}
    category_distribution = [
        {
            "name": row[0],
            "count": int(row[1]),
            "percentage": round(safe_ratio(int(row[1]), ticket_total), 4),
        }
        for row in category_rows
    ]
    avg_confidence_components = {
        "classification_confidence": round(float(component_row[0]), 4),
        "retrieval_similarity": round(float(component_row[1]), 4),
        "verifier_score": round(float(component_row[2]), 4),
        "privacy_score": round(float(component_row[3]), 4),
    }
    return {
        "tickets_total": ticket_total,
        "total_tickets": ticket_total,
        "routing_decisions_total": decision_total,
        "privacy_findings_total": audit_total,
        "privacy_findings_count": audit_total,
        "categories": [{"name": row[0], "count": int(row[1])} for row in category_rows],
        "category_distribution": category_distribution,
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
        "avg_confidence": round(float(decision_row[1]), 4),
        "cache_hit_rate": safe_ratio(int(decision_row[5]), int(decision_row[0])),
        "escalation_count": int(decision_row[4]),
        "avg_latency_ms": round(float(decision_row[6]), 1),
        "route_distribution": {
            "semantic_cache": route_distribution.get("semantic_cache", 0),
            "generative_rag": route_distribution.get("generative_rag", 0),
            "out_of_distribution": route_distribution.get("out_of_distribution", 0),
            "human_review": route_distribution.get("human_review_required", 0),
            "human_review_required": route_distribution.get("human_review_required", 0),
        },
        "route_latency_ms": route_latency_ms,
        "avg_confidence_components": avg_confidence_components,
        "privacy_by_type": [{"entity_type": row[0], "count": int(row[1])} for row in privacy_rows],
        "top_entity_types": [row[0] for row in privacy_rows[:2]],
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
        WITH latest_tickets AS (
            SELECT
                ticket_id,
                argMax(number, created_at) AS number,
                argMax(short_description, created_at) AS short_description,
                argMax(category, created_at) AS category,
                argMax(assignment_group, created_at) AS assignment_group,
                argMax(urgency, created_at) AS urgency,
                argMax(impact, created_at) AS impact,
                argMax(sanitized_text, created_at) AS sanitized_text,
                argMax(resolution, created_at) AS resolution,
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
                argMax(sla_risk_score, created_at) AS sla_risk_score,
                argMax(sla_risk_level, created_at) AS sla_risk_level,
                max(created_at) AS latest_routed_at
            FROM routing_decisions
            GROUP BY ticket_id
        ),
        privacy_counts AS (
            SELECT ticket_id, count() AS redactions
            FROM privacy_audit
            GROUP BY ticket_id
        )
        SELECT
            t.ticket_id,
            t.short_description,
            t.category,
            t.assignment_group,
            t.urgency,
            t.impact,
            t.latest_created_at,
            t.sanitized_text,
            t.resolution,
            t.source,
            if(isNull(d.ticket_id), 'unrouted',
               if(d.escalation_required = 1, 'human_review_required',
                  if(d.semantic_cache_hit = 1, 'semantic_cache_resolved', 'resolved'))) AS status,
            ifNull(d.route_path, 'unrouted') AS route_path,
            ifNull(d.confidence_score, 0) AS confidence_score,
            ifNull(d.retrieval_similarity, 0) AS retrieval_similarity,
            ifNull(d.verifier_score, 0) AS verifier_score,
            ifNull(d.escalation_required, 0) AS escalation_required,
            ifNull(d.semantic_cache_hit, 0) AS semantic_cache_hit,
            ifNull(d.matched_ticket_id, '') AS matched_ticket_id,
            ifNull(d.latency_ms, 0) AS latency_ms,
            ifNull(d.sla_risk_score, 0) AS sla_risk_score,
            ifNull(d.sla_risk_level, '') AS sla_risk_level,
            ifNull(p.redactions, 0) AS redactions
        FROM latest_tickets AS t
        LEFT JOIN latest_decisions AS d ON t.ticket_id = d.ticket_id
        LEFT JOIN privacy_counts AS p ON t.ticket_id = p.ticket_id
        ORDER BY t.latest_created_at DESC, t.ticket_id DESC
        LIMIT %(limit)s
        """,
        parameters={"limit": limit},
    ).result_rows
    return {
        "tickets": [
            {
                **ticket_from_row(row),
                "sanitized_text": row[7],
                "resolution": row[8],
                "source": row[9],
                "status": row[10],
                "route_path": row[11],
                "confidence_score": float(row[12]),
                "retrieval_similarity": float(row[13]),
                "verifier_score": float(row[14]),
                "escalation_required": bool(row[15]),
                "semantic_cache_hit": bool(row[16]),
                "matched_ticket_id": row[17] or None,
                "latency_ms": int(row[18]),
                "sla_risk_score": float(row[19]),
                "sla_risk_level": row[20] or None,
                "redacted_pii_count": int(row[21]),
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


@app.get("/v1/intelligence/routing")
def routing_intelligence(limit: int = Query(default=500, ge=50, le=2000)) -> dict:
    repo = get_repo()
    settings = get_settings()
    rows = repo.client.query(
        """
        WITH latest_tickets AS (
            SELECT
                ticket_id,
                argMax(short_description, created_at) AS short_description,
                argMax(category, created_at) AS category,
                argMax(assignment_group, created_at) AS assignment_group,
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
                argMax(classification_confidence, created_at) AS classification_confidence,
                argMax(retrieval_similarity, created_at) AS retrieval_similarity,
                argMax(verifier_score, created_at) AS verifier_score,
                argMax(privacy_risk, created_at) AS privacy_risk,
                argMax(escalation_required, created_at) AS escalation_required,
                argMax(route_path, created_at) AS route_path,
                argMax(semantic_cache_hit, created_at) AS semantic_cache_hit,
                argMax(matched_ticket_id, created_at) AS matched_ticket_id,
                argMax(model_name, created_at) AS model_name,
                argMax(latency_ms, created_at) AS latency_ms,
                argMax(sla_risk_score, created_at) AS sla_risk_score,
                argMax(sla_risk_level, created_at) AS sla_risk_level,
                argMax(resolver_group, created_at) AS resolver_group,
                argMax(resolver_confidence, created_at) AS resolver_confidence,
                argMax(knowledge_gap, created_at) AS knowledge_gap,
                argMax(knowledge_gap_reason, created_at) AS knowledge_gap_reason,
                max(created_at) AS latest_decision_at
            FROM routing_decisions
            GROUP BY ticket_id
        )
        SELECT
            d.ticket_id,
            ifNull(t.short_description, '') AS short_description,
            ifNull(t.category, d.assigned_category) AS ticket_category,
            ifNull(t.assignment_group, '') AS assignment_group,
            ifNull(t.urgency, 3) AS urgency,
            ifNull(t.impact, 3) AS impact,
            ifNull(t.source, '') AS source,
            d.assigned_category,
            d.confidence_score,
            d.classification_confidence,
            d.retrieval_similarity,
            d.verifier_score,
            d.privacy_risk,
            d.escalation_required,
            d.route_path,
            d.semantic_cache_hit,
            d.matched_ticket_id,
            d.model_name,
            d.latency_ms,
            d.sla_risk_score,
            d.sla_risk_level,
            if(d.resolver_group != '', d.resolver_group, ifNull(m.assignment_group, ifNull(t.assignment_group, ''))) AS resolver_group,
            d.resolver_confidence,
            d.knowledge_gap,
            d.knowledge_gap_reason,
            d.latest_decision_at
        FROM latest_decisions AS d
        LEFT JOIN latest_tickets AS t ON d.ticket_id = t.ticket_id
        LEFT JOIN latest_tickets AS m ON d.matched_ticket_id = m.ticket_id
        ORDER BY d.latest_decision_at DESC, d.ticket_id DESC
        LIMIT %(limit)s
        """,
        parameters={"limit": limit},
    ).result_rows
    decisions = []
    for row in rows:
        risk_score = float(row[19])
        if risk_score <= 0:
            risk_score, risk_level_value = estimate_sla_risk(
                urgency=int(row[4]),
                impact=int(row[5]),
                confidence=float(row[8]),
                retrieval_similarity=float(row[10]),
                verifier_score=float(row[11]),
                privacy_risk=float(row[12]),
                escalation_required=bool(row[13]),
                rag_threshold=settings.rag_similarity_threshold,
            )
        else:
            risk_level_value = row[20] or risk_level(risk_score)
        knowledge_gap = bool(row[23]) or float(row[10]) < settings.rag_similarity_threshold or float(row[11]) < 0.70
        knowledge_reason = row[24] or (
            "stored RAG evidence or LLM decision confidence is below production threshold"
            if knowledge_gap
            else "approved context and LLM decision confidence are sufficient"
        )
        resolver_confidence = float(row[22])
        if resolver_confidence <= 0 and row[21] and row[21] != row[3]:
            resolver_confidence = max(0.0, min(1.0, float(row[10])))
        decisions.append(
            {
                "ticket_id": row[0],
                "short_description": row[1],
                "ticket_category": row[2],
                "assignment_group": row[3],
                "urgency": int(row[4]),
                "impact": int(row[5]),
                "source": row[6],
                "assigned_category": row[7],
                "confidence_score": float(row[8]),
                "classification_confidence": float(row[9]),
                "retrieval_similarity": float(row[10]),
                "verifier_score": float(row[11]),
                "privacy_risk": float(row[12]),
                "escalation_required": bool(row[13]),
                "route_path": row[14],
                "semantic_cache_hit": bool(row[15]),
                "matched_ticket_id": row[16] or None,
                "model_name": row[17],
                "latency_ms": int(row[18]),
                "sla_risk_score": risk_score,
                "sla_risk_level": risk_level_value,
                "resolver_group": row[21] or row[3] or "Unassigned",
                "resolver_confidence": resolver_confidence,
                "knowledge_gap": knowledge_gap,
                "knowledge_gap_reason": knowledge_reason,
                "created_at": str(row[25]),
            }
        )

    route_quality = aggregate_route_quality(decisions)
    sla_queue = sorted(decisions, key=lambda item: item["sla_risk_score"], reverse=True)[:15]
    knowledge_gaps = aggregate_knowledge_gaps(decisions)
    resolver_capacity = aggregate_resolver_capacity(decisions)
    feedback = review_feedback(repo)
    privacy_total = int(repo.client.command("SELECT count() FROM privacy_audit"))
    return {
        "summary": {
            "decisions_analyzed": len(decisions),
            "auto_resolution_rate": safe_ratio(
                sum(1 for item in decisions if not item["escalation_required"]),
                len(decisions),
            ),
            "semantic_cache_rate": safe_ratio(
                sum(1 for item in decisions if item["semantic_cache_hit"]),
                len(decisions),
            ),
            "human_review_rate": safe_ratio(
                sum(1 for item in decisions if item["escalation_required"]),
                len(decisions),
            ),
            "knowledge_gap_count": sum(1 for item in decisions if item["knowledge_gap"]),
            "critical_sla_count": sum(1 for item in decisions if item["sla_risk_level"] == "critical"),
            "avg_sla_risk": round(avg(decisions, "sla_risk_score"), 4),
            "avg_confidence": round(avg(decisions, "confidence_score"), 4),
        },
        "route_quality": route_quality,
        "sla_risk_queue": sla_queue,
        "knowledge_gaps": knowledge_gaps,
        "resolver_capacity": resolver_capacity,
        "feedback": feedback,
        "governance": {
            "model_name": settings.nvidia_llm_model,
            "privacy_findings": privacy_total,
            "thresholds": {
                "fast_path_similarity": settings.fast_path_similarity_threshold,
                "rag_similarity": settings.rag_similarity_threshold,
                "confidence": settings.routing_confidence_threshold,
                "llm_decision_confidence": 0.70,
            },
            "controls": [
                "privacy redaction before retrieval and model calls",
                "semantic cache bypass only for approved near-duplicate incidents at >=0.95",
                "LLM decision required for every non-cache ticket with scored RAG evidence attached",
                "immutable human-review feedback events",
            ],
        },
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
                argMax(sla_risk_score, created_at) AS sla_risk_score,
                argMax(sla_risk_level, created_at) AS sla_risk_level,
                argMax(resolver_group, created_at) AS resolver_group,
                argMax(resolver_confidence, created_at) AS resolver_confidence,
                argMax(knowledge_gap, created_at) AS knowledge_gap,
                argMax(knowledge_gap_reason, created_at) AS knowledge_gap_reason,
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
            ifNull(d.latency_ms, 0) AS latency_ms,
            ifNull(d.sla_risk_score, 0) AS sla_risk_score,
            ifNull(d.sla_risk_level, '') AS sla_risk_level,
            ifNull(d.resolver_group, '') AS resolver_group,
            ifNull(d.resolver_confidence, 0) AS resolver_confidence,
            ifNull(d.knowledge_gap, 0) AS knowledge_gap,
            ifNull(d.knowledge_gap_reason, '') AS knowledge_gap_reason
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
                "sla_risk_score": float(row[18]),
                "sla_risk_level": row[19] or None,
                "resolver_group": row[20] or None,
                "resolver_confidence": float(row[21]),
                "knowledge_gap": bool(row[22]),
                "knowledge_gap_reason": row[23] or None,
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
    display_short_description, display_description = sanitized_ticket_fields(
        str(ticket[2] or ""),
        str(ticket[3] or ""),
        str(ticket[4] or ""),
    )

    decision_result = repo.client.query(
        """
        SELECT ticket_id, assigned_category, suggested_resolution, confidence_score,
               classification_confidence, retrieval_similarity, verifier_score, privacy_risk,
               escalation_required, route_path, semantic_cache_hit, matched_ticket_id,
               model_name, latency_ms, sla_risk_score, sla_risk_level, resolver_group,
               resolver_confidence, knowledge_gap, knowledge_gap_reason, route_explanation,
               created_at
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

    routing_payload = None
    if decision:
        settings = get_settings()
        sla_score = float(decision[14])
        sla_level_value = decision[15] or risk_level(sla_score)
        if sla_score <= 0:
            sla_score, sla_level_value = estimate_sla_risk(
                urgency=int(ticket[8]),
                impact=int(ticket[9]),
                confidence=float(decision[3]),
                retrieval_similarity=float(decision[5]),
                verifier_score=float(decision[6]),
                privacy_risk=float(decision[7]),
                escalation_required=bool(decision[8]),
                rag_threshold=settings.rag_similarity_threshold,
            )
        knowledge_gap = (
            bool(decision[18])
            or float(decision[5]) < settings.rag_similarity_threshold
            or (decision[9] == "generative_rag" and float(decision[6]) < 0.70)
        )
        knowledge_reason = decision[19] or (
            "stored RAG evidence or LLM decision confidence is below production threshold"
            if knowledge_gap
            else "approved context and LLM decision confidence are sufficient"
        )
        resolver_group = decision[16] or (matched_ticket or {}).get("assignment_group") or ticket[6] or "Unassigned"
        resolver_confidence = float(decision[17]) if float(decision[17]) > 0 else (float(decision[5]) if matched_ticket else 0.0)
        route_explanation = parse_json_list(decision[20])
        if not route_explanation:
            route_explanation = build_route_explanation(
                redactions=len(audit_rows),
                matched_ticket_id=decision[11] or None,
                retrieval_similarity=float(decision[5]),
                route_path=decision[9],
                semantic_cache_hit=bool(decision[10]),
                escalation_required=bool(decision[8]),
                resolver_group=resolver_group,
                resolver_confidence=resolver_confidence,
                sla_risk_score=sla_score,
                sla_risk_level=sla_level_value,
                knowledge_gap=knowledge_gap,
                knowledge_reason=knowledge_reason,
            )
        routing_payload = {
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
            "sla_risk": {
                "score": sla_score,
                "level": sla_level_value or "normal",
            },
            "resolver_recommendation": {
                "group": resolver_group,
                "confidence": resolver_confidence,
                "source": "stored_decision" if decision[16] else "detail_fallback",
                "alternates": [],
            },
            "knowledge_gap": {
                "is_gap": knowledge_gap,
                "reason": knowledge_reason,
                "severity": risk_level(sla_score) if knowledge_gap else "normal",
            },
            "route_explanation": route_explanation,
            "created_at": str(decision[21]),
        }

    return {
        "ticket": {
            "ticket_id": ticket[0],
            "number": ticket[1],
            "short_description": display_short_description,
            "description": display_description,
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
        "routing": routing_payload,
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
    normalized = decision.decision.strip().lower().replace(" ", "_")
    if normalized not in {
        "accept",
        "accepted",
        "approve",
        "approved",
        "reject",
        "rejected",
        "edit",
        "edited",
        "corrected",
        "misrouted",
        "wrong_category",
        "wrong_resolution",
    }:
        raise HTTPException(status_code=400, detail="unsupported review decision")
    payload = decision.model_dump()
    payload["decision"] = normalized
    review_id = get_repo().insert_review_event(payload)
    return {
        "status": "recorded",
        "review_id": review_id,
        "ticket_id": decision.ticket_id,
        "decision": normalized,
        "reviewer": decision.reviewer,
    }


@app.get("/v1/review/events")
def review_events(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    return review_feedback(get_repo(), limit=limit)


def safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def avg(items: list[dict], key: str) -> float:
    if not items:
        return 0.0
    return sum(float(item.get(key, 0.0) or 0.0) for item in items) / len(items)


def risk_level(score: float) -> str:
    if score >= 0.75:
        return "critical"
    if score >= 0.55:
        return "elevated"
    if score >= 0.35:
        return "watch"
    return "normal"


def estimate_sla_risk(
    *,
    urgency: int,
    impact: int,
    confidence: float,
    retrieval_similarity: float,
    verifier_score: float,
    privacy_risk: float,
    escalation_required: bool,
    rag_threshold: float,
) -> tuple[float, str]:
    priority_risk = ((4 - urgency) + (4 - impact)) / 6
    uncertainty = 1.0 - max(0.0, min(1.0, confidence))
    retrieval_gap = max(0.0, rag_threshold - retrieval_similarity) / max(rag_threshold, 0.01)
    verifier_gap = max(0.0, 0.70 - verifier_score) / 0.70
    score = (
        0.45 * priority_risk
        + 0.25 * uncertainty
        + 0.15 * retrieval_gap
        + 0.10 * verifier_gap
        + 0.05 * privacy_risk
        + (0.15 if escalation_required else 0.0)
    )
    score = round(max(0.0, min(1.0, score)), 4)
    return score, risk_level(score)


def aggregate_route_quality(decisions: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in decisions:
        groups[item["route_path"]].append(item)
    rows = []
    for route_path, items in groups.items():
        rows.append(
            {
                "route_path": route_path,
                "count": len(items),
                "avg_confidence": round(avg(items, "confidence_score"), 4),
                "avg_retrieval_similarity": round(avg(items, "retrieval_similarity"), 4),
                "avg_verifier_score": round(avg(items, "verifier_score"), 4),
                "avg_latency_ms": round(avg(items, "latency_ms"), 1),
                "escalation_rate": safe_ratio(sum(1 for item in items if item["escalation_required"]), len(items)),
                "semantic_cache_rate": safe_ratio(sum(1 for item in items if item["semantic_cache_hit"]), len(items)),
            }
        )
    return sorted(rows, key=lambda item: item["count"], reverse=True)


def aggregate_knowledge_gaps(decisions: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in decisions:
        if item["knowledge_gap"]:
            groups[(item["assigned_category"], item["knowledge_gap_reason"])].append(item)
    clusters = []
    for (category, reason), items in groups.items():
        examples = sorted(items, key=lambda item: item["sla_risk_score"], reverse=True)[:3]
        max_risk = max((item["sla_risk_score"] for item in items), default=0.0)
        clusters.append(
            {
                "category": category,
                "reason": reason,
                "severity": risk_level(max_risk),
                "count": len(items),
                "avg_retrieval_similarity": round(avg(items, "retrieval_similarity"), 4),
                "avg_verifier_score": round(avg(items, "verifier_score"), 4),
                "recommended_action": "create or refresh a runbook for this category and review similar escalations",
                "examples": [
                    {
                        "ticket_id": item["ticket_id"],
                        "short_description": item["short_description"],
                        "sla_risk_score": item["sla_risk_score"],
                        "confidence_score": item["confidence_score"],
                    }
                    for item in examples
                ],
            }
        )
    return sorted(clusters, key=lambda item: (item["count"], item["severity"]), reverse=True)[:12]


def aggregate_resolver_capacity(decisions: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in decisions:
        groups[item["resolver_group"] or item["assignment_group"] or "Unassigned"].append(item)
    max_workload = max((len(items) for items in groups.values()), default=1)
    rows = []
    for group, items in groups.items():
        escalations = sum(1 for item in items if item["escalation_required"])
        critical = sum(1 for item in items if item["sla_risk_level"] == "critical")
        load_index = min(
            1.0,
            0.55 * (len(items) / max_workload)
            + 0.25 * safe_ratio(escalations, len(items))
            + 0.20 * (1.0 - avg(items, "confidence_score")),
        )
        if load_index >= 0.75:
            recommendation = "saturated"
        elif load_index >= 0.50:
            recommendation = "constrained"
        else:
            recommendation = "available"
        rows.append(
            {
                "group": group,
                "workload": len(items),
                "open_escalations": escalations,
                "critical_sla": critical,
                "avg_confidence": round(avg(items, "confidence_score"), 4),
                "load_index": round(load_index, 4),
                "recommendation": recommendation,
            }
        )
    return sorted(rows, key=lambda item: item["load_index"], reverse=True)


def review_feedback(repo: ClickHouseRepository, limit: int = 30) -> dict:
    counts = repo.client.query(
        """
        SELECT decision, count() AS c
        FROM review_events
        GROUP BY decision
        ORDER BY c DESC, decision ASC
        """
    ).result_rows
    recent = repo.client.query(
        """
        SELECT review_id, ticket_id, decision, reviewer, notes, corrected_category,
               corrected_assignment_group, corrected_resolution, created_at
        FROM review_events
        ORDER BY created_at DESC
        LIMIT %(limit)s
        """,
        parameters={"limit": limit},
    ).result_rows
    total = sum(int(row[1]) for row in counts)
    correction_total = sum(
        int(row[1])
        for row in counts
        if row[0] in {"corrected", "misrouted", "wrong_category", "wrong_resolution", "edit", "edited"}
    )
    return {
        "total_events": total,
        "correction_rate": safe_ratio(correction_total, total),
        "by_decision": [{"decision": row[0], "count": int(row[1])} for row in counts],
        "recent": [
            {
                "review_id": row[0],
                "ticket_id": row[1],
                "decision": row[2],
                "reviewer": row[3],
                "notes": row[4],
                "corrected_category": row[5] or None,
                "corrected_assignment_group": row[6] or None,
                "corrected_resolution": row[7] or None,
                "created_at": str(row[8]),
            }
            for row in recent
        ],
    }


def parse_json_list(value: str) -> list[dict]:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def build_route_explanation(
    *,
    redactions: int,
    matched_ticket_id: str | None,
    retrieval_similarity: float,
    route_path: str,
    semantic_cache_hit: bool,
    escalation_required: bool,
    resolver_group: str,
    resolver_confidence: float,
    sla_risk_score: float,
    sla_risk_level: str,
    knowledge_gap: bool,
    knowledge_reason: str,
) -> list[dict[str, str]]:
    if semantic_cache_hit:
        route_impact = "approved historical resolution returned without invoking the LLM"
    elif escalation_required:
        route_impact = "LLM decision requires human review before remediation"
    else:
        route_impact = "retrieved context was attached to the prompt and resolved by the LLM"
    return [
        {
            "label": "Privacy gate",
            "value": f"{redactions} redactions",
            "impact": "only sanitized text is available to retrieval and model calls",
        },
        {
            "label": "Nearest approved ticket",
            "value": f"{matched_ticket_id or 'none'} at {retrieval_similarity:.2f}",
            "impact": "similarity only controls the >=0.95 semantic cache bypass; otherwise the LLM decides",
        },
        {
            "label": "Route branch",
            "value": route_path,
            "impact": route_impact,
        },
        {
            "label": "Resolver recommendation",
            "value": f"{resolver_group} at {resolver_confidence:.2f}",
            "impact": "derived from stored decision metadata or matched-ticket fallback",
        },
        {
            "label": "SLA risk",
            "value": f"{sla_risk_level} at {sla_risk_score:.2f}",
            "impact": "priority, confidence, retrieval gap, LLM confidence gap, and privacy risk combined",
        },
        {
            "label": "Knowledge coverage",
            "value": "gap detected" if knowledge_gap else "covered",
            "impact": knowledge_reason,
        },
    ]


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
