from __future__ import annotations

import time
from collections import Counter
from typing import TypedDict

import httpx
from langgraph.graph import END, StateGraph

from app.clickhouse_repo import ClickHouseRepository, RetrievedTicket, TicketRecord
from app.config import Settings
from app.embeddings import embed_text
from app.llm import NvidiaLLM
from app.privacy import redact_text
from app.schemas import TicketRequest


class AgentState(TypedDict, total=False):
    request: TicketRequest
    ticket_id: str
    raw_text: str
    sanitized_text: str
    redacted_count: int
    privacy_audit: dict
    retrieved: list[RetrievedTicket]
    fast_path_match: RetrievedTicket | None
    assigned_category: str
    classification_confidence: float
    retrieval_similarity: float
    suggested_resolution: list[str]
    verifier_score: float
    confidence_score: float
    privacy_risk: float
    escalation_required: bool
    routing_latency_ms: int
    agent_state: str
    route_path: str
    semantic_cache_hit: bool
    matched_ticket_id: str | None


class RoutingAgent:
    def __init__(self, settings: Settings, repo: ClickHouseRepository, llm: NvidiaLLM):
        self.settings = settings
        self.repo = repo
        self.llm = llm
        self.graph = self._build_graph()

    async def route(self, request: TicketRequest) -> dict:
        started = time.perf_counter()
        raw_text = f"{request.short_description}\n\n{request.description}"
        initial_state: AgentState = {
            "request": request,
            "raw_text": raw_text,
            "agent_state": "started",
        }
        state = await self.graph.ainvoke(initial_state)
        state["routing_latency_ms"] = int((time.perf_counter() - started) * 1000)
        response = self._to_response(state)
        self.repo.insert_routing_decision(response | {"model_name": self.settings.nvidia_llm_model})
        self._store_ticket(request, state)
        return response

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("privacy", self._privacy_node)
        graph.add_node("retrieve", self._retrieve_node)
        graph.add_node("fast_path", self._fast_path_node)
        graph.add_node("ood_escalate", self._ood_escalation_node)
        graph.add_node("triage", self._triage_node)
        graph.add_node("generate", self._generate_node)
        graph.add_node("verify", self._verify_node)
        graph.add_node("escalate", self._escalate_node)

        graph.set_entry_point("privacy")
        graph.add_edge("privacy", "retrieve")
        graph.add_conditional_edges(
            "retrieve",
            self._route_after_retrieval,
            {
                "fast_path": "fast_path",
                "generative_rag": "triage",
                "ood_escalate": "ood_escalate",
            },
        )
        graph.add_edge("fast_path", END)
        graph.add_edge("ood_escalate", END)
        graph.add_edge("triage", "generate")
        graph.add_edge("generate", "verify")
        graph.add_conditional_edges(
            "verify",
            self._should_escalate,
            {"escalate": "escalate", "complete": END},
        )
        graph.add_edge("escalate", END)
        return graph.compile()

    async def _privacy_node(self, state: AgentState) -> AgentState:
        request = state["request"]
        raw_text = state["raw_text"]
        payload = request.model_dump()
        payload["raw_text"] = raw_text
        payload["bypass_redpanda"] = True
        payload["return_sanitized"] = True
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.post(f"{self.settings.privacy_shield_url}/v1/ingest/stream", json=payload)
                resp.raise_for_status()
                data = resp.json()
                state["ticket_id"] = data["ticket_id"]
                state["sanitized_text"] = data["sanitized_text"]
                state["redacted_count"] = len(data.get("findings", []))
                state["privacy_audit"] = {
                    "stream_id": data["stream_id"],
                    "ticket_id": data["ticket_id"],
                    "raw_sha256": data["raw_sha256"],
                    "sanitized_sha256": data["sanitized_sha256"],
                    "findings": data.get("findings", []),
                    "detector_version": data["detector_version"],
                    "policy_version": data["policy_version"],
                }
        except Exception:
            fallback = redact_text(raw_text)
            state["ticket_id"] = request.ticket_id or request.number or fallback.sanitized_sha256[:16]
            state["sanitized_text"] = fallback.sanitized_text
            state["redacted_count"] = len(fallback.findings)
            state["privacy_audit"] = {
                "stream_id": f"sync_{fallback.raw_sha256[:16]}",
                "ticket_id": state["ticket_id"],
                "raw_sha256": fallback.raw_sha256,
                "sanitized_sha256": fallback.sanitized_sha256,
                "findings": [
                    {
                        "entity_type": item.entity_type,
                        "placeholder": item.placeholder,
                        "confidence": item.confidence,
                        "start_offset": item.start_offset,
                        "end_offset": item.end_offset,
                    }
                    for item in fallback.findings
                ],
                "detector_version": "python-regex-v1",
                "policy_version": "enterprise-ticket-policy-v1",
            }
        state["agent_state"] = "privacy_complete"
        return state

    async def _retrieve_node(self, state: AgentState) -> AgentState:
        embedding = embed_text(state["sanitized_text"], self.settings.embedding_dim)
        retrieved = self.repo.find_similar(embedding, self.settings.rag_top_k)
        fast_path_match = next(
            (
                ticket
                for ticket in retrieved
                if ticket.similarity >= self.settings.fast_path_similarity_threshold
                and self._is_fast_path_eligible(ticket)
            ),
            None,
        )
        state["retrieved"] = retrieved
        state["fast_path_match"] = fast_path_match
        state["retrieval_similarity"] = round(retrieved[0].similarity, 4) if retrieved else 0.0
        state["matched_ticket_id"] = (
            fast_path_match.ticket_id
            if fast_path_match
            else retrieved[0].ticket_id if retrieved else None
        )
        state["route_path"] = "retrieved"
        state["semantic_cache_hit"] = False
        state["agent_state"] = "retrieval_complete"
        return state

    async def _fast_path_node(self, state: AgentState) -> AgentState:
        matched = state.get("fast_path_match") or state.get("retrieved", [])[0]
        privacy_risk = self._privacy_risk(state)
        state["assigned_category"] = normalize_category(matched.category)
        state["classification_confidence"] = 1.0
        state["verifier_score"] = 1.0
        state["privacy_risk"] = privacy_risk
        state["confidence_score"] = round(max(0.0, min(1.0, 0.99 - privacy_risk * 0.10)), 4)
        state["suggested_resolution"] = [matched.resolution]
        state["escalation_required"] = False
        state["route_path"] = "semantic_cache"
        state["semantic_cache_hit"] = True
        state["matched_ticket_id"] = matched.ticket_id
        state["agent_state"] = "semantic_cache_hit"
        return state

    async def _ood_escalation_node(self, state: AgentState) -> AgentState:
        category, confidence = keyword_category(state["sanitized_text"])
        privacy_risk = self._privacy_risk(state)
        state["assigned_category"] = normalize_category(category)
        state["classification_confidence"] = round(float(confidence), 4)
        state["verifier_score"] = 0.0
        state["privacy_risk"] = privacy_risk
        state["confidence_score"] = round(max(0.0, min(1.0, 0.25 * state.get("retrieval_similarity", 0.0))), 4)
        state["suggested_resolution"] = [
            "Escalate to a human reviewer: no sufficiently similar historical ticket was found.",
            "Collect affected system, timestamps, recent changes, logs, screenshots, and business impact before assigning a fix.",
        ]
        state["escalation_required"] = True
        state["route_path"] = "out_of_distribution"
        state["semantic_cache_hit"] = False
        state["agent_state"] = "ood_escalated"
        return state

    async def _triage_node(self, state: AgentState) -> AgentState:
        retrieved = state.get("retrieved", [])
        if retrieved:
            weights: Counter[str] = Counter()
            for item in retrieved:
                weights[item.category] += max(item.similarity, 0.0)
            category, score = weights.most_common(1)[0]
            total = sum(weights.values()) or 1.0
            confidence = max(0.35, min(0.98, score / total))
        else:
            category, confidence = keyword_category(state["sanitized_text"])
        state["assigned_category"] = normalize_category(category)
        state["classification_confidence"] = round(float(confidence), 4)
        state["route_path"] = "generative_rag"
        state["semantic_cache_hit"] = False
        state["agent_state"] = "triage_complete"
        return state

    async def _generate_node(self, state: AgentState) -> AgentState:
        state["suggested_resolution"] = self.llm.generate_resolution(
            ticket_text=state["sanitized_text"],
            category=state["assigned_category"],
            retrieved=state.get("retrieved", []),
        )
        state["agent_state"] = "generation_complete"
        return state

    async def _verify_node(self, state: AgentState) -> AgentState:
        verification = self.llm.verify(
            ticket_text=state["sanitized_text"],
            resolution=state.get("suggested_resolution", []),
            retrieved=state.get("retrieved", []),
        )
        privacy_risk = self._privacy_risk(state)
        confidence = (
            0.35 * state.get("classification_confidence", 0.0)
            + 0.25 * max(0.0, state.get("retrieval_similarity", 0.0))
            + 0.30 * verification.score
            + 0.10 * (1.0 - privacy_risk)
        )
        state["verifier_score"] = round(verification.score, 4)
        state["privacy_risk"] = round(privacy_risk, 4)
        state["confidence_score"] = round(max(0.0, min(1.0, confidence)), 4)
        state["escalation_required"] = (
            state["confidence_score"] < self.settings.routing_confidence_threshold
            or state.get("retrieval_similarity", 0.0) < 0.20
            or verification.score < 0.70
        )
        state["agent_state"] = "verification_complete"
        return state

    async def _escalate_node(self, state: AgentState) -> AgentState:
        state["agent_state"] = "escalated"
        return state

    def _route_after_retrieval(self, state: AgentState) -> str:
        similarity = state.get("retrieval_similarity", 0.0)
        if state.get("fast_path_match") is not None:
            return "fast_path"
        if similarity >= self.settings.rag_similarity_threshold:
            return "generative_rag"
        return "ood_escalate"

    def _is_fast_path_eligible(self, ticket: RetrievedTicket) -> bool:
        return (
            ticket.source != "api"
            and ticket.assignment_group != "Pending Review"
            and bool(ticket.resolution.strip())
        )

    def _should_escalate(self, state: AgentState) -> str:
        return "escalate" if state.get("escalation_required") else "complete"

    def _privacy_risk(self, state: AgentState) -> float:
        return round(min(0.4, state.get("redacted_count", 0) * 0.05), 4)

    def _to_response(self, state: AgentState) -> dict:
        retrieved = state.get("retrieved", [])
        return {
            "ticket_id": state["ticket_id"],
            "assigned_category": state["assigned_category"],
            "suggested_resolution": state.get("suggested_resolution", []),
            "confidence_score": state["confidence_score"],
            "confidence_components": {
                "classification": state.get("classification_confidence", 0.0),
                "retrieval_similarity": state.get("retrieval_similarity", 0.0),
                "verifier_score": state.get("verifier_score", 0.0),
                "privacy_risk": state.get("privacy_risk", 0.0),
            },
            "escalation_required": state.get("escalation_required", True),
            "redacted_entities_count": state.get("redacted_count", 0),
            "routing_latency_ms": state.get("routing_latency_ms", 0),
            "agent_state": state.get("agent_state", "unknown"),
            "retrieved_ticket_ids": [item.ticket_id for item in retrieved],
            "route_path": state.get("route_path", "generative_rag"),
            "semantic_cache_hit": bool(state.get("semantic_cache_hit", False)),
            "matched_ticket_id": state.get("matched_ticket_id"),
        }

    def _store_ticket(self, request: TicketRequest, state: AgentState) -> None:
        embedding = embed_text(state["sanitized_text"], self.settings.embedding_dim)
        record = TicketRecord(
            ticket_id=state["ticket_id"],
            number=request.number or state["ticket_id"],
            short_description=request.short_description,
            description=request.description,
            sanitized_text=state["sanitized_text"],
            category=state["assigned_category"],
            assignment_group=request.assignment_group or "Pending Review",
            resolution="\n".join(state.get("suggested_resolution", [])),
            urgency=request.urgency,
            impact=request.impact,
            embedding=embedding,
            source=request.source,
        )
        self.repo.insert_tickets([record])
        audit = state.get("privacy_audit")
        if audit:
            self.repo.insert_privacy_audit(**audit)


def normalize_category(category: str) -> str:
    value = category.strip().lower()
    mapping = {
        "software": "Application",
        "application": "Application",
        "hardware": "Infrastructure",
        "network": "Network",
        "access": "Access Management",
        "security": "Security",
        "database": "Database",
        "storage": "Storage",
        "infrastructure": "Infrastructure",
    }
    return mapping.get(value, category.strip().title() or "Application")


def keyword_category(text: str) -> tuple[str, float]:
    lowered = text.lower()
    if any(word in lowered for word in ["vpn", "network", "latency", "timeout", "router", "switch"]):
        return "Network", 0.62
    if any(word in lowered for word in ["access", "permission", "login", "mfa", "password"]):
        return "Access Management", 0.62
    if any(word in lowered for word in ["database", "sql", "query", "replication"]):
        return "Database", 0.58
    if any(word in lowered for word in ["disk", "storage", "backup", "volume"]):
        return "Storage", 0.58
    if any(word in lowered for word in ["malware", "phishing", "vulnerability", "token"]):
        return "Security", 0.60
    if any(word in lowered for word in ["server", "cpu", "memory", "host"]):
        return "Infrastructure", 0.58
    return "Application", 0.45
