"""Appendix D Acceptance Tests for Phase 1 and Phase 3:
- Sheet naming: student_form and health_assessment
- Schema validation, 1 retry, then needs_manual
- Server-side clinical plausibility rules and confidence flags
- Low-confidence escalation rules & logging
- Atomic paper reservation with 10 parallel scans and 3 papers left
- Release reservation on failure
- Approval marks paper billed & blocks unresolved flags (409)
- Free re-scan within 24 hours and image-hash reuse
- Usage and ledger endpoints (tokens_in, tokens_out, tokens_think, seconds, cost_rs)
- Admin AI kill switch (/api/admin/ai-pause)
"""

import io
import time
import json
import pytest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock

from backend.app import create_app
from backend import database
from backend.services.ai_service import (
    apply_server_side_flags,
    should_escalate_record,
    parse_and_validate_schema,
    validate_and_sanitize_extraction,
    ai_extract,
    FLAG_CONFIDENCE,
    FLAG_CONFIDENCE_MEASURE,
    CRITICAL_MEASUREMENT_KEYS
)


@pytest.fixture
def app_and_db(tmp_path):
    db_file = str(tmp_path / "test_phase1_phase3.db")
    app = create_app(db_path=db_file)
    app.config["TESTING"] = True
    database.create_camp("Phase Test Camp", 100, "Today", camp_id="camp_phase_test", db_path=db_file)
    return app, db_file


@pytest.fixture
def client(app_and_db):
    app, _ = app_and_db
    with app.test_client() as c:
        yield c


def _dummy_image(color=(255, 255, 255), size=(100, 100)):
    from PIL import Image
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PHASE 1 TESTS
# ---------------------------------------------------------------------------

def test_sheet_naming_student_form_and_health_assessment(client, app_and_db):
    """Confirm endpoints accept standard sheet names student_form and health_assessment."""
    _, db_path = app_and_db
    sf_img = _dummy_image(color=(10, 20, 30))
    ha_img = _dummy_image(color=(40, 50, 60))

    data = {
        "student_form": (io.BytesIO(sf_img), "student_form.jpg", "image/jpeg"),
        "health_assessment": (io.BytesIO(ha_img), "health_assessment.jpg", "image/jpeg"),
        "camp_id": "camp_phase_test"
    }

    mock_res = {
        "success": True,
        "data": validate_and_sanitize_extraction({
            "person_name": "Rahul Dravid",
            "full_name": "Rahul Dravid",
            "dob": "1973-01-11",
            "blood_pressure": "120/80",
            "pulse": "72",
            "weight": "75",
            "height": "175",
            "rbs": "105"
        }),
        "usage": {"tokens_in": 1200, "tokens_out": 400, "tokens_think": 20, "seconds": 1.2, "cost_rs": 0.50}
    }

    with patch("backend.services.gemini_service.GeminiService.extract_from_images", return_value=mock_res):
        res = client.post("/api/extract", data=data, content_type="multipart/form-data")
        assert res.status_code == 200
        json_data = res.json
        assert json_data["success"] is True
        assert json_data["data"]["person_name"] == "Rahul Dravid"


def test_schema_validation_and_retry_then_needs_manual(app_and_db):
    """Validate with schema; one retry; then status needs_manual and paper reservation released."""
    _, db_path = app_and_db
    sf_img = _dummy_image(color=(1, 2, 3))
    ha_img = _dummy_image(color=(4, 5, 6))

    # Invalid non-JSON output from model
    mock_bad = {
        "success": False,
        "error": "Failed to parse model response as valid JSON",
        "raw_text": "This is completely unparseable gibberish without any JSON.",
        "usage": {"tokens_in": 500, "tokens_out": 50, "tokens_think": 0, "seconds": 0.8, "cost_rs": 0.20}
    }

    initial_usage = database.get_usage_summary(org_id="org_default", db_path=db_path)
    initial_used = initial_usage["papers_used"]

    with patch("backend.services.gemini_service.GeminiService._call_model", return_value=mock_bad):
        res = ai_extract(
            student_form_bytes=sf_img,
            health_assessment_bytes=ha_img,
            camp_id="camp_phase_test",
            db_path=db_path
        )

        assert res["success"] is False
        assert res.get("status") == "needs_manual"
        # Verify reservation was released on failure
        final_usage = database.get_usage_summary(org_id="org_default", db_path=db_path)
        assert final_usage["papers_used"] == initial_used, "Paper quota must be released on failure"


def test_server_side_flags_and_medical_plausibility():
    """Verify medical plausibility rules for BP, pulse, weight, height, age, RBS, and BMI."""
    # 1. Implausible Blood Pressure (systolic < diastolic or extreme)
    fields_bad_bp = {
        "bp": ["90/120", 0.95],
        "pulse": ["72", 0.95],
        "weight_kg": ["65", 0.95],
        "height_cm": ["170", 0.95],
        "full_name": ["Patient One", 0.95],
        "dob": ["1990-01-01", 0.95]
    }
    flags_bp = apply_server_side_flags(fields_bad_bp)
    assert "bp" in flags_bp["implausible_fields"]
    assert "systolic > diastolic" in flags_bp["flags"]["bp"]

    # 2. Implausible Pulse: 400 bpm (valid: 30-220 bpm)
    fields_bad_pulse = {
        "bp": ["120/80", 0.95],
        "pulse": ["400", 0.95],
        "full_name": ["Patient Two", 0.95],
        "dob": ["1990-01-01", 0.95]
    }
    flags_pulse = apply_server_side_flags(fields_bad_pulse)
    assert "pulse" in flags_pulse["implausible_fields"]

    # 3. Implausible Weight: 0 kg (valid: 1-250 kg)
    fields_bad_weight = {
        "bp": ["120/80", 0.95],
        "weight_kg": ["0", 0.95],
        "height_cm": ["170", 0.95],
        "full_name": ["Patient Three", 0.95],
        "dob": ["1990-01-01", 0.95]
    }
    flags_weight = apply_server_side_flags(fields_bad_weight)
    assert "weight_kg" in flags_weight["implausible_fields"]

    # 4. Implausible RBS: 15 mg/dL and 1200 mg/dL (valid: 20-800 mg/dL)
    fields_bad_rbs = {"rbs": ["15", 0.95], "full_name": ["P", 0.95], "dob": ["1990-01-01", 0.95]}
    flags_rbs = apply_server_side_flags(fields_bad_rbs)
    assert "rbs" in flags_rbs["implausible_fields"]

    # 5. Plausible record: BMI calculation
    fields_clean = {
        "full_name": ["Healthy Patient", 0.99],
        "dob": ["1995-05-15", 0.99],
        "bp": ["120/80", 0.98],
        "pulse": ["72", 0.98],
        "weight_kg": ["65", 0.98],
        "height_cm": ["170", 0.98],
        "rbs": ["95", 0.98]
    }
    flags_clean = apply_server_side_flags(fields_clean)
    assert len(flags_clean["implausible_fields"]) == 0
    assert len(flags_clean["low_confidence_fields"]) == 0
    assert flags_clean["calculated_bmi"] == 22.5  # 65 / (1.7 * 1.7) = 22.49 -> 22.5


def test_escalation_rules_confidence_and_critical():
    """Verify escalation logic:
    - Count ONLY low-confidence fields (< FLAG_CONFIDENCE)
    - NEVER count blank fields with confidence 1
    - Escalate once at most per record
    - Escalate on 3+ low-confidence fields OR any critical measurement low-confidence / implausible
    """
    # 1. Blank fields with confidence 1 must NEVER escalate
    clean_fields = {
        "full_name": ["Patient Clean", 1.0],
        "dob": ["2000-01-01", 1.0],
        "address": [None, 1.0],
        "allergy": [None, 1.0],
        "past_history": [None, 1.0],
        "bp": ["120/80", 0.95]
    }
    flag_analysis = apply_server_side_flags(clean_fields)
    should, reason = should_escalate_record(True, clean_fields, flag_analysis)
    assert not should, f"Blank fields with confidence 1 should never trigger escalation: {reason}"

    # 2. 3 low-confidence fields -> must escalate
    fields_3_low = {
        "field1": ["A", 0.60],
        "field2": ["B", 0.50],
        "field3": ["C", 0.70],
        "bp": ["120/80", 0.95]
    }
    flag_analysis = apply_server_side_flags(fields_3_low)
    should, reason = should_escalate_record(True, fields_3_low, flag_analysis)
    assert should
    assert "3 low-confidence fields" in reason

    # 3. Critical measurement (bp, pulse, weight, rbs, height) low-confidence -> must escalate
    fields_low_bp = {
        "bp": ["120/80", 0.85],  # Critical threshold is 0.90!
        "pulse": ["72", 0.95]
    }
    flag_analysis = apply_server_side_flags(fields_low_bp)
    should, reason = should_escalate_record(True, fields_low_bp, flag_analysis)
    assert should
    assert "bp" in reason

    # 4. Critical measurement implausible -> must escalate
    fields_bad_bp = {
        "bp": ["70/120", 0.95],
        "pulse": ["72", 0.95]
    }
    flag_analysis = apply_server_side_flags(fields_bad_bp)
    should, reason = should_escalate_record(True, fields_bad_bp, flag_analysis)
    assert should
    assert "failed medical plausibility" in reason


# ---------------------------------------------------------------------------
# PHASE 3 TESTS
# ---------------------------------------------------------------------------

def test_atomic_quota_10_parallel_scans_with_3_papers_left(app_and_db):
    """Test 10 parallel scan requests when only 3 papers remain.
    Exactly 3 must succeed with reservation; exactly 7 must receive LIMIT_REACHED / False.
    """
    _, db_path = app_and_db
    test_org = "org_parallel_test"

    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute("""
            INSERT OR REPLACE INTO orgs (id, name, papers_included, addon_papers, papers_used, plan)
            VALUES (?, 'Parallel Test Org', 3, 0, 0, 'starter')
        """, (test_org,))
        conn.commit()

    def try_reserve(idx):
        success, code, papers_left = database.reserve_paper(org_id=test_org, record_id=f"rec_par_{idx}", db_path=db_path)
        return success, papers_left

    # Execute 10 reservations concurrently
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(try_reserve, range(10)))

    successes = [r for r in results if r[0] is True]
    failures = [r for r in results if r[0] is False]

    assert len(successes) == 3, f"Expected exactly 3 reservations to succeed, got {len(successes)}"
    assert len(failures) == 7, f"Expected exactly 7 reservations to fail, got {len(failures)}"

    # Check database state
    org_state = database.get_usage_summary(org_id=test_org, db_path=db_path)
    assert org_state["papers_used"] == 3
    assert org_state["papers_left"] == 0


def test_free_rescan_within_24h_and_image_hash_reuse(app_and_db):
    """Test that:
    1. Duplicate images sent within 24h return cached extraction without consuming paper quota.
    2. Re-scanning the same record within 24h is free and does not charge quota.
    """
    _, db_path = app_and_db
    test_org = "org_hash_test"

    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute("""
            INSERT OR REPLACE INTO orgs (id, name, papers_included, addon_papers, papers_used, plan)
            VALUES (?, 'Hash Test Org', 10, 0, 0, 'starter')
        """, (test_org,))
        conn.commit()

    img1 = _dummy_image(color=(11, 22, 33))
    img2 = _dummy_image(color=(44, 55, 66))

    mock_res = {
        "success": True,
        "data": validate_and_sanitize_extraction({
            "person_name": "Anil Kumble",
            "full_name": "Anil Kumble",
            "dob": "1970-10-17",
            "blood_pressure": "118/78",
            "pulse": "68",
            "weight": "78",
            "height": "182",
            "rbs": "102"
        }),
        "usage": {"tokens_in": 1000, "tokens_out": 200, "tokens_think": 0, "seconds": 1.0, "cost_rs": 0.30}
    }

    # First call: consumes 1 paper
    with patch("backend.services.gemini_service.GeminiService.extract_from_images", return_value=mock_res):
        res1 = ai_extract(img1, img2, camp_id="camp_phase_test", org_id=test_org, db_path=db_path)
        assert res1["success"] is True
        record_id = res1["record_id"]

    state_after_1 = database.get_usage_summary(org_id=test_org, db_path=db_path)
    assert state_after_1["papers_used"] == 1

    # Second call with EXACT SAME images (hash match within 24 hours)
    # Must NOT call AI service and MUST NOT consume paper quota!
    with patch("backend.services.gemini_service.GeminiService.extract_from_images", side_effect=AssertionError("AI should not be called on hash match")):
        res2 = ai_extract(img1, img2, camp_id="camp_phase_test", org_id=test_org, db_path=db_path)
        assert res2["success"] is True
        assert res2.get("cached") is True
        assert res2.get("record_id") == record_id

    state_after_2 = database.get_usage_summary(org_id=test_org, db_path=db_path)
    assert state_after_2["papers_used"] == 1, "Cached image hash reuse must not consume additional quota"

    # Third call: Re-scan of the same record_id within 24 hours is free
    new_img1 = _dummy_image(color=(99, 88, 77))
    with patch("backend.services.gemini_service.GeminiService.extract_from_images", return_value=mock_res):
        res3 = ai_extract(new_img1, img2, camp_id="camp_phase_test", record_id=record_id, org_id=test_org, db_path=db_path)
        assert res3["success"] is True

    state_after_3 = database.get_usage_summary(org_id=test_org, db_path=db_path)
    assert state_after_3["papers_used"] == 1, "Re-scan of same record within 24 hours must be free"


def test_review_blocks_unresolved_flags_and_marks_billed(client, app_and_db):
    """Test explicit approval separation:
    1. Resolving flags alone (/review) must NOT approve or bill paper.
    2. Approval (/approve) with unresolved flags returns 409.
    3. Explicit approval (/approve) sets approved_by, approved_at, status='approved', and bills paper (approve row).
    """
    _, db_path = app_and_db
    rec = database.insert_record({
        "id": "rec_review_flag_test",
        "camp_id": "camp_phase_test",
        "person_name": "Test Unresolved",
        "status": "needs_review",
        "uncertain_fields": ["bp"]
    }, db_path=db_path)

    # 1. Attempt to approve directly with unresolved flags -> 409
    res_approve_fail = client.post(f"/api/records/{rec['id']}/approve", json={
        "approved_by": "Dr. Priya",
        "unresolved_flags": ["bp"]
    })
    assert res_approve_fail.status_code == 409
    assert res_approve_fail.json["error"] == "flags_unresolved"

    # 2. Review record to resolve flags: updates fields and status to reviewed, but does NOT approve or bill!
    res_review = client.put(f"/api/records/{rec['id']}/review", json={
        "reviewed_by": "Dr. Priya",
        "unresolved_flags": [],
        "uncertain_fields": [],
        "blood_pressure": "120/80"
    })
    assert res_review.status_code == 200
    assert res_review.json["status"] == "reviewed"

    # Verify no approve entry in usage_ledger yet (review alone does NOT bill!)
    ledger_before = database.get_usage_ledger_paged(org_id="org_default", db_path=db_path)
    actions_before = [row["action"] for row in ledger_before.get("rows", [])]
    assert "approve" not in actions_before, "Resolving flags alone must not bill paper!"

    # 3. Explicit approval action: stores approved_by, approved_at, and logs 'approve' in ledger
    res_approve = client.post(f"/api/records/{rec['id']}/approve", json={
        "approved_by": "Dr. Priya"
    })
    assert res_approve.status_code == 200
    data = res_approve.json
    assert data["status"] == "approved"
    assert data["approved_by"] == "Dr. Priya"
    assert data["approved_at"] is not None

    # Verify approve entry is now logged in usage_ledger
    ledger_after = database.get_usage_ledger_paged(org_id="org_default", db_path=db_path)
    actions_after = [row["action"] for row in ledger_after.get("rows", [])]
    assert "approve" in actions_after


def test_usage_and_ledger_endpoints(client, app_and_db):
    """Test GET /api/usage and GET /api/usage/ledger endpoints with admin-only protection."""
    _, db_path = app_and_db

    # Log a mock usage entry
    database.log_usage_ledger(
        org_id="org_default",
        record_id="rec_ledger_001",
        action="ai_extract",
        model="gemini-3.6-flash",
        tokens_in=5500,
        tokens_out=800,
        tokens_think=150,
        seconds=1.45,
        cost_rs=1.75,
        db_path=db_path
    )

    # 1. Unauthenticated requests must return 403 Forbidden
    res_usage_no_auth = client.get("/api/usage")
    assert res_usage_no_auth.status_code == 403
    assert res_usage_no_auth.json["error"] == "forbidden"

    res_ledger_no_auth = client.get("/api/usage/ledger")
    assert res_ledger_no_auth.status_code == 403
    assert res_ledger_no_auth.json["error"] == "forbidden"

    # 2. Authenticated admin requests must succeed with 200 OK
    admin_headers = {"X-Admin-Key": "admin_secret_key"}

    res_usage = client.get("/api/usage", headers=admin_headers)
    assert res_usage.status_code == 200
    u_data = res_usage.json
    assert "papers_included" in u_data
    assert "papers_used" in u_data
    assert "papers_left" in u_data
    assert "plan" in u_data

    res_ledger = client.get("/api/usage/ledger", headers=admin_headers)
    assert res_ledger.status_code == 200
    l_data = res_ledger.json
    rows = l_data.get("rows", [])
    assert len(rows) >= 1
    first_entry = rows[0]
    assert first_entry["tokens_in"] == 5500
    assert first_entry["tokens_out"] == 800
    assert first_entry["tokens_think"] == 150
    assert first_entry["seconds"] == 1.45
    assert first_entry["cost_rs"] == 1.75


def test_admin_kill_switch_ai_pause(client, app_and_db):
    """Test POST /api/admin/ai-pause emergency kill switch with admin protection."""
    _, db_path = app_and_db
    sf_img = _dummy_image()
    ha_img = _dummy_image()

    data = {
        "student_form": (io.BytesIO(sf_img), "sf.jpg", "image/jpeg"),
        "health_assessment": (io.BytesIO(ha_img), "ha.jpg", "image/jpeg"),
        "camp_id": "camp_phase_test"
    }

    # 1. Unauthenticated pause request must be rejected with 403
    unauth_pause = client.post("/api/admin/ai-pause", json={"paused": True})
    assert unauth_pause.status_code == 403

    # 2. Activate kill switch with admin credentials
    admin_headers = {"X-Role": "admin"}
    pause_res = client.post("/api/admin/ai-pause", json={"paused": True}, headers=admin_headers)
    assert pause_res.status_code == 200
    assert pause_res.json["paused"] is True

    # 3. Extraction attempt should be blocked with 403 AI_PAUSED
    extract_res = client.post("/api/extract", data=data, content_type="multipart/form-data")
    assert extract_res.status_code == 403
    assert extract_res.json["code"] == "AI_PAUSED"

    # 4. Deactivate kill switch with admin credentials
    unpause_res = client.post("/api/admin/ai-pause", json={"paused": False}, headers=admin_headers)
    assert unpause_res.status_code == 200
    assert unpause_res.json["paused"] is False
