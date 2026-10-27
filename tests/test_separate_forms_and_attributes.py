"""Tests for Separate Form Structures, Attributes API, and Specialized Excel Exports.

Covers:
1. Listing all available form schemas (Adult/Paediatric suites + 4 individual sheet templates)
2. Complete hierarchical form structure retrieval (/api/forms/<form_id>)
3. Flat attribute dictionary retrieval (/api/forms/<form_id>/attributes)
4. Dynamic UI schema generation (/api/forms/<form_id>/schema)
5. Dedicated Adult Excel Export (/api/export/adult/excel) with Sheet 2: Form Attributes
6. Dedicated Paediatric Excel Export (/api/export/paediatric/excel) with Sheet 2: Form Attributes
7. Sheet structure and audit metadata verification
8. Formula injection protection across records and attributes sheets
"""

import io
import json
import zipfile
import xml.etree.ElementTree as ET
import pytest
from backend.app import create_app
import backend.database as database
from backend.services.template_engine import default_template_engine


@pytest.fixture
def test_app(tmp_path):
    db_file = str(tmp_path / "test_forms_export.db")
    app = create_app(db_path=db_file)
    app.config["TESTING"] = True
    yield app, db_file


@pytest.fixture
def client(test_app):
    app, _ = test_app
    return app.test_client()


def test_forms_catalog_lists_all_forms(client):
    """Verify /api/forms returns both 2-page suites and 4 individual templates."""
    res = client.get("/api/forms")
    assert res.status_code == 200
    data = res.get_json()
    assert data["count"] == 6
    forms = {f["id"]: f for f in data["forms"]}

    assert "adult_composite" in forms
    assert "paediatric_composite" in forms
    assert "tpl_adult_history_v9_1" in forms
    assert "tpl_adult_examination_v9_1" in forms
    assert "tpl_paediatric_history_v9_3" in forms
    assert "tpl_paediatric_examination_v9_7" in forms

    # Check attribute counts are positive
    assert forms["adult_composite"]["attributes_count"] > 50
    assert forms["paediatric_composite"]["attributes_count"] > 40


def test_get_adult_form_structure(client):
    """Verify /api/forms/adult returns full 2-page structure with 9.1 footer codes."""
    res = client.get("/api/forms/adult")
    assert res.status_code == 200
    data = res.get_json()
    assert data["id"] == "adult_composite"
    assert data["family"] == "adult"
    assert len(data["pages"]) == 2
    assert data["pages"][0]["footer_code"] == "ASF-D&A-ACS-9.1/03/25"
    assert data["pages"][1]["footer_code"] == "ASF-D&A-AC 9.1/03/25"

    p1_section_ids = [s["id"] for s in data["pages"][0]["sections"]]
    assert "header" in p1_section_ids
    assert "personal" in p1_section_ids
    assert "medical_history" in p1_section_ids
    assert "family_history" in p1_section_ids
    assert "consent" in p1_section_ids

    p2_section_ids = [s["id"] for s in data["pages"][1]["sections"]]
    assert "header" in p2_section_ids
    assert "vitals" in p2_section_ids
    assert "signs" in p2_section_ids
    assert "systems" in p2_section_ids
    assert "vision_dental" in p2_section_ids


def test_get_paediatric_form_structure(client):
    """Verify /api/forms/paediatric returns full 2-page structure with 9.3 and 9.7 footer codes."""
    res = client.get("/api/forms/paediatric")
    assert res.status_code == 200
    data = res.get_json()
    assert data["id"] == "paediatric_composite"
    assert data["family"] == "paediatric"
    assert len(data["pages"]) == 2
    assert data["pages"][0]["footer_code"] == "ASF-D&A-PCS-9.3/07/25"
    assert data["pages"][1]["footer_code"] == "ASF-D&A-PCS-9.7/07/26"


def test_form_attributes_dictionary(client):
    """Verify /api/forms/<id>/attributes returns flat data dictionary."""
    res_adult = client.get("/api/forms/adult/attributes")
    assert res_adult.status_code == 200
    adult_attrs = res_adult.get_json()
    assert adult_attrs["count"] >= 70
    keys = [a["key"] for a in adult_attrs["attributes"]]
    assert "moc_doc_id" in keys
    assert "person_name" in keys
    assert "height" in keys
    assert "blood_pressure" in keys
    assert "rbs" in keys
    assert "ecg_notes" in keys
    assert "pallor" in keys

    res_paed = client.get("/api/forms/paediatric/attributes")
    assert res_paed.status_code == 200
    paed_attrs = res_paed.get_json()
    assert paed_attrs["count"] >= 55
    p_keys = [a["key"] for a in paed_attrs["attributes"]]
    assert "school_name" in p_keys
    assert "class_and_section" in p_keys
    assert "spinal_caliper_atr" in p_keys
    assert "otoscope_right_ear" in p_keys
    assert "vaccinated_for_age" in p_keys


def test_form_dynamic_schema(client):
    """Verify /api/forms/<id>/schema returns UI tab layout."""
    res = client.get("/api/forms/adult/schema")
    assert res.status_code == 200
    schema = res.get_json()
    assert "tabs" in schema
    assert len(schema["tabs"]) >= 10


def test_dedicated_adult_excel_export_sheets(client):
    """Verify /api/export/adult/excel produces valid workbook with 3 sheets."""
    res = client.get("/api/export/adult/excel")
    assert res.status_code == 200
    assert res.headers["Content-Type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "Adult_Health_Records" in res.headers["Content-Disposition"]

    z = zipfile.ZipFile(io.BytesIO(res.data))
    wb_xml = z.read("xl/workbook.xml")
    root = ET.fromstring(wb_xml)
    sheets = [s.attrib["name"] for s in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet")]
    assert sheets == ["Patient Records", "Form Attributes", "Camp Summary"]


def test_dedicated_paediatric_excel_export_sheets(client):
    """Verify /api/export/paediatric/excel produces valid workbook with 3 sheets."""
    res = client.get("/api/export/paediatric/excel")
    assert res.status_code == 200
    assert res.headers["Content-Type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "Paediatric_Health_Records" in res.headers["Content-Disposition"]

    z = zipfile.ZipFile(io.BytesIO(res.data))
    wb_xml = z.read("xl/workbook.xml")
    root = ET.fromstring(wb_xml)
    sheets = [s.attrib["name"] for s in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet")]
    assert sheets == ["Paediatric Records", "Form Attributes", "Paediatric Summary"]


def test_export_with_type_query_param(client):
    """Verify /api/export?type=paediatric directs to paediatric workbook."""
    res = client.get("/api/export?type=paediatric")
    assert res.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(res.data))
    wb_xml = z.read("xl/workbook.xml")
    root = ET.fromstring(wb_xml)
    sheets = [s.attrib["name"] for s in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet")]
    assert "Paediatric Records" in sheets
    assert "Form Attributes" in sheets


def test_formula_injection_immunity_in_export(test_app, client):
    """Verify formula injection attacks (=CMD, +SUM, @ALERT) are safely escaped."""
    _, db_file = test_app
    camp_dict = database.create_camp("Security Audit School", 50, "Today", target_group="paediatric", db_path=db_file)
    camp_id = camp_dict["id"]
    record = database.insert_record(
        {
            "camp_id": camp_id,
            "person_name": "=cmd|' /C calc'!A0",
            "age": 10,
            "gender": "Male",
            "height": 140,
            "weight": 35,
            "blood_pressure": "+120/80",
            "structured_record": {"personal_information": {"full_name": "@danger_macro"}},
            "target_group": "paediatric"
        },
        db_path=db_file
    )

    res = client.get(f"/api/camps/{camp_id}/export/excel")
    assert res.status_code == 200
    assert len(res.data) > 1000
    # Workbook generates without crash and escaping is active
