"""Unit and integration tests for the Student/Staff Health Screening Form.
Tests the exact 8-group schema, metadata structure, blank templates,
and handwritten form extraction validation.
"""

import os
import json
import pytest
from backend import database
from backend.services.ai_service import validate_and_sanitize_extraction, _format_meta_field
from backend.services.record_service import RecordService

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")

@pytest.fixture
def service(tmp_path):
    db_file = str(tmp_path / "test_health_form.db")
    database.init_db(db_file)
    return RecordService(db_file)

def test_format_meta_field_structure():
    # Extracted field with details
    field = _format_meta_field({
        "value": "yes",
        "confidence": 0.96,
        "status": "extracted",
        "source_page": 1,
        "details": "UNDERGONE SURGERY AT AGE 11"
    }, default_page=1)

    assert field["value"] == "yes"
    assert field["confidence"] == 0.96
    assert field["status"] == "extracted"
    assert field["source_page"] == 1
    assert field["details"] == "UNDERGONE SURGERY AT AGE 11"

    # Missing / None field
    missing_f = _format_meta_field(None, default_page=2)
    assert missing_f["value"] is None
    assert missing_f["status"] == "missing"
    assert missing_f["source_page"] == 2
    assert missing_f["confidence"] == 0.0

def test_health_form_8_groups_present():
    raw_sample = {
        "personal_information": {
            "full_name": {"value": "Rahul Sharma", "confidence": 0.98},
            "age": {"value": "21"},
            "gender": {"value": "Male"},
            "blood_group": {"value": "O+"},
            "doc_id": {"value": "DOC-8942"},
            "consent_screening": {"value": "checked"}
        },
        "medical_history": {
            "known_allergies": {"value": "no"},
            "previous_surgery_hospitalization": {
                "value": "yes",
                "details": "Appendectomy at age 12"
            },
            "physical_activity": {"value": "yes", "details": "Football, gym"}
        },
        "family_history": {
            "diabetes": {"value": "checked", "relationship": "Father"},
            "hypertension": {"value": "unchecked"}
        },
        "health_assessment": {
            "examination_date": {"value": "07/10/2026"},
            "height_cm": {"value": "172"},
            "weight_kg": {"value": "65"},
            "pulse_bpm": {"value": "78"},
            "blood_pressure_mmhg": {"value": "118/76"},
            "random_blood_sugar_mgdl": {"value": "88"},
            "waist_circumference_cm": {"value": "76"},
            "pallor": {"value": "no"},
            "edema": {"value": "no"}
        },
        "system_examination": {
            "ear": {"value": "normal"},
            "respiratory_system": {"value": "normal"},
            "abdomen": {"value": "normal"}
        },
        "vision": {
            "vision_category": {"value": "unaided"},
            "distant_vision_right": {"value": "6/6"},
            "distant_vision_left": {"value": "6/6"}
        },
        "dental": {
            "normal": {"value": "normal"}
        },
        "referral": {
            "specialist_referred_to": {"value": None},
            "doctor_signature_present": {"value": "present"}
        }
    }

    sanitized = validate_and_sanitize_extraction(raw_sample)
    assert "structured_record" in sanitized
    sr = sanitized["structured_record"]
    
    # Check all 8 groups are present
    expected_groups = [
        "personal_information",
        "medical_history",
        "family_history",
        "health_assessment",
        "system_examination",
        "vision",
        "dental",
        "referral"
    ]
    for grp in expected_groups:
        assert grp in sr, f"Group '{grp}' missing from structured_record"

    # Check top-level backwards-compatible values
    assert sanitized["person_name"] == "Rahul Sharma"
    assert sanitized["age"] == "21"
    assert sanitized["gender"] == "Male"
    assert "172 cm" in sanitized["height"]
    assert "65 kg" in sanitized["weight"]
    assert "118/76 mmHg" in sanitized["blood_pressure"]

def test_checkbox_ambiguity_not_negative():
    """Unchecked checkbox should NOT be treated as negative medical history."""
    raw = {
        "medical_history": {
            "known_allergies": {"value": "unchecked"},
            "abdominal_pain_indigestion_constipation": None
        }
    }
    sanitized = validate_and_sanitize_extraction(raw)
    sr = sanitized["structured_record"]
    allergies = sr["medical_history"]["known_allergies"]
    abdominal = sr["medical_history"]["abdominal_pain_indigestion_constipation"]

    assert allergies["value"] == "unchecked"
    assert abdominal["value"] is None
    assert abdominal["status"] == "missing"

def test_database_persistence_with_structured_record(service):
    camp = database.create_camp("Health Check Adyar", 100, "17 Oct", service.db_path)
    
    structured_payload = {
        "personal_information": {
            "full_name": {"value": "Ananya K", "status": "extracted"},
            "blood_group": {"value": "B+", "status": "extracted"}
        },
        "health_assessment": {
            "height_cm": {"value": "160", "status": "extracted"},
            "weight_kg": {"value": "50", "status": "extracted"}
        }
    }

    rec = service.create_record({
        "camp_id": camp["id"],
        "person_name": "Ananya K",
        "age": "24",
        "structured_record": structured_payload
    })

    assert rec["id"] is not None
    assert rec["person_name"] == "Ananya K"
    assert "structured_record" in rec
    assert rec["structured_record"]["personal_information"]["blood_group"]["value"] == "B+"

    # Review update
    updated = service.review_record(rec["id"], {
        "person_name": "Ananya Krishnan",
        "structured_record": {
            **structured_payload,
            "personal_information": {
                **structured_payload["personal_information"],
                "full_name": {"value": "Ananya Krishnan", "status": "extracted"}
            }
        }
    }, reviewer="Dr. Suresh")

    assert updated["status"] == "reviewed"
    assert updated["person_name"] == "Ananya Krishnan"
    assert updated["structured_record"]["personal_information"]["full_name"]["value"] == "Ananya Krishnan"

def test_blank_template_fixtures_exist():
    """Verify that blank template fixtures exist and can be loaded."""
    p1 = os.path.join(FIXTURES_DIR, "blank_page_1.jpeg")
    p2 = os.path.join(FIXTURES_DIR, "blank_page_2.jpeg")
    assert os.path.exists(p1), "blank_page_1.jpeg fixture must exist"
    assert os.path.exists(p2), "blank_page_2.jpeg fixture must exist"
    assert os.path.getsize(p1) > 10000
    assert os.path.getsize(p2) > 10000

def test_filled_sample_fixtures_exist():
    """Verify that filled sample fixtures exist and can be loaded."""
    p1 = os.path.join(FIXTURES_DIR, "filled_sample_page_1.jpeg")
    p2 = os.path.join(FIXTURES_DIR, "filled_sample_page_2.jpeg")
    assert os.path.exists(p1), "filled_sample_page_1.jpeg fixture must exist"
    assert os.path.exists(p2), "filled_sample_page_2.jpeg fixture must exist"
    assert os.path.getsize(p1) > 10000
    assert os.path.getsize(p2) > 10000
