"""Appendix D Acceptance Tests for Phase 4 and Phase 5:
- Scheduled job: releases papers reserved but not approved after RESERVATION_EXPIRY_DAYS (default 7) with ledger row
- Image-hash cache strictly scoped by org_id (multi-tenant privacy)
- Free re-scans limited to 3 per record in 24 hours and logged to usage_ledger
- 5% grace buffer and one-month rollover (capped at 2,500)
- Razorpay order creation
- Signature-checked webhook with HMAC SHA-256
- Strictly idempotent payment credit (duplicate deliveries never double credit)
- ReportLab GST Tax Invoice PDF generation with sequential numbering (INV-2026-0001)
- Invoice list and PDF download endpoints
- Flask-Limiter enforcement: 10/minute per user, 60/minute per org (429 RateLimitExceeded)
- Daily cap enforcement (default 2,000 papers/day, 429 DAILY_CAP_REACHED)
- Per-org emergency kill switch (pausing Org A does not block Org B)
- Concurrent multi-worker atomic reservation integrity
"""

import io
import os
import hmac
import hashlib
import json
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import pytest

from backend.app import create_app
from backend import database
from backend.services.ai_service import ai_extract, validate_and_sanitize_extraction


@pytest.fixture
def app_and_db(tmp_path):
    db_file = str(tmp_path / "test_phase4_phase5.db")
    app = create_app(db_path=db_file)
    app.config["TESTING"] = True
    database.create_camp("Phase 4-5 Camp", 100, "Today", camp_id="camp_p45_test", db_path=db_file)
    return app, db_file


@pytest.fixture
def client(app_and_db):
    app, _ = app_and_db
    with app.test_client() as c:
        yield c


def _dummy_image(color=(120, 150, 180), size=(80, 80)):
    from PIL import Image
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PHASE 1 & 3 FIX VERIFICATIONS
# ---------------------------------------------------------------------------

def test_scheduled_job_releases_expired_reservations_after_7_days(client, app_and_db):
    """Test that:
    1. Reservations older than 7 days that are not approved are released back to org.
    2. An 'expired_release' row is inserted into usage_ledger.
    3. Already approved or recent reservations (<7 days) are NOT released.
    4. Admin endpoint POST /api/admin/cleanup-expired-reservations triggers this job.
    """
    _, db_path = app_and_db
    test_org = "org_expiry_test"

    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute("""
            INSERT OR REPLACE INTO orgs (id, name, papers_included, addon_papers, papers_used, plan)
            VALUES (?, 'Expiry Test Org', 10, 0, 3, 'starter');
        """, (test_org,))
        conn.commit()

    now = datetime.now(timezone.utc)
    eight_days_ago = (now - timedelta(days=8)).isoformat()
    three_days_ago = (now - timedelta(days=3)).isoformat()

    # 1. Old unapproved reservation (8 days ago) -> SHOULD be released
    database.insert_record({
        "id": "rec_old_unapproved",
        "camp_id": "camp_p45_test",
        "person_name": "Old Unapproved",
        "status": "needs_review"
    }, db_path=db_path)
    database.log_usage_ledger(
        org_id=test_org,
        record_id="rec_old_unapproved",
        action="reserve",
        db_path=db_path
    )
    # Manually backdate created_at in usage_ledger
    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute(
            "UPDATE usage_ledger SET created_at = ? WHERE record_id = 'rec_old_unapproved';",
            (eight_days_ago,)
        )
        conn.commit()

    # 2. Old approved reservation (8 days ago) -> SHOULD NOT be released
    database.insert_record({
        "id": "rec_old_approved",
        "camp_id": "camp_p45_test",
        "person_name": "Old Approved",
        "status": "approved",
        "approved_by": "Dr. Priya",
        "approved_at": eight_days_ago
    }, db_path=db_path)
    database.log_usage_ledger(
        org_id=test_org,
        record_id="rec_old_approved",
        action="reserve",
        db_path=db_path
    )
    database.log_usage_ledger(
        org_id=test_org,
        record_id="rec_old_approved",
        action="approve",
        db_path=db_path
    )
    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute(
            "UPDATE usage_ledger SET created_at = ? WHERE record_id = 'rec_old_approved';",
            (eight_days_ago,)
        )
        conn.commit()

    # 3. Recent unapproved reservation (3 days ago) -> SHOULD NOT be released
    database.insert_record({
        "id": "rec_recent_unapproved",
        "camp_id": "camp_p45_test",
        "person_name": "Recent Patient",
        "status": "needs_review"
    }, db_path=db_path)
    database.log_usage_ledger(
        org_id=test_org,
        record_id="rec_recent_unapproved",
        action="reserve",
        db_path=db_path
    )
    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute(
            "UPDATE usage_ledger SET created_at = ? WHERE record_id = 'rec_recent_unapproved';",
            (three_days_ago,)
        )
        conn.commit()

    # Verify admin protection on cleanup endpoint
    unauth_res = client.post("/api/admin/cleanup-expired-reservations")
    assert unauth_res.status_code == 403

    # Run cleanup job via admin API
    res = client.post(
        "/api/admin/cleanup-expired-reservations",
        json={"expiry_days": 7},
        headers={"X-Admin-Key": "admin_secret_key"}
    )
    assert res.status_code == 200
    assert res.json["released_papers"] == 1

    # Verify papers_used decremented on org (was 3, now 2)
    summary = database.get_usage_summary(org_id=test_org, db_path=db_path)
    assert summary["papers_used"] == 2

    # Verify 'expired_release' entry in usage_ledger
    ledger = database.get_usage_ledger_paged(org_id=test_org, db_path=db_path)
    actions = [r["action"] for r in ledger["rows"]]
    assert "expired_release" in actions


def test_image_hash_cache_strictly_scoped_by_org_id(app_and_db):
    """Test that image hash cache is partitioned by org_id (multi-tenant privacy):
    Identical image scanned by Org Alpha must NOT be returned from cache for Org Beta.
    """
    _, db_path = app_and_db
    org_alpha = "org_alpha_clinic"
    org_beta = "org_beta_hospital"

    img1 = _dummy_image(color=(25, 45, 65))
    img2 = _dummy_image(color=(75, 95, 115))

    img_hash = database.compute_scan_hash(img1, img2)
    mock_payload = {
        "success": True,
        "person_name": "Alpha Patient",
        "blood_pressure": "120/80"
    }

    # Save hash cache strictly for Org Alpha
    database.save_scan_hash(img_hash, "rec_alpha_01", org_alpha, mock_payload, db_path=db_path)

    # 1. Org Alpha looks up identical hash -> gets cached result
    cached_alpha = database.get_recent_scan_by_hash(img_hash, org_id=org_alpha, max_hours=24, db_path=db_path)
    assert cached_alpha is not None
    assert cached_alpha["person_name"] == "Alpha Patient"

    # 2. Org Beta looks up identical hash -> must get None (no cross-tenant leakage!)
    cached_beta = database.get_recent_scan_by_hash(img_hash, org_id=org_beta, max_hours=24, db_path=db_path)
    assert cached_beta is None, "Image hash must be strictly scoped by org_id!"


def test_free_rescan_limit_3_per_record_in_24h(app_and_db):
    """Test that free re-scans are capped at 3 per record within 24 hours,
    logging each 'free_rescan' to usage_ledger, and the 4th re-scan is charged.
    """
    _, db_path = app_and_db
    test_org = "org_rescan_limit_test"

    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute("""
            INSERT OR REPLACE INTO orgs (id, name, papers_included, addon_papers, papers_used, plan)
            VALUES (?, 'Rescan Limit Org', 2, 0, 0, 'starter');
        """, (test_org,))
        conn.commit()

    img1 = _dummy_image(color=(101, 102, 103))
    img2 = _dummy_image(color=(201, 202, 203))

    mock_res = {
        "success": True,
        "data": validate_and_sanitize_extraction({
            "person_name": "Sachin Tendulkar",
            "full_name": "Sachin Tendulkar",
            "dob": "1973-04-24",
            "blood_pressure": "120/80",
            "pulse": "72",
            "weight": "70",
            "height": "165",
            "rbs": "100"
        }),
        "usage": {"tokens_in": 1000, "tokens_out": 200, "tokens_think": 0, "seconds": 1.0, "cost_rs": 0.25}
    }

    # Initial scan (consumes 1 paper quota)
    with patch("backend.services.gemini_service.GeminiService.extract_from_images", return_value=mock_res):
        res0 = ai_extract(img1, img2, camp_id="camp_p45_test", org_id=test_org, db_path=db_path)
        assert res0["success"] is True
        record_id = res0["record_id"]

    org_s0 = database.get_usage_summary(org_id=test_org, db_path=db_path)
    assert org_s0["papers_used"] == 1

    # Re-scan 1 (free 1/3)
    img_alt1 = _dummy_image(color=(1, 2, 3))
    with patch("backend.services.gemini_service.GeminiService.extract_from_images", return_value=mock_res):
        res1 = ai_extract(img_alt1, img2, camp_id="camp_p45_test", record_id=record_id, org_id=test_org, db_path=db_path)
        assert res1["success"] is True
    assert database.get_record_rescan_count(record_id, db_path=db_path) == 1
    assert database.get_usage_summary(org_id=test_org, db_path=db_path)["papers_used"] == 1

    # Re-scan 2 (free 2/3)
    img_alt2 = _dummy_image(color=(4, 5, 6))
    with patch("backend.services.gemini_service.GeminiService.extract_from_images", return_value=mock_res):
        res2 = ai_extract(img_alt2, img2, camp_id="camp_p45_test", record_id=record_id, org_id=test_org, db_path=db_path)
        assert res2["success"] is True
    assert database.get_record_rescan_count(record_id, db_path=db_path) == 2
    assert database.get_usage_summary(org_id=test_org, db_path=db_path)["papers_used"] == 1

    # Re-scan 3 (free 3/3)
    img_alt3 = _dummy_image(color=(7, 8, 9))
    with patch("backend.services.gemini_service.GeminiService.extract_from_images", return_value=mock_res):
        res3 = ai_extract(img_alt3, img2, camp_id="camp_p45_test", record_id=record_id, org_id=test_org, db_path=db_path)
        assert res3["success"] is True
    assert database.get_record_rescan_count(record_id, db_path=db_path) == 3
    assert database.get_usage_summary(org_id=test_org, db_path=db_path)["papers_used"] == 1

    # Re-scan 4: Exceeds 3 free re-scans! Must consume 2nd paper from quota
    img_alt4 = _dummy_image(color=(10, 11, 12))
    with patch("backend.services.gemini_service.GeminiService.extract_from_images", return_value=mock_res):
        res4 = ai_extract(img_alt4, img2, camp_id="camp_p45_test", record_id=record_id, org_id=test_org, db_path=db_path)
        assert res4["success"] is True
    assert database.get_usage_summary(org_id=test_org, db_path=db_path)["papers_used"] == 2

    # Re-scan 5: Quota is now fully exhausted (2/2 papers used). Must fail with 402 LIMIT_REACHED!
    img_alt5 = _dummy_image(color=(13, 14, 15))
    with patch("backend.services.gemini_service.GeminiService.extract_from_images", return_value=mock_res):
        res5 = ai_extract(img_alt5, img2, camp_id="camp_p45_test", record_id=record_id, org_id=test_org, db_path=db_path)
        assert res5["success"] is False
        assert res5["code"] == "LIMIT_REACHED"


def test_grace_buffer_5_percent_and_monthly_rollover(app_and_db):
    """Test:
    1. 5% grace buffer allows reservations up to papers_included + 5%.
    2. Monthly rollover caps unused papers at 2,500 and resets papers_used.
    """
    _, db_path = app_and_db
    test_org = "org_grace_test"

    # Org with 100 papers included: 5% grace = 5 papers. Effective limit = 105.
    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute("""
            INSERT OR REPLACE INTO orgs (id, name, papers_included, addon_papers, papers_used, plan)
            VALUES (?, 'Grace Test Org', 100, 0, 100, 'starter');
        """, (test_org,))
        conn.commit()

    # 1. 101st reservation succeeds within 5% grace buffer
    ok1, code1, left1 = database.reserve_paper(test_org, "rec_g1", db_path=db_path)
    assert ok1 is True

    # 2. Consume up to 105
    for i in range(102, 106):
        ok, _, _ = database.reserve_paper(test_org, f"rec_g{i}", db_path=db_path)
        assert ok is True

    # 3. 106th reservation exceeds 5% grace buffer -> fails with limit_reached
    ok_exceed, code_exceed, _ = database.reserve_paper(test_org, "rec_g_exceed", db_path=db_path)
    assert ok_exceed is False
    assert code_exceed == "limit_reached"

    # 4. Test 1-month rollover with cap 2,500
    rollover_org = "org_rollover_test"
    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute("""
            INSERT OR REPLACE INTO orgs (id, name, papers_included, addon_papers, papers_used, rollover_papers, plan)
            VALUES (?, 'Rollover Org', 10000, 0, 6000, 100, 'outreach_10k');
        """, (rollover_org,))
        conn.commit()

    # Unused is 10,000 - 6,000 = 4,000. Cap is 2,500!
    updated_org = database.process_monthly_rollover(rollover_org, db_path=db_path)
    assert updated_org["rollover_papers"] == 2500, "Rollover must be capped at 2,500"
    assert updated_org["papers_used"] == 0, "papers_used must be reset to 0 upon rollover"


# ---------------------------------------------------------------------------
# PHASE 4: RAZORPAY ORDERS, WEBHOOK, IDEMPOTENT CREDIT & GST INVOICES
# ---------------------------------------------------------------------------

def test_razorpay_order_creation_endpoint(client, app_and_db):
    """Test POST /api/billing/order creates order with correct amounts in paise."""
    res = client.post("/api/billing/order", json={"papers": 1000, "org_id": "org_default"})
    assert res.status_code == 200
    data = res.json
    assert "order_id" in data
    assert data["papers"] == 1000
    assert data["amount_rs"] == 5000.0
    assert data["gst_rs"] == 900.0
    assert data["total_rs"] == 5900.0
    assert data["amount_paise"] == 590000
    assert data["currency"] == "INR"


def test_razorpay_webhook_signature_and_idempotent_credit(client, app_and_db):
    """Test:
    1. Webhook rejects requests with missing or invalid HMAC SHA-256 signature (400).
    2. Valid signature credits addon_papers and generates invoice.
    3. Duplicate webhook delivery is strictly idempotent (no double credits).
    """
    _, db_path = app_and_db
    test_org = "org_webhook_test"
    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute("""
            INSERT OR REPLACE INTO orgs (id, name, papers_included, addon_papers, papers_used, plan)
            VALUES (?, 'Webhook Org', 1000, 0, 0, 'starter');
        """, (test_org,))
        conn.commit()

    webhook_secret = "mock_webhook_secret"
    payload_obj = {
        "event": "payment.captured",
        "org_id": test_org,
        "razorpay_payment_id": "pay_test_unique_999",
        "papers": 1000
    }
    raw_body = json.dumps(payload_obj).encode("utf-8")

    # 1. Invalid signature -> 400
    res_bad_sig = client.post(
        "/api/billing/webhook",
        data=raw_body,
        content_type="application/json",
        headers={"X-Razorpay-Signature": "invalid_signature_hex"}
    )
    assert res_bad_sig.status_code == 400

    # 2. Valid signature -> 200 and credits 1,000 addon papers
    valid_sig = hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    res_ok = client.post(
        "/api/billing/webhook",
        data=raw_body,
        content_type="application/json",
        headers={"X-Razorpay-Signature": valid_sig}
    )
    assert res_ok.status_code == 200
    assert res_ok.json["success"] is True

    summary = database.get_usage_summary(org_id=test_org, db_path=db_path)
    assert summary["addon_papers"] == 1000

    # 3. Duplicate delivery with same payment_id -> MUST BE IDEMPOTENT (addon_papers remains 1000)
    res_dup = client.post(
        "/api/billing/webhook",
        data=raw_body,
        content_type="application/json",
        headers={"X-Razorpay-Signature": valid_sig}
    )
    assert res_dup.status_code == 200
    assert res_dup.json["result"]["idempotent"] is True

    summary_dup = database.get_usage_summary(org_id=test_org, db_path=db_path)
    assert summary_dup["addon_papers"] == 1000, "Duplicate payment must never double-credit quota!"


def test_sequential_gst_invoice_pdf_generation_and_download(client, app_and_db):
    """Test ReportLab PDF generation, sequential numbering (INV-2026-0001, INV-2026-0002),
    and invoice download endpoint.
    """
    _, db_path = app_and_db
    test_org = "org_inv_seq_test"
    database.get_or_create_org(org_id=test_org, name="Seq Test Org", db_path=db_path)

    # Credit payment 1
    _, res1 = database.process_successful_payment(
        org_id=test_org,
        payment_id="pay_inv_01",
        papers=1000,
        amount_rs=5000.0,
        gst_rs=900.0,
        db_path=db_path
    )
    inv1_num = res1["invoice_number"]

    # Credit payment 2
    _, res2 = database.process_successful_payment(
        org_id=test_org,
        payment_id="pay_inv_02",
        papers=2500,
        amount_rs=11250.0,
        gst_rs=2025.0,
        db_path=db_path
    )
    inv2_num = res2["invoice_number"]

    # Sequential verification
    assert inv1_num.endswith("0001")
    assert inv2_num.endswith("0002")

    # PDF file exists and is valid
    pdf1_path = res1["pdf_path"]
    assert os.path.exists(pdf1_path)
    with open(pdf1_path, "rb") as f:
        header = f.read(5)
        assert header == b"%PDF-", "Generated file must be a valid PDF"

    # List invoices endpoint
    res_list = client.get("/api/billing/invoices", headers={"X-Org-ID": test_org})
    assert res_list.status_code == 200
    invoices = res_list.json["invoices"]
    assert len(invoices) >= 2
    assert invoices[0]["number"] == inv2_num
    assert invoices[1]["number"] == inv1_num

    # Download invoice PDF endpoint
    res_dl = client.get(f"/api/billing/invoices/{inv1_num}/download")
    assert res_dl.status_code == 200
    assert res_dl.mimetype == "application/pdf"
    assert res_dl.data.startswith(b"%PDF-")


# ---------------------------------------------------------------------------
# PHASE 5: FLASK-LIMITER, DAILY CAP & PER-ORG KILL SWITCH
# ---------------------------------------------------------------------------

def test_daily_cap_enforcement_returns_429(app_and_db):
    """Test daily cap enforcement:
    When an organization reaches its daily cap (e.g. 5 papers/day),
    reserve_paper returns False with code 'daily_cap_reached',
    and ai_extract returns HTTP 429.
    """
    _, db_path = app_and_db
    test_org = "org_daily_cap_test"

    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute("""
            INSERT OR REPLACE INTO orgs (id, name, papers_included, addon_papers, papers_used, daily_cap, plan)
            VALUES (?, 'Daily Cap Org', 1000, 0, 0, 3, 'starter');
        """, (test_org,))
        conn.commit()

    # Reserve 3 papers (reaching the daily cap of 3)
    for i in range(3):
        ok, code, _ = database.reserve_paper(test_org, f"rec_d_{i}", db_path=db_path)
        assert ok is True

    # 4th reservation exceeds daily cap -> returns daily_cap_reached
    ok4, code4, _ = database.reserve_paper(test_org, "rec_d_4", db_path=db_path)
    assert ok4 is False
    assert code4 == "daily_cap_reached"

    # ai_extract returns 429 when daily cap is reached
    img1 = _dummy_image(color=(50, 60, 70))
    img2 = _dummy_image(color=(80, 90, 100))
    res = ai_extract(img1, img2, camp_id="camp_p45_test", org_id=test_org, db_path=db_path)
    assert res["success"] is False
    assert res["code"] == "DAILY_CAP_REACHED"
    assert res["http_status"] == 429


def test_per_org_kill_switch_isolation(client, app_and_db):
    """Test that pausing AI intake on Org A does NOT affect Org B."""
    _, db_path = app_and_db
    org_paused = "org_hospital_paused"
    org_active = "org_clinic_active"

    database.get_or_create_org(org_id=org_paused, name="Paused Org", db_path=db_path)
    database.get_or_create_org(org_id=org_active, name="Active Org", db_path=db_path)

    # 1. Admin pauses ONLY org_paused
    pause_res = client.post(
        "/api/admin/ai-pause",
        json={"org_id": org_paused, "paused": True},
        headers={"X-Role": "admin"}
    )
    assert pause_res.status_code == 200

    # Verify database state
    assert database.get_usage_summary(org_id=org_paused, db_path=db_path)["ai_paused"] is True
    assert database.get_usage_summary(org_id=org_active, db_path=db_path)["ai_paused"] is False

    # 2. Scanning for org_paused is blocked with 403 AI_PAUSED
    img1 = _dummy_image(color=(11, 22, 33))
    img2 = _dummy_image(color=(44, 55, 66))
    res_blocked = ai_extract(img1, img2, camp_id="camp_p45_test", org_id=org_paused, db_path=db_path)
    assert res_blocked["success"] is False
    assert res_blocked["code"] == "AI_PAUSED"

    # 3. Scanning for org_active proceeds normally
    mock_res = {
        "success": True,
        "data": validate_and_sanitize_extraction({"person_name": "Active Patient", "full_name": "Active Patient"}),
        "usage": {"tokens_in": 800, "tokens_out": 200, "tokens_think": 0, "seconds": 0.8, "cost_rs": 0.20}
    }
    with patch("backend.services.gemini_service.GeminiService.extract_from_images", return_value=mock_res):
        res_active = ai_extract(img1, img2, camp_id="camp_p45_test", org_id=org_active, db_path=db_path)
        assert res_active["success"] is True


def test_concurrent_multi_worker_atomic_reservation_integrity(app_and_db):
    """Re-run 10 parallel scans test across workers with 3 papers left to ensure
    zero over-allocation under concurrent load.
    """
    _, db_path = app_and_db
    test_org = "org_concurrency_p45"

    with database.get_db_connection(db_path) as conn:
        conn.cursor().execute("""
            INSERT OR REPLACE INTO orgs (id, name, papers_included, addon_papers, papers_used, plan)
            VALUES (?, 'Concurrency Test Org', 3, 0, 0, 'starter');
        """, (test_org,))
        conn.commit()

    def try_reserve(idx):
        success, code, papers_left = database.reserve_paper(
            org_id=test_org,
            record_id=f"rec_concurrent_{idx}",
            db_path=db_path
        )
        return success, papers_left

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(try_reserve, range(10)))

    successes = [r for r in results if r[0] is True]
    failures = [r for r in results if r[0] is False]

    # Exactly 3 succeed, 7 fail
    assert len(successes) == 3
    assert len(failures) == 7

    summary = database.get_usage_summary(org_id=test_org, db_path=db_path)
    assert summary["papers_used"] == 3
    assert summary["papers_left"] == 0


def test_flask_limiter_enforcement_returns_429(client, app_and_db):
    """Test Flask-Limiter enforcement:
    10 requests/minute per user on /api/extract. Exceeding 10/min returns HTTP 429.
    """
    sf_img = _dummy_image()
    ha_img = _dummy_image()

    mock_res = {
        "success": True,
        "data": validate_and_sanitize_extraction({"person_name": "Rate Patient", "full_name": "Rate Patient"}),
        "usage": {"tokens_in": 500, "tokens_out": 100, "tokens_think": 0, "seconds": 0.5, "cost_rs": 0.10}
    }

    user_headers = {"X-User-ID": "dr_rate_limit_test", "X-Org-ID": "org_rate_test"}
    with patch("backend.services.gemini_service.GeminiService.extract_from_images", return_value=mock_res):
        status_codes = []
        for i in range(12):
            data_req = {
                "student_form": (io.BytesIO(sf_img), "sf.jpg", "image/jpeg"),
                "health_assessment": (io.BytesIO(ha_img), "ha.jpg", "image/jpeg"),
                "camp_id": "camp_p45_test"
            }
            res = client.post("/api/extract", data=data_req, content_type="multipart/form-data", headers=user_headers)
            status_codes.append(res.status_code)

    assert 429 in status_codes, f"Expected 429 rate limit response, got: {status_codes}"

