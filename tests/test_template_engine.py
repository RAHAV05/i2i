"""Unit and integration tests for Template Engine, Declarative Clinical Forms,
Case Pairing Logic, and Ground Rule Invariants (Phase 2).
"""

import os
import json
import pytest
from backend.services.template_engine import TemplateEngine, TemplateValidationError, default_template_engine
from backend.services.invariants import (
    wrap_field_value,
    format_screening_alert,
    validate_version_tracking,
    GroundRuleViolation,
    SCREENING_DISCLAIMER
)
from backend.app import create_app


@pytest.fixture
def client(tmp_path):
    test_db = str(tmp_path / "test_templates.db")
    app = create_app(db_path=test_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ==============================================================================
# 1. TEMPLATE LOADING & SCHEMA INTEGRITY TESTS
# ==============================================================================

def test_load_all_four_client_templates():
    """Verify all 4 client templates load cleanly and satisfy schema validation."""
    engine = TemplateEngine()
    templates = engine.list_templates()
    assert len(templates) >= 4

    ids = [t["id"] for t in templates]
    assert "tpl_adult_history_v9_1" in ids
    assert "tpl_adult_examination_v9_1" in ids
    assert "tpl_paediatric_history_v9_3" in ids
    assert "tpl_paediatric_examination_v9_7" in ids


def test_adult_history_template_fields():
    """Verify Adult History (ASF-D&A-ACS-9.1/03/25) field attributes."""
    tpl = default_template_engine.get_template("tpl_adult_history_v9_1")
    assert tpl is not None
    assert tpl["footer_code"] == "ASF-D&A-ACS-9.1/03/25"
    assert tpl["family"] == "adult"
    assert tpl["page_type"] == "history"

    # Must contain critical fields
    field_keys = [f["key"] for s in tpl["sections"] for f in s["fields"]]
    assert "moc_doc_id" in field_keys
    assert "admission_id" in field_keys
    assert "person_name" in field_keys
    assert "known_allergies" in field_keys
    assert "consent_screening" in field_keys
    assert "signature_present" in field_keys


def test_adult_examination_template_has_no_id_fields():
    """Verify Adult Exam (ASF-D&A-AC 9.1/03/25) lacks ID fields and relies on name + date."""
    tpl = default_template_engine.get_template("tpl_adult_examination_v9_1")
    assert tpl is not None
    assert tpl["footer_code"] == "ASF-D&A-AC 9.1/03/25"
    assert tpl["family"] == "adult"
    assert tpl["page_type"] == "examination"

    field_keys = [f["key"] for s in tpl["sections"] for f in s["fields"]]
    # Ground truth: no Moc Doc or Admission ID printed on the physical adult exam sheet
    assert "moc_doc_id" not in field_keys
    assert "admission_id" not in field_keys
    assert "person_name" in field_keys
    assert "exam_date" in field_keys
    assert "blood_pressure" in field_keys
    assert "height" in field_keys
    assert "weight" in field_keys


def test_paediatric_examination_atr_and_otoscopy():
    """Verify Paediatric Exam (ASF-D&A-PCS-9.7/07/26) includes spinal caliper ATR and otoscopy."""
    tpl = default_template_engine.get_template("tpl_paediatric_examination_v9_7")
    assert tpl is not None
    assert tpl["footer_code"] == "ASF-D&A-PCS-9.7/07/26"

    field_keys = [f["key"] for s in tpl["sections"] for f in s["fields"]]
    assert "spinal_caliper_atr" in field_keys
    assert "otoscope_right_ear" in field_keys
    assert "otoscope_left_ear" in field_keys
    assert "admission_id" in field_keys


# ==============================================================================
# 2. PAGE CLASSIFICATION TESTS
# ==============================================================================

def test_classify_exact_footer_match():
    """Classification matches exact footer code."""
    res = default_template_engine.classify_page(footer_code_detected="ASF-D&A-ACS-9.1/03/25")
    assert res["matched"] is True
    assert res["template_id"] == "tpl_adult_history_v9_1"
    assert res["method"] == "footer_exact"
    assert res["confidence"] == 1.0


def test_classify_fuzzy_footer_match():
    """Classification matches OCR noisy footer substring."""
    res = default_template_engine.classify_page(footer_code_detected="scan asf-d&a-pcs-9.7/07/26 clean")
    assert res["matched"] is True
    assert res["template_id"] == "tpl_paediatric_examination_v9_7"
    assert res["method"] == "footer_fuzzy"


def test_classify_header_keyword_fallback():
    """Classification falls back to header keywords if footer unreadable."""
    res = default_template_engine.classify_page(
        footer_code_detected="",
        header_text_detected="School Health Camp Paediatric Examination Doctor Office Use Only"
    )
    assert res["matched"] is True
    assert res["template_id"] == "tpl_paediatric_examination_v9_7"
    assert res["method"] == "title_keyword"


def test_classify_unsupported_form():
    """Unknown forms trigger clear rejection."""
    res = default_template_engine.classify_page(footer_code_detected="FORM-UNKNOWN-X-99.9")
    assert res["matched"] is False
    assert res["template_id"] is None
    assert res["method"] == "unsupported"


# ==============================================================================
# 3. CASE PAIRING ENGINE TESTS
# ==============================================================================

def test_pair_case_by_matching_admission_id():
    """Paediatric case pairs by matching Admission ID."""
    h_page = {
        "family": "paediatric",
        "page_type": "history",
        "admission_id": "ADM-2026-904",
        "person_name": "Aarav Sharma"
    }
    e_page = {
        "family": "paediatric",
        "page_type": "examination",
        "admission_id": "ADM-2026-904",
        "person_name": "Aarav S"
    }
    pair_res = default_template_engine.pair_case(h_page, e_page)
    assert pair_res["status"] == "paired"
    assert pair_res["pairing_method"] == "admission_id"
    assert pair_res["confidence"] >= 0.99


def test_reject_conflicting_admission_ids():
    """Conflicting IDs are never merged silently."""
    h_page = {
        "family": "paediatric",
        "page_type": "history",
        "admission_id": "ADM-001",
        "person_name": "Child A"
    }
    e_page = {
        "family": "paediatric",
        "page_type": "examination",
        "admission_id": "ADM-002",
        "person_name": "Child B"
    }
    pair_res = default_template_engine.pair_case(h_page, e_page)
    assert pair_res["status"] == "mismatched"
    assert "Conflicting Admission IDs" in pair_res["reason"]


def test_adult_case_pairing_via_session_and_name():
    """Adult Exam (no ID) pairs successfully via same capture session and matching name."""
    h_page = {
        "family": "adult",
        "page_type": "history",
        "person_name": "Dr. Priya Ramanathan",
        "session_id": "sess_20261026_001"
    }
    e_page = {
        "family": "adult",
        "page_type": "examination",
        "person_name": "Priya Ramanathan",
        "session_id": "sess_20261026_001"
    }
    pair_res = default_template_engine.pair_case(h_page, e_page)
    assert pair_res["status"] == "paired"
    assert pair_res["pairing_method"] == "session_and_name"
    assert pair_res["confidence"] >= 0.90


def test_adult_case_pairing_different_session_needs_review():
    """Adult pages with matching name but different sessions go to needs_pairing queue."""
    h_page = {
        "family": "adult",
        "page_type": "history",
        "person_name": "Rohan Mehta",
        "session_id": "sess_A"
    }
    e_page = {
        "family": "adult",
        "page_type": "examination",
        "person_name": "Rohan Mehta",
        "session_id": "sess_B"
    }
    pair_res = default_template_engine.pair_case(h_page, e_page)
    assert pair_res["status"] == "needs_pairing"
    assert "Requires physician verification" in pair_res["reason"]


def test_reject_family_mismatch():
    """Cannot pair adult history with paediatric examination."""
    h_page = {"family": "adult", "page_type": "history", "person_name": "Adult Person"}
    e_page = {"family": "paediatric", "page_type": "examination", "person_name": "Child Person"}
    pair_res = default_template_engine.pair_case(h_page, e_page)
    assert pair_res["status"] == "mismatched"
    assert "Family mismatch" in pair_res["reason"]


# ==============================================================================
# 4. DYNAMIC 5TH TEMPLATE TEST (ZERO CODE CHANGE)
# ==============================================================================

def test_add_fifth_template_without_code_changes(tmp_path):
    """Prove that adding a 5th form requires only a new JSON file, no Python code changes."""
    tpl5 = {
        "id": "tpl_dental_specialized_v1",
        "family": "custom",
        "page_type": "examination",
        "footer_code": "DENTAL-CAMP-FORM-V1",
        "version": "1.0.0",
        "title": "Specialized Dental Assessment Sheet",
        "sections": [
            {
                "id": "dental_grid",
                "title": "Dental Tooth Chart",
                "fields": [
                    {
                        "key": "molar_caries",
                        "label": "Molar Caries Present",
                        "type": "yes_no",
                        "export_column": "Molar_Caries"
                    }
                ]
            }
        ]
    }
    
    # Save into temporary templates directory
    tpl_file = tmp_path / "dental_template.json"
    tpl_file.write_text(json.dumps(tpl5), encoding="utf-8")

    custom_engine = TemplateEngine(templates_dir=str(tmp_path))
    assert len(custom_engine.templates) == 1
    assert "tpl_dental_specialized_v1" in custom_engine.templates

    # Test classification
    cls = custom_engine.classify_page(footer_code_detected="DENTAL-CAMP-FORM-V1")
    assert cls["matched"] is True
    assert cls["template_id"] == "tpl_dental_specialized_v1"

    # Test UI schema generation
    ui_schema = custom_engine.generate_ui_schema("tpl_dental_specialized_v1")
    assert len(ui_schema["tabs"]) == 1
    assert ui_schema["tabs"][0]["fields"][0]["key"] == "molar_caries"


# ==============================================================================
# 5. CORE INVARIANTS TESTS
# ==============================================================================

def test_invariant_never_guess_unreadable():
    """Invariant 1: Unclear handwriting is marked unreadable with null normalized value."""
    res = wrap_field_value(raw_text="???", normalized_value="120/80", confidence=0.15)
    assert res["status"] == "unreadable"
    assert res["normalized_value"] is None
    assert res["raw_text"] == "???"


def test_invariant_separate_raw_and_normalized():
    """Invariant 2: Raw text is stored exactly as written, separate from normalized value."""
    res = wrap_field_value(raw_text="  120 / 80 mmHg  ", normalized_value="120/80", confidence=0.95)
    assert res["raw_text"] == "120 / 80 mmHg"
    assert res["normalized_value"] == "120/80"
    assert res["status"] == "valid"


def test_invariant_screening_alert_disclaimer():
    """Invariant 7: Alerts are screening flags carrying explicit disclaimer and rule version."""
    alert = format_screening_alert(
        rule_key="high_bmi_adult",
        rule_version="1.0.0",
        parameter="BMI",
        severity="high_alert",
        message="Patient BMI 28.4 kg/m² exceeds Asian-Indian obesity cut-off.",
        measured_value=28.4
    )
    assert alert["disclaimer"] == SCREENING_DISCLAIMER
    assert alert["rule_version"] == "1.0.0"
    assert alert["is_screening_flag"] is True


def test_invariant_version_tracking_validation():
    """Invariant 3: Records without template and glossary versions violate ground rules."""
    valid_record = {"template_version_id": "tpl_v1", "glossary_version_id": "gloss_v1"}
    validate_version_tracking(valid_record)  # Should not raise

    invalid_record = {"person_name": "Test"}
    with pytest.raises(GroundRuleViolation):
        validate_version_tracking(invalid_record)


# ==============================================================================
# 6. REST API ENDPOINTS INTEGRATION TESTS
# ==============================================================================

def test_api_list_templates(client):
    """GET /api/templates returns available templates."""
    res = client.get("/api/templates")
    assert res.status_code == 200
    data = res.get_json()
    assert "templates" in data
    assert data["count"] >= 4


def test_api_get_template_ui_schema(client):
    """GET /api/templates/<id>?ui_schema=true returns dynamic UI layout."""
    res = client.get("/api/templates/tpl_adult_history_v9_1?ui_schema=true")
    assert res.status_code == 200
    data = res.get_json()
    assert "tabs" in data
    assert len(data["tabs"]) >= 4


def test_api_classify_template(client):
    """POST /api/templates/classify correctly classifies footer."""
    res = client.post("/api/templates/classify", json={"footer_code": "ASF-D&A-ACS-9.1/03/25"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["matched"] is True
    assert data["template_id"] == "tpl_adult_history_v9_1"


def test_api_pair_case(client):
    """POST /api/cases/pair pairs pages cleanly."""
    payload = {
        "history_page": {
            "family": "paediatric",
            "page_type": "history",
            "admission_id": "ADM-101",
            "person_name": "Pooja"
        },
        "exam_page": {
            "family": "paediatric",
            "page_type": "examination",
            "admission_id": "ADM-101",
            "person_name": "Pooja"
        }
    }
    res = client.post("/api/cases/pair", json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "paired"
    assert data["pairing_method"] == "admission_id"
