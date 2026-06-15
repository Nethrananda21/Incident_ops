from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent import RoutingAgent, keyword_category
from app.clickhouse_repo import RetrievedTicket
from app.llm import ResolutionDecision


class WorkflowProbe(RoutingAgent):
    def __init__(self, fast_path: bool = False, escalate: bool = False):
        self.fast_path = fast_path
        self.escalate = escalate
        self.steps: list[str] = []
        self.workflow = self._build_workflow()

    async def _privacy_node(self, state):
        self.steps.append("privacy")
        return state

    async def _retrieve_node(self, state):
        self.steps.append("retrieve")
        state["fast_path_match"] = object() if self.fast_path else None
        return state

    async def _evidence_node(self, state):
        self.steps.append("evidence")
        return state

    async def _assessment_node(self, state):
        self.steps.append("assess")
        return state

    async def _fast_path_node(self, state):
        self.steps.append("fast_path")
        return state

    async def _policy_escalation_node(self, state):
        self.steps.append("policy_escalate")
        return state

    async def _ood_escalation_node(self, state):
        self.steps.append("ood_escalate")
        return state

    async def _triage_node(self, state):
        self.steps.append("triage")
        return state

    async def _llm_resolution_node(self, state):
        self.steps.append("llm_resolution")
        state["escalation_required"] = self.escalate
        return state

    async def _escalate_node(self, state):
        self.steps.append("escalate")
        return state


@pytest.mark.asyncio
async def test_fast_path_workflow_terminates_after_cache_hit():
    agent = WorkflowProbe(fast_path=True)

    await agent._run_workflow({"agent_state": "started"})

    assert agent.steps == ["privacy", "retrieve", "evidence", "assess", "fast_path"]


@pytest.mark.asyncio
async def test_non_cache_workflow_always_reaches_llm_decision_before_escalation():
    agent = WorkflowProbe(fast_path=False, escalate=True)

    await agent._run_workflow({"agent_state": "started"})

    assert agent.steps == ["privacy", "retrieve", "evidence", "assess", "triage", "llm_resolution", "escalate"]


def test_fast_path_eligibility_requires_curated_source():
    agent = RoutingAgent.__new__(RoutingAgent)
    agent.settings = SimpleNamespace(approved_knowledge_source_values=("curated",))

    untrusted_ticket = RetrievedTicket(
        ticket_id="api-1",
        short_description="VPN issue",
        sanitized_text="VPN issue",
        category="Network",
        assignment_group="Network Ops",
        resolution="Restart VPN gateway.",
        similarity=0.99,
        source="api",
    )
    curated_ticket = RetrievedTicket(
        ticket_id="curated-1",
        short_description="VPN issue",
        sanitized_text="VPN issue",
        category="Network",
        assignment_group="Network Ops",
        resolution="Restart VPN gateway.",
        similarity=0.99,
        source="curated",
    )

    assert not agent._is_fast_path_eligible(untrusted_ticket)
    assert agent._is_fast_path_eligible(curated_ticket)


def test_payment_gateway_timeout_is_application_not_network():
    category, confidence = keyword_category(
        "Payment Gateway Timeout during money transfer. "
        "Payment Failed: 503 Gateway Timeout and the Payments API appears unavailable."
    )

    assert category == "Application"
    assert confidence > 0.6


def test_plain_network_timeout_still_routes_to_network():
    category, confidence = keyword_category("VPN timeout and high latency through the office router")

    assert category == "Network"
    assert confidence > 0.6


def test_bearer_token_exposure_raises_privacy_risk():
    agent = RoutingAgent.__new__(RoutingAgent)
    state = {
        "raw_text": "Checkout failures include an unredacted bearer token in debug header.",
        "sanitized_text": "Checkout failures include an unredacted bearer token in debug header.",
        "redacted_count": 0,
        "privacy_audit": {"findings": []},
    }

    assert "Bearer token exposure" in agent._security_findings(state)
    assert agent._privacy_risk(state) >= 0.35


def test_password_reset_is_not_credential_exposure():
    agent = RoutingAgent.__new__(RoutingAgent)
    state = {
        "request": SimpleNamespace(urgency=3, impact=3),
        "raw_text": "VPN login fails after password reset",
        "sanitized_text": "VPN login fails after password reset",
        "redacted_count": 0,
        "privacy_audit": {"findings": []},
    }

    assert agent._security_findings(state) == []
    assert agent._human_review_reason(state) is None


def test_password_value_still_requires_security_review():
    agent = RoutingAgent.__new__(RoutingAgent)
    state = {
        "request": SimpleNamespace(urgency=3, impact=3),
        "raw_text": "VPN ticket includes password=TempPass123 in logs",
        "sanitized_text": "VPN ticket includes [CREDENTIAL_1] in logs",
        "redacted_count": 1,
        "privacy_audit": {"findings": [{"entity_type": "CREDENTIAL", "confidence": 0.97}]},
    }

    assert "Credential exposure" in agent._security_findings(state)
    assert agent._human_review_reason(state) == "credential exposure requires security review"


def test_application_payment_fallback_routes_to_payments_engineering():
    agent = RoutingAgent.__new__(RoutingAgent)
    state = {
        "assigned_category": "Application",
        "sanitized_text": "Checkout timeout in payment orchestration with database deadlocks.",
        "raw_text": "Checkout timeout in payment orchestration with database deadlocks.",
        "retrieved": [],
        "redacted_count": 0,
        "privacy_audit": {"findings": []},
    }

    recommendation = agent._resolver_recommendation(state)

    assert recommendation["group"] == "Payments Engineering"
    assert recommendation["source"] == "category_default"


def test_out_of_scope_ticket_still_requires_human_review():
    agent = RoutingAgent.__new__(RoutingAgent)
    state = {
        "request": SimpleNamespace(urgency=3, impact=3),
        "sanitized_text": "Please arrange cafeteria lunch for the product event",
        "raw_text": "Please arrange cafeteria lunch for the product event",
        "redacted_count": 0,
        "privacy_audit": {"findings": []},
    }

    assert agent._human_review_reason(state) == "outside supported IT incident scope"


@pytest.mark.asyncio
async def test_llm_resolution_node_uses_llm_below_semantic_cache_threshold():
    agent = RoutingAgent.__new__(RoutingAgent)
    calls = []
    agent.llm = SimpleNamespace(
        resolve_and_decide=lambda **kwargs: calls.append(kwargs) or ResolutionDecision(
            resolution_steps=["LLM-generated resolution step"],
            escalation_required=False,
            confidence_score=0.91,
            rationale="Retrieved context is sufficient.",
        ),
    )
    agent.settings = SimpleNamespace(rag_similarity_threshold=0.70)
    state = {
        "request": SimpleNamespace(urgency=3, impact=3),
        "sanitized_text": "Agreement upload returns a 500 error",
        "assigned_category": "Application",
        "classification_confidence": 0.90,
        "retrieval_similarity": 0.94,
        "redacted_count": 0,
        "route_path": "retrieved",
        "semantic_cache_hit": False,
        "matched_ticket_id": "INC12345",
        "rag_evidence": {"quality_band": "strong", "quality_score": 0.91, "evidence_count": 1},
        "retrieved": [
            RetrievedTicket(
                ticket_id="INC12345",
                short_description="Agreement upload 500 error",
                sanitized_text="Agreement upload returns 500",
                category="Application",
                assignment_group="IT Support",
                resolution="Restart the upload worker and retry the agreement upload.",
                similarity=0.94,
                source="curated",
            )
        ],
    }

    result = await agent._llm_resolution_node(state)

    assert calls
    assert calls[0]["retrieved"][0].similarity == 0.94
    assert calls[0]["rag_evidence"]["quality_band"] == "strong"
    assert result["suggested_resolution"] == ["LLM-generated resolution step"]
    assert result["escalation_required"] is False
    assert result["route_path"] == "generative_rag"
    assert result["confidence_score"] == pytest.approx(0.35 * 0.90 + 0.35 * 0.91 + 0.20 * 0.94 + 0.10)


@pytest.mark.asyncio
async def test_llm_decision_can_escalate_even_when_similarity_is_not_low():
    agent = RoutingAgent.__new__(RoutingAgent)
    calls = []
    agent.llm = SimpleNamespace(
        resolve_and_decide=lambda **kwargs: calls.append(kwargs) or ResolutionDecision(
            resolution_steps=["Preserve evidence and route to the incident commander."],
            escalation_required=True,
            confidence_score=0.88,
            rationale="The ticket needs human authorization.",
        ),
    )
    agent.settings = SimpleNamespace(rag_similarity_threshold=0.70)
    state = {
        "request": SimpleNamespace(urgency=3, impact=3),
        "sanitized_text": "Agreement upload returns a 500 error",
        "assigned_category": "Application",
        "classification_confidence": 0.90,
        "retrieval_similarity": 0.94,
        "redacted_count": 0,
        "route_path": "retrieved",
        "semantic_cache_hit": False,
        "matched_ticket_id": "INC12345",
        "rag_evidence": {"quality_band": "strong", "quality_score": 0.91, "evidence_count": 1},
        "retrieved": [
            RetrievedTicket(
                ticket_id="INC12345",
                short_description="Agreement upload 500 error",
                sanitized_text="Agreement upload returns 500",
                category="Application",
                assignment_group="IT Support",
                resolution="Restart the upload worker and retry the agreement upload.",
                similarity=0.94,
                source="curated",
            )
        ],
    }

    result = await agent._llm_resolution_node(state)

    assert calls
    assert result["verifier_score"] == 0.88
    assert result["escalation_required"] is True
    assert result["route_path"] == "human_review_required"


@pytest.mark.asyncio
async def test_low_confidence_llm_review_without_policy_risk_auto_routes():
    agent = RoutingAgent.__new__(RoutingAgent)
    agent.llm = SimpleNamespace(
        resolve_and_decide=lambda **_: ResolutionDecision(
            resolution_steps=[
                "Verify the user completed the VPN password reset procedure.",
                "Check VPN authentication policy and account synchronization status.",
                "Ask Network Ops to validate gateway authentication logs.",
            ],
            escalation_required=True,
            confidence_score=0.20,
            rationale="No historical context was retrieved.",
        ),
    )
    agent.settings = SimpleNamespace(rag_similarity_threshold=0.70)
    state = {
        "request": SimpleNamespace(urgency=3, impact=3),
        "sanitized_text": "VPN login fails after password reset",
        "raw_text": "VPN login fails after password reset",
        "assigned_category": "Network",
        "classification_confidence": 0.62,
        "retrieval_similarity": 0.0,
        "redacted_count": 0,
        "privacy_audit": {"findings": []},
        "retrieved": [],
        "route_path": "retrieved",
        "semantic_cache_hit": False,
        "matched_ticket_id": None,
        "rag_evidence": {"quality_band": "none", "quality_score": 0.0, "evidence_count": 0},
    }

    result = await agent._llm_resolution_node(state)

    assert result["escalation_required"] is False
    assert result["route_path"] == "generative_rag"
    assert "sparse" in result["escalation_rationale"]


@pytest.mark.asyncio
async def test_llm_decision_can_auto_resolve_even_when_similarity_is_low():
    agent = RoutingAgent.__new__(RoutingAgent)
    agent.llm = SimpleNamespace(
        resolve_and_decide=lambda **_: ResolutionDecision(
            resolution_steps=["Collect logs, restart the affected worker, and validate the queue drain."],
            escalation_required=False,
            confidence_score=0.86,
            rationale="The symptoms are resolvable from retrieved operational context.",
        ),
    )
    agent.settings = SimpleNamespace(rag_similarity_threshold=0.70)
    state = {
        "request": SimpleNamespace(urgency=2, impact=2),
        "sanitized_text": "Merchant cannot access agreement review screen",
        "assigned_category": "Network",
        "classification_confidence": 0.62,
        "retrieval_similarity": 0.32,
        "redacted_count": 0,
        "retrieved": [],
        "route_path": "retrieved",
        "semantic_cache_hit": False,
        "matched_ticket_id": "INC00000",
        "rag_evidence": {"quality_band": "weak", "quality_score": 0.24, "evidence_count": 0},
    }

    result = await agent._llm_resolution_node(state)

    assert result["escalation_required"] is False
    assert result["route_path"] == "generative_rag"


def test_rag_evidence_scores_supporting_context_without_routing_escalation():
    agent = RoutingAgent.__new__(RoutingAgent)
    agent.settings = SimpleNamespace(
        fast_path_similarity_threshold=0.95,
        rag_similarity_threshold=0.70,
    )
    state = {
        "fast_path_match": None,
        "retrieved": [
            RetrievedTicket(
                ticket_id="INC11111",
                short_description="Agreement generation times out",
                sanitized_text="Agreement generation times out in the merchant portal",
                category="Application",
                assignment_group="IT Support",
                resolution="Restart the agreement worker and clear the stuck job.",
                similarity=0.82,
                source="6StringNinja/synthetic-servicenow-incidents",
            ),
            RetrievedTicket(
                ticket_id="INC22222",
                short_description="Agreement screen 500",
                sanitized_text="Agreement review screen returns 500",
                category="Application",
                assignment_group="IT Support",
                resolution="Recycle the document rendering service.",
                similarity=0.76,
                source="6StringNinja/synthetic-servicenow-incidents",
            ),
        ],
    }

    evidence = agent._build_rag_evidence(state)

    assert evidence["quality_band"] == "strong"
    assert evidence["dominant_category"] == "Application"
    assert evidence["items"][0]["evidence_role"] == "supporting_context"
    assert evidence["policy"].startswith("Semantic cache bypass is allowed only")
