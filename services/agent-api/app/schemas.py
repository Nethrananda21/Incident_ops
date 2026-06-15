from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TicketRequest(ApiModel):
    ticket_id: str | None = Field(default=None, max_length=128)
    number: str | None = Field(default=None, max_length=128)
    short_description: str = Field(..., min_length=1, max_length=300)
    description: str = Field(..., min_length=1, max_length=8000)
    urgency: int = Field(default=3, ge=1, le=3)
    impact: int = Field(default=3, ge=1, le=3)
    category: str | None = Field(default=None, max_length=120)
    assignment_group: str | None = Field(default=None, max_length=160)
    resolution: str | None = Field(default=None, max_length=5000)
    source: str = Field(default="api", min_length=1, max_length=80)

    @field_validator("short_description", "description", "source")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class ConfidenceComponents(ApiModel):
    classification: float
    retrieval_similarity: float
    rag_evidence_quality: float = 0.0
    historical_similarity: float = 0.0
    verifier_score: float
    privacy_risk: float
    risk_modifier: float = 1.0


class RouteEvidence(ApiModel):
    label: str
    value: str
    impact: str


class KnowledgeGapSignal(ApiModel):
    is_gap: bool
    reason: str
    severity: str


class ResolverRecommendation(ApiModel):
    group: str
    confidence: float
    source: str
    alternates: list[str] = Field(default_factory=list)


class RagEvidenceItem(ApiModel):
    ticket_id: str
    category: str
    assignment_group: str
    source: str
    similarity: float
    evidence_role: str
    resolution_present: bool


class RagEvidenceSummary(ApiModel):
    quality_score: float
    quality_band: str
    top_similarity: float
    average_similarity: float
    category_consensus: float
    resolution_coverage: float
    evidence_count: int
    dominant_category: str | None = None
    items: list[RagEvidenceItem] = Field(default_factory=list)
    policy: str


class SlaRisk(ApiModel):
    score: float
    level: str


class RouteResponse(ApiModel):
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
    sla_risk: SlaRisk
    knowledge_gap: KnowledgeGapSignal
    resolver_recommendation: ResolverRecommendation
    rag_evidence: RagEvidenceSummary
    route_explanation: list[RouteEvidence]


class ReviewDecision(ApiModel):
    ticket_id: str = Field(..., min_length=1, max_length=128)
    decision: str = Field(..., min_length=1, max_length=40)
    reviewer: str = Field(..., min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=5000)
    corrected_category: str | None = Field(default=None, max_length=120)
    corrected_assignment_group: str | None = Field(default=None, max_length=160)
    corrected_resolution: str | None = Field(default=None, max_length=5000)

    @field_validator("ticket_id", "decision", "reviewer")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value
