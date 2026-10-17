"""Unit tests for database layer and SQL aggregations."""

import os
import json
import pytest
from backend import database

@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_clinic.db")
    database.init_db(db_file)
    return db_file

def test_init_db_creates_tables(temp_db):
    with database.get_db_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row["name"] for row in cursor.fetchall()]
        assert "camps" in tables
        assert "records" in tables

def test_create_and_list_camps(temp_db):
    camp = database.create_camp("Test Camp Ambattur", 150, "17 Oct", temp_db)
    assert camp["name"] == "Test Camp Ambattur"
    assert camp["expected_records"] == 150
    assert camp["total_scanned"] == 0

    camps = database.list_camps_with_stats(temp_db)
    assert len(camps) == 1
    assert camps[0]["name"] == "Test Camp Ambattur"

def test_insert_and_count_records(temp_db):
    camp = database.create_camp("Camp A", 50, "Today", temp_db)
    rec1 = database.insert_record({
        "camp_id": camp["id"],
        "person_name": "Ravi Kumar",
        "age": "45",
        "gender": "Male",
        "height": "170 cm",
        "weight": "70 kg",
        "blood_pressure": "120/80",
        "status": "needs_review"
    }, temp_db)
    assert rec1["person_name"] == "Ravi Kumar"
    assert rec1["status"] == "needs_review"

    # Insert a second reviewed record
    database.insert_record({
        "camp_id": camp["id"],
        "person_name": "Ananya Roy",
        "status": "reviewed"
    }, temp_db)

    # Check camp computed stats
    camp_stats = database.get_camp_by_id(camp["id"], temp_db)
    assert camp_stats["total_scanned"] == 2
    assert camp_stats["to_check"] == 1
    assert camp_stats["reviewed"] == 1

def test_reset_demo_data(temp_db):
    database.reset_demo_data(temp_db)
    camps = database.list_camps_with_stats(temp_db)
    assert len(camps) >= 3
    stats = database.get_overall_stats(temp_db)
    assert stats["total_camps"] >= 3
    assert stats["total_scanned"] >= 3
    assert stats["to_check"] >= 1
