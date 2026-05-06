from __future__ import annotations

import argparse
import json
from pathlib import Path


EASY_PATTERNS = [
    ("Application", "IT Support", "Merchant agreement e-signature button disabled", "Sales Agent can open the Merchant Agreement PDF, but the e-signature submit button is disabled for merchant profile {merchant}."),
    ("Application", "IT Support", "Equipment dropdown empty during merchant onboarding", "Sales Agent reaches the equipment step, but the POS terminal dropdown is empty for new location {location}."),
    ("Access Management", "IAM Support", "Sales Agent denied fee configuration page", "Sales Agent has valid login but receives access denied when opening the service fee configuration page for merchant {merchant}."),
    ("Application", "IT Support", "Merchant data not transferring to processing stage", "Merchant data entered by Sales Agents in the Sales UI is not transferring to the Processing stage after completion for location {location}."),
    ("Database", "Database Support", "Duplicate merchant ID warning for additional location", "Sales Agent attempts to add a new location for existing merchant {merchant}, but the portal reports a duplicate merchant ID warning."),
    ("Access Management", "IAM Support", "Access permission error for e-signing Merchant Agreement", "Merchant reviewer can open the onboarding portal but receives a permission error when attempting to e-sign the Merchant Agreement for case {case_id}."),
    ("Application", "IT Support", "Agreement PDF opens blank for merchant reviewer", "Merchant reviewer can authenticate but sees a blank agreement screen instead of the expected PDF for onboarding case {case_id}."),
    ("Network", "Network Support", "Network outage during location data submission", "While submitting location data for merchant {merchant}, a short network outage interrupted data transmission and delayed onboarding."),
    ("Application", "IT Support", "Fees calculation mismatch in final agreement", "Sales Agent observes that the system is inaccurately calculating processing fees for fee bundle {bundle}, causing discrepancies in the final agreement."),
    ("Access Management", "IAM Support", "New Sales Agent missing onboarding portal role", "New Sales Agent can sign in but cannot open the merchant onboarding portal for assigned region {region}."),
]


MEDIUM_PATTERNS = [
    ("Application", "IT Support", "Intermittent e-sign failures for selected merchants", "Three merchants can open agreements, but e-sign submission intermittently fails after document review for cases in region {region}."),
    ("Network", "Network Support", "Merchant onboarding API returns repeated 500 errors", "Sales UI repeatedly receives API 500 responses while uploading merchant data for location {location}, but other locations work."),
    ("Database", "Database Support", "Fee calculation mismatch for bundled services", "The fee module calculates a different total than the final agreement for bundle {bundle}, affecting several merchants."),
    ("Hardware", "Desktop Support", "Equipment selection not saved for new location", "During the onboarding process for a new merchant location, Sales Agent selected equipment option {device} but the selections were not being saved despite multiple attempts."),
    ("Access Management", "IAM Support", "Temporary fee override role has unclear approval", "Sales Agent requests temporary fee override access for merchant {merchant}, but approval notes are incomplete."),
]


HARD_PATTERNS = [
    ("Security", "Security Operations", "Possible unauthorized merchant data export", "Audit logs show a bulk export of merchant onboarding records from an account not assigned to region {region}."),
    ("Security", "Security Operations", "Merchant agreement exposes another merchant data", "A merchant reviewer reports seeing another merchant's agreement details while reviewing case {case_id}."),
    ("Database", "Database Support", "Production merchant database corruption warning", "The production onboarding database reports checksum corruption while Sales Agents are submitting new merchant records."),
    ("Application", "IT Support", "Payment fees applied incorrectly to live merchants", "The fee engine appears to apply incorrect processing rates to live merchants after bundle {bundle} was changed."),
    ("Security", "Security Operations", "Privileged onboarding role granted without ticket", "A privileged onboarding administrator role was granted to an account with no matching access approval ticket."),
    ("Network", "Network Support", "Payment terminal activation outage across regions", "Terminal activation fails across multiple regions and merchants cannot begin payment processing."),
    ("Infrastructure", "Infrastructure Support", "Onboarding message queue backlog blocking processing", "The production onboarding queue depth is growing quickly and processing workers are crashing with unknown errors."),
    ("Security", "Security Operations", "Suspicious OAuth app has merchant record access", "A newly consented OAuth application has read access to merchant onboarding records and no registered owner."),
    ("Database", "Database Support", "Restore requested for production onboarding data", "A team requests production restore for onboarding data, but recovery point approval is missing."),
    ("Application", "IT Support", "Duplicate charges reported after terminal activation", "New merchants report duplicate payment charges after terminal activation even though onboarding shows one completed setup."),
]


MERCHANTS = [
    "MRC-2184",
    "MRC-4077",
    "MRC-5032",
    "MRC-6190",
    "MRC-7345",
    "MRC-8451",
    "MRC-9026",
    "MRC-1138",
    "MRC-2679",
    "MRC-3904",
]
LOCATIONS = ["Austin-02", "Phoenix-04", "Raleigh-01", "Denver-07", "Tampa-03"]
REGIONS = ["Northeast", "Midwest", "Southwest", "Pacific", "Central"]
DEVICES = ["PX-700", "VX-520", "SmartPOS-4", "TapPro-12", "RetailDock-8"]
BUNDLES = ["starter-plus", "retail-pro", "high-volume", "seasonal-market", "mobile-dining"]


def render(template: str, index: int) -> str:
    return template.format(
        merchant=MERCHANTS[index % len(MERCHANTS)],
        location=LOCATIONS[index % len(LOCATIONS)],
        region=REGIONS[index % len(REGIONS)],
        device=DEVICES[index % len(DEVICES)],
        bundle=BUNDLES[index % len(BUNDLES)],
        case_id=f"CASE-{8100 + index}",
    )


def build_ticket(index: int, difficulty: str, pattern_index: int, pattern: tuple[str, str, str, str]) -> dict[str, object]:
    category, group, short_template, description_template = pattern
    prefix = {"easy": "EASY", "medium": "MED", "hard": "HARD"}[difficulty]
    expected_escalation = difficulty == "hard" or (difficulty == "medium" and pattern_index in {2, 4})
    urgency = 1 if difficulty == "hard" else 2 if difficulty == "medium" else 3
    impact = 1 if difficulty == "hard" else 2 if difficulty == "medium" else 3
    short_description = f"{render(short_template, index)} - variant {pattern_index + 1}"
    description = (
        f"{render(description_template, index)} "
        f"The ticket is a new synthetic ServiceNow-style merchant onboarding case, benchmark variant {index:03d}."
    )
    return {
        "ticket_id": f"EVAL-{prefix}-{index:03d}",
        "number": f"EVAL{index:04d}",
        "difficulty": difficulty,
        "short_description": short_description,
        "description": description,
        "urgency": urgency,
        "impact": impact,
        "category": category,
        "assignment_group": group,
        "expected_escalation_required": expected_escalation,
        "expected_decision": "human_review" if expected_escalation else "auto_resolution",
    }


def add_tickets(
    tickets: list[dict[str, object]],
    *,
    difficulty: str,
    count: int,
    patterns: list[tuple[str, str, str, str]],
    start_index: int,
) -> int:
    index = start_index
    for offset in range(count):
        pattern_index = offset % len(patterns)
        tickets.append(build_ticket(index, difficulty, pattern_index, patterns[pattern_index]))
        index += 1
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a 100-ticket LLM routing benchmark set.")
    parser.add_argument("--output", default="data/eval_ticket_set_100.json")
    args = parser.parse_args()

    tickets: list[dict[str, object]] = []
    index = 1
    index = add_tickets(tickets, difficulty="easy", count=40, patterns=EASY_PATTERNS, start_index=index)
    index = add_tickets(tickets, difficulty="medium", count=20, patterns=MEDIUM_PATTERNS, start_index=index)
    add_tickets(tickets, difficulty="hard", count=40, patterns=HARD_PATTERNS, start_index=index)

    output = {
        "metadata": {
            "name": "IncidentOps AI merchant-onboarding escalation benchmark",
            "total_tickets": len(tickets),
            "difficulty_distribution": {"easy": 40, "medium": 20, "hard": 40},
            "expected_escalations": sum(1 for ticket in tickets if ticket["expected_escalation_required"]),
            "expected_auto_resolutions": sum(1 for ticket in tickets if not ticket["expected_escalation_required"]),
            "purpose": "Verify whether the routing agent returns auto-resolution or human escalation for new unique merchant-onboarding tickets.",
        },
        "tickets": tickets,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"generated {len(tickets)} tickets -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
