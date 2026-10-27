"""Unit and integration tests for Clinical Alert Engine, DPDP Erasure, Referral Slip PDF, and 7-Tab Review Persistence."""

import os
import io
import json
import pytest
from backend.services.ai_service import classify_bmi, classify_bp, apply_server_side_flags, validate_and_sanitize_extraction
from backend import database
from backend.app import create_app


def test_classify_bmi_staging():
    # 1. Underweight (< 18.5)
    uw = classify_bmi(17.2)
    assert uw["category"] == "Underweight"
    assert uw["alert_level"] == "warning"
    assert not uw["is_high_risk"]

    # 2. Normal (18.5 - 22.9)
    norm = classify_bmi(21.5)
    assert norm["category"] == "Normal Weight"
    assert norm["alert_level"] == "optimal"
    assert not norm["is_high_risk"]

    # 3. Asian-Indian Overweight (23.0 - 24.9)
    ov = classify_bmi(24.2)
    assert "Overweight" in ov["category"]
    assert ov["alert_level"] == "advisory"
    assert not ov["is_high_risk"]

    # 4. Asian-Indian Obese Class I / WHO Overweight (25.0 - 29.9)
    ob1 = classify_bmi(27.4)
    assert "Obese" in ob1["category"] or "Overweight" in ob1["category"]
    assert ob1["alert_level"] == "high"
    assert ob1["is_high_risk"] is True

    # 5. Obese Class II/III (>= 30.0)
    ob2 = classify_bmi(33.5)
    assert "Severe" in ob2["category"] or "Obese" in ob2["category"]
    assert ob2["alert_level"] == "critical"
    assert ob2["is_high_risk"] is True

    # 6. Edge cases & None
    assert classify_bmi(None)["category"] == "Unknown"
    assert classify_bmi("invalid")["category"] == "Unknown"


def test_classify_bp_staging():
    # 1. Normal (< 120 and < 80)
    norm = classify_bp(118, 76)
    assert norm["stage"] == "Normal"
    assert norm["alert_level"] == "normal"
    assert not norm["is_high_risk"]

    # 2. Elevated (120-129 and < 80)
    elev = classify_bp(124, 78)
    assert elev["stage"] == "Elevated BP"
    assert elev["alert_level"] == "advisory"
    assert not elev["is_high_risk"]

    # 3. Stage 1 Hypertension (130-139 or 80-89)
    st1 = classify_bp(134, 84)
    assert st1["stage"] == "Stage 1 Hypertension"
    assert st1["alert_level"] == "advisory"
    assert not st1["is_high_risk"]

    # 4. Stage 2 Hypertension (>= 140 or >= 90)
    st2 = classify_bp(145, 95)
    assert st2["stage"] == "Stage 2 Hypertension"
    assert st2["alert_level"] == "high"
    assert st2["is_high_risk"] is True

    # 5. Hypertensive Crisis (>= 180 or >= 120)
    crisis1 = classify_bp(190, 110)
    assert crisis1["stage"] == "Hypertensive Crisis"
    assert crisis1["alert_level"] == "critical"
    assert crisis1["is_high_risk"] is True

    crisis2 = classify_bp(160, 125)
    assert crisis2["stage"] == "Hypertensive Crisis"
    assert crisis2["alert_level"] == "critical"
    assert crisis2["is_high_risk"] is True

    # 6. Invalid / None
    assert classify_bp(None)["stage"] == "Unknown"


def test_ai_service_integration_flags():
    fields = {
        "full_name": ["Aarav Mehta", 0.98],
        "age": ["16", 0.95],
        "height_cm": ["170", 0.99],
        "weight_kg": ["82", 0.99], # BMI = 82 / 1.7^2 = 28.4 (High Risk / Obese Class I)
        "bp": ["146/92", 0.95],    # Stage 2 Hypertension
        "dob": ["12/04/2008", 0.99]
    }
    flags_meta = apply_server_side_flags(fields)
    assert flags_meta["calculated_bmi"] == 28.4
    assert flags_meta["bmi_classification"]["is_high_risk"] is True
    assert flags_meta["bp_classification"]["stage"] == "Stage 2 Hypertension"
    assert flags_meta["bp_classification"]["is_high_risk"] is True

    res = validate_and_sanitize_extraction({"personal_information": fields})
    assert res["bmi"] == 28.4
    assert "bmi_classification" in res
    assert "bp_classification" in res


def test_dpdp_erasure_removes_disk_files(tmp_path):
    db_file = str(tmp_path / "test_dpdp.db")
    database.init_db(db_file)
    camp = database.create_camp("DPDP Test Camp", 50, "Today", db_path=db_file)

    # Create dummy upload files in frontend/uploads
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    uploads_dir = os.path.join(base_dir, "frontend", "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    dummy_p1 = os.path.join(uploads_dir, "test_dpdp_p1.jpg")
    dummy_p2 = os.path.join(uploads_dir, "test_dpdp_p2.jpg")
    with open(dummy_p1, "wb") as f1, open(dummy_p2, "wb") as f2:
        f1.write(b"fake image 1")
        f2.write(b"fake image 2")

    rec = database.insert_record({
        "camp_id": camp["id"],
        "person_name": "Test DPDP Patient",
        "page1_preview": "/uploads/test_dpdp_p1.jpg",
        "page2_preview": "/uploads/test_dpdp_p2.jpg",
        "status": "needs_review"
    }, db_path=db_file)

    assert os.path.exists(dummy_p1)
    assert os.path.exists(dummy_p2)

    # Erase patient record
    erased = database.erase_patient_record(rec["id"], db_path=db_file)
    assert erased is True

    # Verify record gone from DB
    assert database.get_record_by_id(rec["id"], db_path=db_file) is None

    # Verify physical disk files securely unlinked
    assert not os.path.exists(dummy_p1)
    assert not os.path.exists(dummy_p2)


def test_referral_slip_pdf_endpoint(tmp_path):
    db_file = str(tmp_path / "test_slip.db")
    app = create_app(db_path=db_file)
    app.config["TESTING"] = True

    camp = database.create_camp("Slip Camp", 30, "Today", db_path=db_file)
    rec = database.insert_record({
        "camp_id": camp["id"],
        "person_name": "Rohan Patel",
        "age": "15",
        "gender": "Male",
        "height": "165",
        "weight": "74",
        "blood_pressure": "142/92",
        "referral_details": "Pediatric Cardiology",
        "reviewer_notes": "Follow up within 2 weeks.",
        "status": "reviewed",
        "structured_record": {
            "personal_information": {"admission_id": {"value": "ADM-101"}},
            "health_assessment": {
                "height_cm": {"value": "165"},
                "weight_kg": {"value": "74"},
                "blood_pressure_mmhg": {"value": "142/92"},
                "pulse_bpm": {"value": "88"}
            },
            "referral": {"specialist_referred_to": {"value": "Pediatric Cardiology"}}
        }
    }, db_path=db_file)

    with app.test_client() as client:
        res = client.get(f"/api/records/{rec['id']}/referral-slip")
        assert res.status_code == 200
        assert res.mimetype == "application/pdf"
        assert len(res.data) > 200
        assert res.data[:4] == b"%PDF"


def test_samd_audit_trail_and_bypassed_quality(tmp_path):
    db_file = str(tmp_path / "test_samd.db")
    database.init_db(db_file)
    camp = database.create_camp("Audit Camp", 50, "Today", db_path=db_file)

    raw_ai_record = {
        "personal_information": {"full_name": {"value": "Original AI Name"}},
        "health_assessment": {"height_cm": {"value": "160"}, "weight_kg": {"value": "55"}}
    }

    # Volunteer scanned with low light bypass
    bypassed_flags = ["Page 1: Low lighting / underexposed", "Page 2: Blurry / motion-blurred document"]

    rec = database.insert_record({
        "camp_id": camp["id"],
        "person_name": "Original AI Name",
        "structured_record": raw_ai_record,
        "original_ai_extraction": raw_ai_record,
        "quality_warning_bypassed": bypassed_flags,
        "status": "needs_review"
    }, db_path=db_file)

    assert rec["original_ai_extraction"] == raw_ai_record
    assert rec["quality_warning_bypassed"] == bypassed_flags

    # Physician edits the record and corrects name
    edited_structured = json.loads(json.dumps(raw_ai_record))
    edited_structured["personal_information"]["full_name"]["value"] = "Physician Corrected Name"
    edited_structured["medical_history"] = {"known_allergies": "Penicillin"}

    updated = database.update_reviewed_record(
        rec["id"],
        {
            "person_name": "Physician Corrected Name",
            "structured_record": edited_structured,
            "reviewer_notes": "Name corrected from paper form."
        },
        reviewer="Dr. Priya",
        db_path=db_file
    )

    # SaMD Audit check: structured_record has updated data, but original_ai_extraction is preserved unmodified!
    assert updated["person_name"] == "Physician Corrected Name"
    assert updated["structured_record"]["medical_history"]["known_allergies"] == "Penicillin"
    assert updated["original_ai_extraction"]["personal_information"]["full_name"]["value"] == "Original AI Name"
    assert updated["quality_warning_bypassed"] == bypassed_flags
