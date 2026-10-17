"""Integration tests for Flask REST API endpoints."""

import io
import pytest
from backend.app import create_app

@pytest.fixture
def client(tmp_path):
    db_file = str(tmp_path / "test_api.db")
    app = create_app(db_path=db_file)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_health_and_status(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json["status"] == "ok"

    status_res = client.get("/api/status")
    assert status_res.status_code == 200
    assert status_res.json["demo_mode"] is True
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
    assert res.json["message"] == "Demo data reset successfully"
