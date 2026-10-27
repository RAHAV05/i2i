import os
import json
import tempfile
import pytest
from backend import database
from backend.services.ai_service import (
    ADULT_EXTRACTION_PROMPT,
    PAEDIATRIC_EXTRACTION_PROMPT,
    get_prompt_for_target_group,
    extract as ai_extract
)
from backend.services.record_service import RecordService, sanitize_csv_cell
from backend.app import create_app


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database.init_db(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def test_client(temp_db):
    app = create_app(db_path=temp_db)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# ============================================================================
# 1. Camp Target Group Designation & Persistence
# ============================================================================

def test_camp_target_group_paediatric(temp_db):
    camp = database.create_camp(
        name="St. Mary's School Annual Health Screening",
        expected_records=250,
        date_str="2026-10-27",
        location="Chennai Central",
        target_group="paediatric",
        db_path=temp_db
    )
    assert camp["id"].startswith("camp_")
    assert camp["name"] == "St. Mary's School Annual Health Screening"
    assert camp["target_group"] == "paediatric"

    fetched = database.get_camp_by_id(camp["id"], db_path=temp_db)
    assert fetched is not None
    assert fetched["target_group"] == "paediatric"

    camps_list = database.list_camps_with_stats(db_path=temp_db)
    matching = [c for c in camps_list if c["id"] == camp["id"]]
    assert len(matching) == 1
    assert matching[0]["target_group"] == "paediatric"


def test_camp_target_group_adult_default(temp_db):
    camp = database.create_camp(
        name="Infosys Corporate Wellness Camp",
        expected_records=150,
        db_path=temp_db
    )
    assert camp["target_group"] == "adult"

    fetched = database.get_camp_by_id(camp["id"], db_path=temp_db)
    assert fetched["target_group"] == "adult"


# ============================================================================
# 2. Segregated Database Routing (adult_records vs paediatric_records)
# ============================================================================

def test_segregated_paediatric_record_insertion(temp_db):
    p_camp = database.create_camp(
        name="Green Valley Public School",
        expected_records=100,
        target_group="paediatric",
        db_path=temp_db
    )

    rec_data = {
        "camp_id": p_camp["id"],
        "person_name": "Aarav Sharma",
        "age": "10",
        "gender": "Male",
        "height": "138",
        "weight": "34",
        "blood_pressure": "104/66",
        "spinal_caliper_atr": 6.5,
        "school_name": "Green Valley Public School",
        "class_and_section": "Class 5B",
        "admission_id": "GV-2026-501",
        "parent_guardian_name": "Rajesh Sharma",
        "emergency_contact_phone": "+91 98401 23456",
        "dental_caries": "Caries lower right molar",
        "paediatric_alerts": ["ATR 6.5° >= 5°: Potential adolescent idiopathic scoliosis"],
        "structured_record": {
            "personal_information": {
                "full_name": "Aarav Sharma",
                "institution": "Green Valley Public School",
                "course": "Class 5",
                "section": "B"
            },
            "paediatric_growth": {
                "spinal_caliper_atr": 6.5
            }
        }
    }

    inserted = database.insert_record(rec_data, db_path=temp_db)
    assert inserted["id"].startswith("rec_")
    assert inserted["person_name"] == "Aarav Sharma"

    # Verify present in segregated paediatric_records table
    paed_rec = database.get_paediatric_record_by_id(inserted["id"], db_path=temp_db)
    assert paed_rec is not None
    assert paed_rec["person_name"] == "Aarav Sharma"

    # Verify present in list_paediatric_records
    paed_list = database.list_paediatric_records(camp_id=p_camp["id"], db_path=temp_db)
    assert len(paed_list) == 1
    assert paed_list[0]["id"] == inserted["id"]

    # Verify NOT in list_adult_records
    adult_list = database.list_adult_records(camp_id=p_camp["id"], db_path=temp_db)
    assert len(adult_list) == 0

    # Verify unified records table has is_paediatric = 1 and target_group = 'paediatric'
    with database.get_db_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT target_group, is_paediatric FROM records WHERE id = ?;", (inserted["id"],))
        row = cursor.fetchone()
        assert row["target_group"] == "paediatric"
        assert row["is_paediatric"] == 1


def test_segregated_adult_record_insertion(temp_db):
    a_camp = database.create_camp(
        name="TCS Tech Park Outreach",
        expected_records=200,
        target_group="adult",
        db_path=temp_db
    )

    rec_data = {
        "camp_id": a_camp["id"],
        "person_name": "Vikram Raman",
        "age": "38",
        "gender": "Male",
        "height": "175",
        "weight": "78",
        "blood_pressure": "128/82",
        "marital_status": "Married",
        "no_of_children": "2",
        "structured_record": {
            "personal_information": {
                "full_name": "Vikram Raman",
                "marital_status": "Married"
            }
        }
    }

    inserted = database.insert_record(rec_data, db_path=temp_db)
    assert inserted["id"].startswith("rec_")

    # Verify present in segregated adult_records table
    adult_rec = database.get_adult_record_by_id(inserted["id"], db_path=temp_db)
    assert adult_rec is not None
    assert adult_rec["person_name"] == "Vikram Raman"

    # Verify present in list_adult_records
    adult_list = database.list_adult_records(camp_id=a_camp["id"], db_path=temp_db)
    assert len(adult_list) == 1
    assert adult_list[0]["id"] == inserted["id"]

    # Verify NOT in list_paediatric_records
    paed_list = database.list_paediatric_records(camp_id=a_camp["id"], db_path=temp_db)
    assert len(paed_list) == 0


# ============================================================================
# 3. Lifecycle Synchronization (Reviews, Approvals, Erasure, Cascade Delete)
# ============================================================================

def test_segregated_lifecycle_sync(temp_db):
    camp = database.create_camp("Vidya Mandir School", 50, target_group="paediatric", db_path=temp_db)
    rec = database.insert_record({
        "camp_id": camp["id"],
        "person_name": "Meera Iyer",
        "age": "8",
        "gender": "Female",
        "height": "125",
        "weight": "24",
        "target_group": "paediatric"
    }, db_path=temp_db)

    # 1. Update & Review
    updated = database.update_reviewed_record(
        rec["id"],
        {"person_name": "Meera Sundaram Iyer", "height": "126", "reviewer_notes": "All clear"},
        reviewer="Dr. Revathi",
        db_path=temp_db
    )
    assert updated["person_name"] == "Meera Sundaram Iyer"
    assert updated["status"] == "reviewed"

    paed_rec = database.get_paediatric_record_by_id(rec["id"], db_path=temp_db)
    assert paed_rec["person_name"] == "Meera Sundaram Iyer"
    assert paed_rec["status"] == "reviewed"

    # 2. Approve
    ok, app_rec, err = database.approve_record(rec["id"], approved_by="Dr. Revathi", db_path=temp_db)
    assert ok is True
    assert app_rec["status"] == "approved"

    paed_app = database.get_paediatric_record_by_id(rec["id"], db_path=temp_db)
    assert paed_app["status"] == "approved"
    assert paed_app["approved_by"] == "Dr. Revathi"

    # 3. Erasure
    deleted = database.erase_patient_record(rec["id"], db_path=temp_db)
    assert deleted is True
    assert database.get_record_by_id(rec["id"], db_path=temp_db) is None
    assert database.get_paediatric_record_by_id(rec["id"], db_path=temp_db) is None


# ============================================================================
# 4. Prompt Dispatch & Automated Paediatric Screening
# ============================================================================

def test_prompt_routing():
    p_prompt = get_prompt_for_target_group("paediatric")
    assert "paediatric health screening record" in p_prompt.lower()
    assert "spinal_caliper_atr" in p_prompt
    assert "class_and_section" in p_prompt
    assert "school_name" in p_prompt

    a_prompt = get_prompt_for_target_group("adult")
    assert a_prompt == ADULT_EXTRACTION_PROMPT
    assert "marital_status" in a_prompt


# ============================================================================
# 5. Specialized Paediatric Excel Export
# ============================================================================

def test_paediatric_excel_export_generation(temp_db):
    camp = database.create_camp("DAV Public School", 100, target_group="paediatric", db_path=temp_db)
    database.insert_record({
        "camp_id": camp["id"],
        "person_name": "Rohit Verma",
        "age": "11",
        "gender": "Male",
        "height": "142",
        "weight": "36",
        "blood_pressure": "106/68",
        "spinal_caliper_atr": 5.5,
        "school_name": "DAV Public School",
        "class_and_section": "6-A",
        "target_group": "paediatric",
        "paediatric_alerts": ["ATR 5.5° >= 5°: Potential scoliosis"]
    }, db_path=temp_db)

    svc = RecordService(db_path=temp_db)
    xlsx_bytes = svc.generate_excel_export(camp_id=camp["id"])
    assert isinstance(xlsx_bytes, bytes)
    assert len(xlsx_bytes) > 2000
    # ZIP magic header for .xlsx
    assert xlsx_bytes[:4] == b"PK\x03\x04"


def test_excel_formula_injection_immunity():
    # Dangerous formula prefixes must be prepended with a quote
    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert sanitize_csv_cell("+cmd|'/c calc'!A0") == "'+cmd|'/c calc'!A0"
    assert sanitize_csv_cell("Normal Text") == "Normal Text"


# ============================================================================
# 6. REST API Endpoints Verification
# ============================================================================

def test_api_camp_creation_and_segregated_records(test_client, temp_db):
    # 1. Create camp with target_group='paediatric' via POST /api/camps
    res = test_client.post("/api/camps", json={
        "name": "Kendriya Vidyalaya School Camp",
        "expected_records": 120,
        "date_str": "2026-10-27",
        "target_group": "paediatric"
    })
    assert res.status_code == 201
    camp = res.get_json()
    assert camp["target_group"] == "paediatric"
    camp_id = camp["id"]

    # 2. Insert record via POST /api/records
    r_res = test_client.post("/api/records", json={
        "camp_id": camp_id,
        "person_name": "Ananya Krishnan",
        "age": "9",
        "gender": "Female",
        "height": "130",
        "weight": "27",
        "target_group": "paediatric"
    })
    assert r_res.status_code == 201
    rec = r_res.get_json()
    rec_id = rec["id"]

    # 3. GET /api/camps/<camp_id>/paediatric-records
    p_res = test_client.get(f"/api/camps/{camp_id}/paediatric-records")
    assert p_res.status_code == 200
    p_data = p_res.get_json()
    assert len(p_data) == 1
    assert p_data[0]["person_name"] == "Ananya Krishnan"

    # 4. GET /api/paediatric-records/<rec_id>
    single_res = test_client.get(f"/api/paediatric-records/{rec_id}")
    assert single_res.status_code == 200
    assert single_res.get_json()["person_name"] == "Ananya Krishnan"
