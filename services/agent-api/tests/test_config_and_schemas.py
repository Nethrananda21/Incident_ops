import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas import ResolverRecommendation, TicketRequest


def test_settings_reject_inverted_similarity_thresholds():
    with pytest.raises(ValidationError):
        Settings(
            fast_path_similarity_threshold=0.60,
            rag_similarity_threshold=0.70,
        )


def test_settings_parses_approved_knowledge_sources():
    settings = Settings(approved_knowledge_sources=" curated, historical ,,seed ")

    assert settings.approved_knowledge_source_values == ("curated", "historical", "seed")


def test_ticket_request_rejects_blank_and_extra_fields():
    with pytest.raises(ValidationError):
        TicketRequest(short_description=" ", description="valid")

    with pytest.raises(ValidationError):
        TicketRequest(
            short_description="VPN down",
            description="User cannot connect",
            unexpected_field=True,
        )


def test_resolver_recommendation_uses_isolated_default_list():
    first = ResolverRecommendation(group="Network Ops", confidence=0.9, source="test")
    second = ResolverRecommendation(group="IT Support", confidence=0.8, source="test")

    first.alternates.append("Security")

    assert second.alternates == []
