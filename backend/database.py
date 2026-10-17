"""Database access layer for Doc Scanner - Minimal Clinic Demo MVP.
Uses SQLite for lightweight, zero-dependency demo persistence.
All statistics (total scanned, needs review, reviewed) are computed directly from
records in SQLite, preventing desynchronized counters or fake metrics.
"""

import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    DEFAULT_DB_PATH = "/tmp/clinic_records.db"
else:
    DEFAULT_DB_PATH = os.environ.get("DATABASE_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "clinic_records.db")

def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Return a configured SQLite connection with foreign keys and Row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialize database tables if they do not exist."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Camps table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS camps (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                date_str TEXT NOT NULL,
                expected_records INTEGER NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL
            );
        """)
        
        # Records table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY,
                camp_id TEXT NOT NULL,
                person_name TEXT NOT NULL,
                age TEXT,
                gender TEXT,
                height TEXT,
                weight TEXT,
                blood_pressure TEXT,
                examination_details TEXT,
                referral_details TEXT,
                missing_fields TEXT NOT NULL DEFAULT '[]',
                uncertain_fields TEXT NOT NULL DEFAULT '[]',
                extraction_notes TEXT,
                structured_record TEXT,
                status TEXT NOT NULL CHECK(status IN ('needs_review', 'reviewed', 'failed')),
                page1_preview TEXT,
                page2_preview TEXT,
                reviewer_notes TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (camp_id) REFERENCES camps(id) ON DELETE CASCADE
            );
        """)
        
        # Migration: ensure structured_record column exists on existing databases
        cursor.execute("PRAGMA table_info(records);")
        cols = [r["name"] for r in cursor.fetchall()]
        if "structured_record" not in cols:
            cursor.execute("ALTER TABLE records ADD COLUMN structured_record TEXT;")
            
        # Index for fast filtering by camp and status
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_records_camp ON records(camp_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);")
        conn.commit()

def reset_demo_data(db_path: str = DEFAULT_DB_PATH) -> None:
    """Clear all tables and seed realistic, synthetic demo records.
    Never uses real patient data. Clearly labeled as DEMO data.
    """
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    
    sample_camps = [
        {
            "id": "camp_adyar_01",
            "name": "St. Mary's School, Adyar (Demo)",
            "date_str": "16 Oct",
            "expected_records": 120,
            "created_at": now
        },
        {
            "id": "camp_velachery_02",
            "name": "Velachery Community Hall (Demo)",
            "date_str": "12 Oct",
            "expected_records": 90,
            "created_at": now
        },
        {
            "id": "camp_annanagar_03",
            "name": "Anna Nagar Sports Club (Demo)",
            "date_str": "20 Oct",
            "expected_records": 60,
            "created_at": now
        }
    ]
    
    sample_records = [
        {
            "id": "rec_demo_001",
            "camp_id": "camp_adyar_01",
            "person_name": "Meena S (Synthetic)",
            "age": "34",
            "gender": "Female",
            "height": "158 cm",
            "weight": "54 kg",
            "blood_pressure": "118/76 mmHg",
            "examination_details": "Normal respiratory sounds. Dental caries noted on lower left molar.",
            "referral_details": "Referred to Dental OPD for filling.",
            "missing_fields": json.dumps([]),
            "uncertain_fields": json.dumps([]),
            "extraction_notes": "Both pages legible.",
            "status": "reviewed",
            "page1_preview": None,
            "page2_preview": None,
            "reviewer_notes": "Verified by camp doctor. Dental follow-up advised.",
            "reviewed_by": "Dr. Priya (Demo)",
            "reviewed_at": now,
            "created_at": now,
            "updated_at": now
        },
        {
            "id": "rec_demo_002",
            "camp_id": "camp_adyar_01",
            "person_name": "Arjun K (Synthetic)",
            "age": "48",
            "gender": "Male",
            "height": "172 cm",
            "weight": "79 kg",
            "blood_pressure": "142/92 mmHg",
            "examination_details": "Elevated blood pressure observed twice during camp checkup.",
            "referral_details": "Referred to Primary Health Centre for Hypertension workup.",
            "missing_fields": json.dumps([]),
            "uncertain_fields": json.dumps(["blood_pressure"]),
            "extraction_notes": "Second digit of systolic reading was slightly faint on Page 1.",
            "status": "needs_review",
            "page1_preview": None,
            "page2_preview": None,
            "reviewer_notes": None,
            "reviewed_by": None,
            "reviewed_at": None,
            "created_at": now,
            "updated_at": now
        },
        {
            "id": "rec_demo_003",
            "camp_id": "camp_adyar_01",
            "person_name": "Divya P (Synthetic)",
            "age": "22",
            "gender": "Female",
            "height": "162 cm",
            "weight": "49 kg",
            "blood_pressure": "110/70 mmHg",
            "examination_details": "Mild pallor noted on conjunctiva. Otherwise unremarkable.",
            "referral_details": "Advised iron-rich diet and routine CBC test.",
            "missing_fields": json.dumps([]),
            "uncertain_fields": json.dumps([]),
            "extraction_notes": "Clean scan, fully readable.",
            "status": "reviewed",
            "page1_preview": None,
            "page2_preview": None,
            "reviewer_notes": "Checked and approved.",
            "reviewed_by": "Dr. Priya (Demo)",
            "reviewed_at": now,
            "created_at": now,
            "updated_at": now
        }
    ]
    
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM records;")
        cursor.execute("DELETE FROM camps;")
        
        for c in sample_camps:
            cursor.execute("""
                INSERT INTO camps (id, name, date_str, expected_records, created_at)
                VALUES (?, ?, ?, ?, ?);
            """, (c["id"], c["name"], c["date_str"], c["expected_records"], c["created_at"]))
            
        for r in sample_records:
            cursor.execute("""
                INSERT INTO records (
                    id, camp_id, person_name, age, gender, height, weight,
                    blood_pressure, examination_details, referral_details,
                    missing_fields, uncertain_fields, extraction_notes,
                    status, page1_preview, page2_preview, reviewer_notes,
                    reviewed_by, reviewed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                r["id"], r["camp_id"], r["person_name"], r["age"], r["gender"],
                r["height"], r["weight"], r["blood_pressure"], r["examination_details"],
                r["referral_details"], r["missing_fields"], r["uncertain_fields"],
                r["extraction_notes"], r["status"], r["page1_preview"], r["page2_preview"],
                r["reviewer_notes"], r["reviewed_by"], r["reviewed_at"],
                r["created_at"], r["updated_at"]
            ))
        conn.commit()

# --- Camp Queries ---

def list_camps_with_stats(db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Return all camps with computed counts for scanned, to_check, and reviewed records."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                c.id,
                c.name,
                c.date_str,
                c.expected_records,
                c.created_at,
                COUNT(r.id) AS total_scanned,
                SUM(CASE WHEN r.status = 'needs_review' THEN 1 ELSE 0 END) AS to_check,
                SUM(CASE WHEN r.status = 'reviewed' THEN 1 ELSE 0 END) AS reviewed
            FROM camps c
            LEFT JOIN records r ON c.id = r.camp_id
            GROUP BY c.id
            ORDER BY c.created_at DESC;
        """)
        rows = cursor.fetchall()
        camps = []
        for row in rows:
            expected = row["expected_records"]
            scanned = row["total_scanned"] or 0
            to_check = row["to_check"] or 0
            reviewed = row["reviewed"] or 0
            pct = round((scanned / expected * 100), 1) if expected > 0 else 0
            camps.append({
                "id": row["id"],
                "name": row["name"],
                "date_str": row["date_str"],
                "expected_records": expected,
                "total_scanned": scanned,
                "to_check": to_check,
                "reviewed": reviewed,
                "progress_pct": min(100.0, pct),
                "created_at": row["created_at"]
            })
        return camps

def get_camp_by_id(camp_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Fetch a single camp with computed stats."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                c.id, c.name, c.date_str, c.expected_records, c.created_at,
                COUNT(r.id) AS total_scanned,
                SUM(CASE WHEN r.status = 'needs_review' THEN 1 ELSE 0 END) AS to_check,
                SUM(CASE WHEN r.status = 'reviewed' THEN 1 ELSE 0 END) AS reviewed
            FROM camps c
            LEFT JOIN records r ON c.id = r.camp_id
            WHERE c.id = ?
            GROUP BY c.id;
        """, (camp_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "date_str": row["date_str"],
            "expected_records": row["expected_records"],
            "total_scanned": row["total_scanned"] or 0,
            "to_check": row["to_check"] or 0,
            "reviewed": row["reviewed"] or 0,
            "created_at": row["created_at"]
        }

def create_camp(name: str, expected_records: int, date_str: str = "Today", db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Create a new camp and persist it."""
    init_db(db_path)
    camp_id = f"camp_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO camps (id, name, date_str, expected_records, created_at)
            VALUES (?, ?, ?, ?, ?);
        """, (camp_id, name.strip(), date_str, max(1, expected_records), now))
        conn.commit()
    return get_camp_by_id(camp_id, db_path) # type: ignore

# --- Record Queries ---

def list_records(camp_id: Optional[str] = None, status: Optional[str] = None, limit: int = 50, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """List records optionally filtered by camp_id and/or status."""
    init_db(db_path)
    query = """
        SELECT r.*, c.name AS camp_name 
        FROM records r 
        JOIN camps c ON r.camp_id = c.id
    """
    params: List[Any] = []
    conditions = []
    if camp_id:
        conditions.append("r.camp_id = ?")
        params.append(camp_id)
    if status:
        conditions.append("r.status = ?")
        params.append(status)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY r.created_at DESC LIMIT ?;"
    params.append(limit)
    
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        results = []
        for row in rows:
            results.append(_format_record_row(row))
        return results

def get_record_by_id(record_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Fetch a single record by its stable ID."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.*, c.name AS camp_name 
            FROM records r 
            JOIN camps c ON r.camp_id = c.id
            WHERE r.id = ?;
        """, (record_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return _format_record_row(row)

def insert_record(record_data: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Insert a new record, ensuring stable unique ID and default status."""
    init_db(db_path)
    rec_id = record_data.get("id") or f"rec_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"
    now = datetime.now(timezone.utc).isoformat()
    
    missing_json = json.dumps(record_data.get("missing_fields", []))
    uncertain_json = json.dumps(record_data.get("uncertain_fields", []))
    structured_json = json.dumps(record_data.get("structured_record", {})) if record_data.get("structured_record") is not None else None
    status = record_data.get("status", "needs_review")
    
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO records (
                id, camp_id, person_name, age, gender, height, weight,
                blood_pressure, examination_details, referral_details,
                missing_fields, uncertain_fields, extraction_notes, structured_record,
                status, page1_preview, page2_preview, reviewer_notes,
                reviewed_by, reviewed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            rec_id,
            record_data["camp_id"],
            record_data.get("person_name") or "Unknown Patient",
            record_data.get("age"),
            record_data.get("gender"),
            record_data.get("height"),
            record_data.get("weight"),
            record_data.get("blood_pressure"),
            record_data.get("examination_details"),
            record_data.get("referral_details"),
            missing_json,
            uncertain_json,
            record_data.get("extraction_notes"),
            structured_json,
            status,
            record_data.get("page1_preview"),
            record_data.get("page2_preview"),
            record_data.get("reviewer_notes"),
            record_data.get("reviewed_by"),
            record_data.get("reviewed_at"),
            now,
            now
        ))
        conn.commit()
    return get_record_by_id(rec_id, db_path) # type: ignore

def update_reviewed_record(record_id: str, update_data: Dict[str, Any], reviewer: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Update fields and set status to 'reviewed' upon explicit doctor/staff review."""
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    
    missing_json = json.dumps(update_data.get("missing_fields", []))
    uncertain_json = json.dumps(update_data.get("uncertain_fields", []))
    structured_json = json.dumps(update_data.get("structured_record", {})) if update_data.get("structured_record") is not None else None
    
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE records SET
                person_name = ?,
                age = ?,
                gender = ?,
                height = ?,
                weight = ?,
                blood_pressure = ?,
                examination_details = ?,
                referral_details = ?,
                missing_fields = ?,
                uncertain_fields = ?,
                extraction_notes = ?,
                structured_record = COALESCE(?, structured_record),
                reviewer_notes = ?,
                reviewed_by = ?,
                reviewed_at = ?,
                status = 'reviewed',
                updated_at = ?
            WHERE id = ?;
        """, (
            update_data.get("person_name", "").strip() or "Unnamed Patient",
            update_data.get("age"),
            update_data.get("gender"),
            update_data.get("height"),
            update_data.get("weight"),
            update_data.get("blood_pressure"),
            update_data.get("examination_details"),
            update_data.get("referral_details"),
            missing_json,
            uncertain_json,
            update_data.get("extraction_notes"),
            structured_json,
            update_data.get("reviewer_notes"),
            reviewer,
            now,
            now,
            record_id
        ))
        conn.commit()
    return get_record_by_id(record_id, db_path)

def get_overall_stats(db_path: str = DEFAULT_DB_PATH) -> Dict[str, int]:
    """Return system-wide totals directly from database queries."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS total FROM camps;")
        total_camps = cursor.fetchone()["total"]
        
        cursor.execute("""
            SELECT 
                COUNT(*) AS total_records,
                SUM(CASE WHEN status = 'needs_review' THEN 1 ELSE 0 END) AS to_check,
                SUM(CASE WHEN status = 'reviewed' THEN 1 ELSE 0 END) AS reviewed
            FROM records;
        """)
        rec_row = cursor.fetchone()
        return {
            "total_camps": total_camps,
            "total_scanned": rec_row["total_records"] or 0,
            "to_check": rec_row["to_check"] or 0,
            "reviewed": rec_row["reviewed"] or 0
        }

def _format_record_row(row: sqlite3.Row) -> Dict[str, Any]:
    """Helper to convert sqlite Row to JSON-serializable dictionary."""
    missing = []
    uncertain = []
    try:
        if row["missing_fields"]:
            missing = json.loads(row["missing_fields"])
    except Exception:
        pass
    try:
        if row["uncertain_fields"]:
            uncertain = json.loads(row["uncertain_fields"])
    except Exception:
        pass
        
    structured_record = {}
    try:
        if "structured_record" in row.keys() and row["structured_record"]:
            structured_record = json.loads(row["structured_record"])
    except Exception:
        pass

    return {
        "id": row["id"],
        "camp_id": row["camp_id"],
        "camp_name": row["camp_name"] if "camp_name" in row.keys() else None,
        "person_name": row["person_name"],
        "age": row["age"],
        "gender": row["gender"],
        "height": row["height"],
        "weight": row["weight"],
        "blood_pressure": row["blood_pressure"],
        "examination_details": row["examination_details"],
        "referral_details": row["referral_details"],
        "missing_fields": missing,
        "uncertain_fields": uncertain,
        "extraction_notes": row["extraction_notes"],
        "structured_record": structured_record,
        "status": row["status"],
        "page1_preview": row["page1_preview"],
        "page2_preview": row["page2_preview"],
        "reviewer_notes": row["reviewer_notes"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"]
    }
