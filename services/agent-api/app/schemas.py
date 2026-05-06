from pydantic import BaseModel, Field


class TicketRequest(BaseModel):
    ticket_id: str | None = None
    number: str | None = None
    short_description: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    urgency: int = Field(default=3, ge=1, le=3)
    impact: int = Field(default=3, ge=1, le=3)
    category: str | None = None
    assignment_group: str | None = None
    resolution: str | None = None
    source: str = "api"


class ConfidenceComponents(BaseModel):
    classification: float
    retrieval_similarity: float
    verifier_score: float
    privacy_risk: float


class RouteResponse(BaseModel):
    ticket_id: str
    assigned_category: str
    suggested_resolution: list[str]
    confidence_score: float
    confidence_components: ConfidenceComponents
    escalation_required: bool
    redacted_entities_count: int
    routing_latency_ms: int
    agent_state: str
    retrieved_ticket_ids: list[str]
    route_path: str
    semantic_cache_hit: bool
    matched_ticket_id: str | None = None


class ReviewDecision(BaseModel):
    ticket_id: str
    decision: str
    reviewer: str
    notes: str | None = None
    corrected_category: str | None = None
    corrected_resolution: str | None = None
