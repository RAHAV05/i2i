"""Unit tests for RecordService: review state transitions, duplicate protection, and CSV injection sanitization."""

import pytest
import io
from backend import database
from backend.services.record_service import RecordService, sanitize_csv_cell

@pytest.fixture
def service(tmp_path):
    db_file = str(tmp_path / "test_records.db")
    database.init_db(db_file)
    return RecordService(db_file)

def test_sanitize_csv_cell_formula_injection():
    # Malicious spreadsheet formula prefixes
    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("+cmd|' /C calc'!A0") == "'+cmd|' /C calc'!A0"
    assert sanitize_csv_cell("-10+5") == "'-10+5"
    assert sanitize_csv_cell("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert sanitize_csv_cell("\tTabPrefix") == "'\tTabPrefix"
    
    # Safe values
    assert sanitize_csv_cell("Normal Patient") == "Normal Patient"
    assert sanitize_csv_cell("120/80") == "120/80"
    assert sanitize_csv_cell("") == ""
    assert sanitize_csv_cell(None) == ""

def test_create_record_validation(service):
    # Non-existent camp
    with pytest.raises(ValueError, match="does not exist"):
        service.create_record({"camp_id": "nonexistent", "person_name": "Test User"})

    # Camp exists
    camp = database.create_camp("Camp South", 100, "Today", service.db_path)
    
    # Missing name
    with pytest.raises(ValueError, match="Person's name is required"):
        service.create_record({"camp_id": camp["id"], "person_name": "  "})

    # Valid creation
    rec = service.create_record({
        "camp_id": camp["id"],
        "person_name": "Deepak Sharma",
        "age": "41"
    })
    assert rec["person_name"] == "Deepak Sharma"
    assert rec["status"] == "needs_review"

def test_idempotent_duplicate_record_prevention(service):
    camp = database.create_camp("Camp North", 100, "Today", service.db_path)
    payload = {
        "id": "rec_fixed_id_123",
        "camp_id": camp["id"],
        "person_name": "Deepak Sharma",
        "age": "41"
    }
    rec1 = service.create_record(payload)
    rec2 = service.create_record(payload)
    assert rec1["id"] == rec2["id"]

    all_recs = database.list_records(camp_id=camp["id"], db_path=service.db_path)
    assert len(all_recs) == 1

def test_review_record_workflow(service):
    camp = database.create_camp("Camp Central", 50, "Today", service.db_path)
    rec = service.create_record({
        "camp_id": camp["id"],
        "person_name": "Lata M",
        "status": "needs_review"
    })
    assert rec["status"] == "needs_review"

    # Review the record
    reviewed = service.review_record(
        rec["id"],
        {
            "person_name": "Lata Mangesh",
            "age": "50",
            "examination_details": "Normal ENT checkup",
            "reviewer_notes": "All vitals cross-checked with physical sheet."
        },
        reviewer="Dr. Priya (Demo)"
    )
    assert reviewed["status"] == "reviewed"
    assert reviewed["person_name"] == "Lata Mangesh"
    assert reviewed["reviewed_by"] == "Dr. Priya (Demo)"
    assert reviewed["reviewed_at"] is not None

def test_csv_export(service):
    camp = database.create_camp("Camp Delta", 50, "Today", service.db_path)
    service.create_record({
        "camp_id": camp["id"],
        "person_name": "=MALICIOUS_FORMULA",
        "blood_pressure": "130/85"
    })
    csv_str = service.generate_csv_export(camp_id=camp["id"])
    assert "Record ID" in csv_str
    assert "Camp ID" in csv_str
    assert "Camp Name" in csv_str
    assert "Person Name" in csv_str
    # Verify formula was sanitized with single quote
    assert "'=MALICIOUS_FORMULA" in csv_str

def test_excel_export(service):
    import zipfile
    camp = database.create_camp("Camp Excel", 80, "Today", service.db_path)
    service.create_record({
        "camp_id": camp["id"],
        "person_name": "Rohan Verma",
        "age": "28",
        "gender": "Male",
        "height": "175 cm",
        "weight": "72 kg",
        "blood_pressure": "120/80"
    })
    excel_bytes = service.generate_excel_export(camp_id=camp["id"])
    assert isinstance(excel_bytes, bytes)
    assert len(excel_bytes) > 1000
    assert excel_bytes.startswith(b"PK\x03\x04")

    # Verify OpenXML worksheets
    zf = zipfile.ZipFile(io.BytesIO(excel_bytes))
    sheet_files = [f for f in zf.namelist() if f.startswith("xl/worksheets/sheet")]
    assert len(sheet_files) >= 2 # Patient Records and Camp Summary
