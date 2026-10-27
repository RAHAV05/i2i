"""Core Ground Rules and System Invariants for Ink 2 Insight (I2I).

Codifies the 8 core ground rules to ensure patient safety, data integrity,
and non-negotiable architectural principles across all pipelines.

Invariants:
1. Never Guess: Unclear/unreadable writing is marked 'unreadable' for human triage.
2. Separate Raw and Normalized: Raw extracted text is never overwritten.
3. Versioned Configuration: Templates, glossaries, and alert rules are versioned data.
4. Server/Database Enforced Security: Permissions checked server-side via RLS / tokens.
5. Gated Phase Completion: Tests and gates must pass before proceeding.
6. Real Patient Data Isolation: Dev/staging must only use synthetic/masked data.
7. Screening Flags Only: Alerts are screening flags for doctors, never automated diagnoses.
8. Automated Regression: Any change to prompts, models, or glossaries reruns accuracy harness.
"""

from typing import Dict, Any, Optional, Tuple, List
import logging

logger = logging.getLogger("i2i.invariants")

# Invariant 7: Mandatory screening disclaimer
SCREENING_DISCLAIMER = (
    "Screening flag for physician review. Not an automated medical diagnosis."
)

class GroundRuleViolation(Exception):
    """Raised when an operation violates core clinical or architectural ground rules."""
    pass


def wrap_field_value(
    raw_text: Optional[str],
    normalized_value: Any,
    confidence: float,
    status: str = "valid",
    is_unreadable: bool = False,
    crop_bbox: Optional[Dict[str, float]] = None,
    rule_applied: Optional[str] = None
) -> Dict[str, Any]:
    """Invariant 1 & 2: Store raw text as written and normalized value separately.
    
    If unreadable or unclear, mark status as 'unreadable' and never guess.
    """
    raw = str(raw_text or "").strip()
    
    # Invariant 1: Never guess on unreadable handwriting
    if is_unreadable or confidence < 0.25 or raw.lower() in ("unreadable", "illegible", "???"):
        final_status = "unreadable"
        final_norm = None
    else:
        final_status = status
        final_norm = normalized_value

    return {
        "raw_text": raw,
        "normalized_value": final_norm,
        "confidence": round(float(confidence), 3),
        "status": final_status,
        "crop_bbox": crop_bbox or {},
        "rule_applied": rule_applied or "raw_direct"
    }


def format_screening_alert(
    rule_key: str,
    rule_version: str,
    parameter: str,
    severity: str,
    message: str,
    measured_value: Any
) -> Dict[str, Any]:
    """Invariant 7: Screening flags for the doctor, never diagnoses.
    Must always carry the explicit disclaimer and rule version tracking.
    """
    return {
        "rule_key": rule_key,
        "rule_version": rule_version,
        "parameter": parameter,
        "severity": severity,  # 'advisory', 'warning', 'high_alert', 'critical'
        "message": message,
        "measured_value": measured_value,
        "disclaimer": SCREENING_DISCLAIMER,
        "is_screening_flag": True
    }


def validate_version_tracking(record: Dict[str, Any]) -> None:
    """Invariant 3: Templates, glossaries and alert rules must be versioned data."""
    required_versions = ["template_version_id", "glossary_version_id"]
    missing = [k for k in required_versions if not record.get(k)]
    if missing:
        raise GroundRuleViolation(
            f"Invariant 3 Violation: Missing mandatory version tracking fields: {missing}"
        )
