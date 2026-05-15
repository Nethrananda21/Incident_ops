from types import SimpleNamespace

from app.llm import NvidiaLLM, build_evidence_summary, clip_text, load_json_object, normalize_steps


def test_clip_text_handles_tiny_limits():
    assert clip_text("abcdef", 0) == ""
    assert clip_text("abcdef", 3) == "abc"


def test_load_json_object_accepts_wrapped_model_output():
    payload = "```json\n{\"score\": 0.82, \"rationale\": \"grounded\"}\n```"

    assert load_json_object(payload)["score"] == 0.82


def test_normalize_steps_removes_numbering_and_limits_output():
    steps = normalize_steps("1. Restart the service\n2) Verify logs\n3 - Monitor")

    assert steps == ["Restart the service", "Verify logs", "Monitor"]


def test_normalize_steps_removes_markdown_emphasis_from_step_titles():
    steps = normalize_steps("1. **Clear Browser Cache**: reload the Customer UI")

    assert steps == ["Clear Browser Cache: reload the Customer UI"]


def test_fallback_escalation_steps_do_not_reuse_historical_fix():
    llm = NvidiaLLM(SimpleNamespace(nvidia_api_key=""))

    decision = llm._fallback_decision(
        category="Application",
        retrieved=[],
        policy_signal="Outside IT incident scope",
    )

    assert decision.escalation_required is True
    assert "human support triage" in decision.resolution_steps[0]
    assert not any("Resolved by" in step for step in decision.resolution_steps)


def test_build_evidence_summary_is_compact_and_structured():
    summary = build_evidence_summary(
        {
            "quality_score": 0.88,
            "quality_band": "strong",
            "top_similarity": 0.91,
            "average_similarity": 0.84,
            "category_consensus": 0.74,
            "resolution_coverage": 1.0,
            "evidence_count": 2,
            "dominant_category": "Application",
            "policy": "LLM evaluates all non-cache evidence.",
            "items": [
                {
                    "ticket_id": "INC1",
                    "category": "Application",
                    "assignment_group": "IT Support",
                    "similarity": 0.91,
                    "evidence_role": "supporting_context",
                    "resolution_present": True,
                    "sanitized_text": "should not be duplicated here",
                }
            ],
        }
    )

    assert '"quality_band":"strong"' in summary
    assert '"ticket_id":"INC1"' in summary
    assert "should not be duplicated" not in summary
