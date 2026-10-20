"""Integration tests for Flask REST API endpoints."""

import io
import pytest
from backend.app import create_app

@pytest.fixture
def client(tmp_path):
    db_file = str(tmp_path / "test_api.db")
    app = create_app(db_path=db_file)
    app.config["TESTING"] = True
    from backend import database
    database.create_camp("Test Adyar Camp", 100, "Today", camp_id="camp_adyar_01", db_path=db_file)
    with app.test_client() as client:
        yield client

def test_health_and_status(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json["status"] == "ok"

    status_res = client.get("/api/status")
    assert status_res.status_code == 200
    assert "production_mode" in status_res.json
    assert "overall_stats" in status_res.json

def test_camps_endpoints(client):
    # List initial camps (seeded demo camps)
    res = client.get("/api/camps")
    assert res.status_code == 200
    initial_camps = res.json
    assert len(initial_camps) >= 1

    # Create a new camp
    post_res = client.post("/api/camps", json={
        "name": "Kodambakkam Camp",
        "expected_records": 120,
        "date_str": "Tomorrow"
    })
    assert post_res.status_code == 201
    new_camp = post_res.json
    assert new_camp["name"] == "Kodambakkam Camp"
    assert new_camp["expected_records"] == 120

def test_records_endpoints(client):
    camps = client.get("/api/camps").json
    camp_id = camps[0]["id"]

    # Create a record
    create_res = client.post("/api/records", json={
        "camp_id": camp_id,
        "person_name": "Siddharth N",
        "age": "31",
        "blood_pressure": "115/75"
    })
    assert create_res.status_code == 201
    rec = create_res.json
    assert rec["person_name"] == "Siddharth N"
    assert rec["status"] == "needs_review"

    # Review the record
    review_res = client.put(f"/api/records/{rec['id']}/review", json={
        "person_name": "Siddharth Narayanan",
        "age": "31",
        "examination_details": "Vitals normal, fit for duty.",
        "reviewed_by": "Dr. Priya (Demo)"
    })
    assert review_res.status_code == 200
    reviewed_rec = review_res.json
    assert reviewed_rec["status"] == "reviewed"
    assert reviewed_rec["person_name"] == "Siddharth Narayanan"

def test_extract_missing_pages(client):
    res = client.post("/api/extract", data={})
    assert res.status_code == 400
    assert res.json["code"] == "MISSING_PAGES"

def test_extract_invalid_mime(client):
    data = {
        "page1": (io.BytesIO(b"fake txt content"), "test.txt", "text/plain"),
        "page2": (io.BytesIO(b"fake txt content"), "test2.txt", "text/plain"),
    }
    res = client.post("/api/extract", data=data, content_type="multipart/form-data")
    assert res.status_code == 400
    assert res.json["code"] == "INVALID_MIME_TYPE"

def test_export_endpoints(client):
    # Test default Excel export
    res = client.get("/api/export")
    assert res.status_code == 200
    assert res.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert res.data.startswith(b"PK\x03\x04") # Zip signature for .xlsx

    # Test CSV export fallback with format=csv
    res_csv = client.get("/api/export?format=csv")
    assert res_csv.status_code == 200
    assert res_csv.mimetype == "text/csv"
    assert "Record ID" in res_csv.data.decode("utf-8")

def test_reset_demo_endpoint(client):
    res = client.post("/api/reset-demo")
    assert res.status_code == 200
    assert "successfully" in res.json["message"]

def test_record_concurrency_lock_and_unlock(client):
    # Create record
    res = client.post("/api/records", json={
        "camp_id": "camp_adyar_01",
        "person_name": "Lock Test Patient",
        "age": "42"
    })
    rec_id = res.json["id"]

    # Doctor 1 acquires lock
    lock_res1 = client.post(f"/api/records/{rec_id}/lock", json={"doctor_name": "Dr. Priya"})
    assert lock_res1.status_code == 200
    assert lock_res1.json["success"] is True
    assert lock_res1.json["locked_by"] == "Dr. Priya"

    # Doctor 2 attempts lock - should be rejected / return existing lock holder
    lock_res2 = client.post(f"/api/records/{rec_id}/lock", json={"doctor_name": "Dr. Ramesh"})
    assert lock_res2.status_code == 200
    assert lock_res2.json["success"] is False
    assert lock_res2.json["locked_by"] == "Dr. Priya"

    # Doctor 1 releases lock
    unlock_res = client.post(f"/api/records/{rec_id}/unlock", json={"doctor_name": "Dr. Priya"})
    assert unlock_res.status_code == 200
    assert unlock_res.json["unlocked"] is True

    # Now Doctor 2 can acquire lock
    lock_res3 = client.post(f"/api/records/{rec_id}/lock", json={"doctor_name": "Dr. Ramesh"})
    assert lock_res3.status_code == 200
    assert lock_res3.json["success"] is True
    assert lock_res3.json["locked_by"] == "Dr. Ramesh"

def test_check_duplicate_record(client):
    res = client.post("/api/records", json={
        "camp_id": "camp_adyar_01",
        "person_name": "Unique Duplicate Test Subject",
        "age": "29"
    })
    assert res.status_code == 201

    # Check for same name in same camp
    dup_res = client.post("/api/records/check-duplicate", json={
        "camp_id": "camp_adyar_01",
        "person_name": "Unique Duplicate Test Subject"
    })
    assert dup_res.status_code == 200
    assert dup_res.json["has_duplicate"] is True
    assert dup_res.json["count"] >= 1

    # Check for non-existent name
    non_dup = client.post("/api/records/check-duplicate", json={
        "camp_id": "camp_adyar_01",
        "person_name": "Completely Non Existent Person 999"
    })
    assert non_dup.status_code == 200
    assert non_dup.json["has_duplicate"] is False

def test_dpdp_patient_erasure(client):
    # Create record
    res = client.post("/api/records", json={
        "camp_id": "camp_adyar_01",
        "person_name": "Patient To Erase",
        "age": "50"
    })
    rec_id = res.json["id"]

    # Request DPDP erasure
    erase_res = client.post(f"/api/records/{rec_id}/erase")
    assert erase_res.status_code == 200
    assert erase_res.json["erased"] is True

    # Verify record no longer exists
    get_res = client.get(f"/api/records/{rec_id}")
    assert get_res.status_code == 404

def test_tokenized_invite_lifecycle(client):
    # 1. Create single-use invite
    inv_res = client.post("/api/community/invites", json={
        "community_id": "comm_test_01",
        "email": "doctor.test@clinic.org",
        "role": "doctor",
        "expires_hours": 48
    })
    assert inv_res.status_code == 201
    code = inv_res.json["code"]
    inv_id = inv_res.json["id"]
    assert code.startswith("INV-")

    # 2. List invites
    list_res = client.get("/api/community/invites?community_id=comm_test_01")
    assert list_res.status_code == 200
    assert any(i["id"] == inv_id for i in list_res.json["invites"])

    # 3. Verify valid invite
    verify_res = client.post("/api/community/verify-invite", json={"code": code})
    assert verify_res.status_code == 200
    assert verify_res.json["valid"] is True

    # 4. Revoke invite
    del_res = client.delete(f"/api/community/invites/{inv_id}")
    assert del_res.status_code == 200
    assert del_res.json["revoked"] is True

    # 5. Verify revoked invite
    verify_revoked = client.post("/api/community/verify-invite", json={"code": code})
    assert verify_revoked.status_code == 400
    assert verify_revoked.json["valid"] is False

def test_admin_billing_and_credits(client):
    # Get billing details
    bill_res = client.get("/api/admin/billing")
    assert bill_res.status_code == 200
    assert "used_scans" in bill_res.json
    assert "monthly_limit" in bill_res.json
    assert "cost_estimate_inr" in bill_res.json

    # Purchase scan credits
    purch_res = client.post("/api/admin/purchase-credits", json={"credits": 500})
    assert purch_res.status_code == 200
    assert purch_res.json["credits_added"] == 500
    assert purch_res.json["new_limit"] >= 3000

