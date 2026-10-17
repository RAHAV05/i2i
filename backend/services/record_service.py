import io
import csv
import sys
import os
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import xlsxwriter

# Ensure project root in path for flexible execution
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend import database

DANGEROUS_CSV_PREFIXES = ('=', '+', '-', '@', '\t', '\r')

def sanitize_csv_cell(value: Any) -> str:
    """Sanitize cell against CSV / Spreadsheet formula injection.
    If a string starts with =, +, -, @, tab, or carriage return,
    prepend a single quote (') so spreadsheet engines treat it strictly as text.
    """
    if value is None:
        return ""
    s = str(value)
    if s.startswith(DANGEROUS_CSV_PREFIXES) or s.lstrip().startswith(('=', '+', '-', '@')):
        return f"'{s}"
    return s.strip()

class RecordService:
    """Business logic for camp records."""

    def __init__(self, db_path: str = database.DEFAULT_DB_PATH):
        self.db_path = db_path

    def validate_camp(self, camp_id: str) -> Optional[Dict[str, Any]]:
        """Verify camp existence."""
        camp = database.get_camp_by_id(camp_id, self.db_path)
        return camp

    def create_record(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and create a new scanned record in 'needs_review' state."""
        camp_id = data.get("camp_id")
        if not camp_id:
            raise ValueError("Camp ID is required.")

        camp = self.validate_camp(camp_id)
        if not camp:
            raise ValueError(f"Camp '{camp_id}' does not exist.")

        person_name = (data.get("person_name") or "").strip()
        if not person_name:
            raise ValueError("Person's name is required to create a patient record.")

        # Ensure status is 'needs_review' initially
        record_payload = {
            "id": data.get("id"),
            "camp_id": camp_id,
            "person_name": person_name,
            "age": data.get("age"),
            "gender": data.get("gender"),
            "height": data.get("height"),
            "weight": data.get("weight"),
            "blood_pressure": data.get("blood_pressure"),
            "examination_details": data.get("examination_details"),
            "referral_details": data.get("referral_details"),
            "missing_fields": data.get("missing_fields", []),
            "uncertain_fields": data.get("uncertain_fields", []),
            "extraction_notes": data.get("extraction_notes"),
            "structured_record": data.get("structured_record", {}),
            "status": "needs_review",
            "page1_preview": data.get("page1_preview"),
            "page2_preview": data.get("page2_preview"),
            "reviewer_notes": None,
            "reviewed_by": None,
            "reviewed_at": None
        }

        # Check for duplicate submission if an explicit ID was passed
        if record_payload["id"]:
            existing = database.get_record_by_id(record_payload["id"], self.db_path)
            if existing:
                return existing

        created = database.insert_record(record_payload, self.db_path)
        return created

    def review_record(self, record_id: str, updates: Dict[str, Any], reviewer: str) -> Dict[str, Any]:
        """Explicitly approve/review a record with updated fields."""
        existing = database.get_record_by_id(record_id, self.db_path)
        if not existing:
            raise ValueError(f"Record with ID '{record_id}' not found.")

        person_name = updates.get("person_name", existing["person_name"]).strip()
        if not person_name:
            raise ValueError("Person's name cannot be blank upon review.")

        if not reviewer or not reviewer.strip():
            reviewer = "Camp Doctor (Reviewer)"

        updated = database.update_reviewed_record(record_id, updates, reviewer.strip(), self.db_path)
        if not updated:
            raise RuntimeError(f"Failed to update record '{record_id}'.")
        return updated

    def generate_csv_export(self, camp_id: Optional[str] = None, only_reviewed: bool = False) -> str:
        """Generate RFC 4180 compliant CSV of records with formula injection protection."""
        status_filter = "reviewed" if only_reviewed else None
        records = database.list_records(camp_id=camp_id, status=status_filter, limit=10000, db_path=self.db_path)

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")

        headers = [
            "Record ID",
            "Camp ID",
            "Camp Name",
            "Document ID",
            "Admission ID",
            "School / College",
            "Staff / Student",
            "Person Name",
            "Age",
            "Gender",
            "DOB",
            "Blood Group",
            "Mobile Number",
            "Address",
            "Height",
            "Weight",
            "Blood Pressure",
            "Pulse (bpm)",
            "RBS (mg/dL)",
            "Waist Circumference (cm)",
            "Known Allergies",
            "Previous Surgery / Hospitalization",
            "Physical Activity",
            "Social History",
            "Vision Right Eye",
            "Vision Left Eye",
            "Examination Details",
            "Referral Details",
            "Status",
            "Reviewed By",
            "Reviewed At",
            "Reviewer Notes",
            "Extraction Notes",
            "Created At"
        ]
        writer.writerow(headers)

        for r in records:
            sr = r.get("structured_record") or {}
            p_info = sr.get("personal_information") or {}
            m_hist = sr.get("medical_history") or {}
            h_asmt = sr.get("health_assessment") or {}
            vis = sr.get("vision") or {}

            doc_id = p_info.get("doc_id", {}).get("value")
            adm_id = p_info.get("admission_id", {}).get("value")
            school = p_info.get("school_college_name", {}).get("value")
            staff_student = p_info.get("staff_student_type", {}).get("value")
            dob = p_info.get("dob", {}).get("value")
            b_group = p_info.get("blood_group", {}).get("value")
            mobile = p_info.get("mobile_number", {}).get("value")
            address = p_info.get("address", {}).get("value")

            pulse = h_asmt.get("pulse_bpm", {}).get("value")
            rbs = h_asmt.get("random_blood_sugar_mgdl", {}).get("value")
            waist = h_asmt.get("waist_circumference_cm", {}).get("value")

            allergies = m_hist.get("known_allergies", {}).get("details") or m_hist.get("known_allergies", {}).get("value")
            surgery = m_hist.get("previous_surgery_hospitalization", {}).get("details") or m_hist.get("previous_surgery_hospitalization", {}).get("value")
            activity = m_hist.get("physical_activity", {}).get("details") or m_hist.get("physical_activity", {}).get("value")
            social = m_hist.get("social_history", {}).get("details") or m_hist.get("social_history", {}).get("value")

            va_right = vis.get("distant_vision_right", {}).get("value")
            va_left = vis.get("distant_vision_left", {}).get("value")

            row = [
                sanitize_csv_cell(r.get("id")),
                sanitize_csv_cell(r.get("camp_id")),
                sanitize_csv_cell(r.get("camp_name")),
                sanitize_csv_cell(doc_id),
                sanitize_csv_cell(adm_id),
                sanitize_csv_cell(school),
                sanitize_csv_cell(staff_student),
                sanitize_csv_cell(r.get("person_name")),
                sanitize_csv_cell(r.get("age")),
                sanitize_csv_cell(r.get("gender")),
                sanitize_csv_cell(dob),
                sanitize_csv_cell(b_group),
                sanitize_csv_cell(mobile),
                sanitize_csv_cell(address),
                sanitize_csv_cell(r.get("height")),
                sanitize_csv_cell(r.get("weight")),
                sanitize_csv_cell(r.get("blood_pressure")),
                sanitize_csv_cell(pulse),
                sanitize_csv_cell(rbs),
                sanitize_csv_cell(waist),
                sanitize_csv_cell(allergies),
                sanitize_csv_cell(surgery),
                sanitize_csv_cell(activity),
                sanitize_csv_cell(social),
                sanitize_csv_cell(va_right),
                sanitize_csv_cell(va_left),
                sanitize_csv_cell(r.get("examination_details")),
                sanitize_csv_cell(r.get("referral_details")),
                sanitize_csv_cell(r.get("status")),
                sanitize_csv_cell(r.get("reviewed_by")),
                sanitize_csv_cell(r.get("reviewed_at")),
                sanitize_csv_cell(r.get("reviewer_notes")),
                sanitize_csv_cell(r.get("extraction_notes")),
                sanitize_csv_cell(r.get("created_at")),
            ]
            writer.writerow(row)

        return output.getvalue()

    def generate_excel_export(self, camp_id: Optional[str] = None, only_reviewed: bool = False) -> bytes:
        """Generate formatted Excel (.xlsx) workbook containing all 8 form sections and operational details."""
        status_filter = "reviewed" if only_reviewed else None
        records = database.list_records(camp_id=camp_id, status=status_filter, limit=10000, db_path=self.db_path)
        camps = database.list_camps_with_stats(self.db_path)
        if camp_id:
            camps = [c for c in camps if c["id"] == camp_id]

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True, 'remove_timezone': True})

        # Styling
        header_fmt = wb.add_format({
            'bold': True,
            'font_name': 'Figtree',
            'font_size': 10,
            'bg_color': '#0E7C6B',
            'font_color': '#FFFFFF',
            'valign': 'vcenter',
            'align': 'center',
            'border': 1,
            'border_color': '#A7D7CE',
            'text_wrap': True
        })
        cell_fmt = wb.add_format({
            'font_name': 'Figtree',
            'font_size': 9,
            'valign': 'vcenter',
            'border': 1,
            'border_color': '#E6EEEC'
        })
        cell_alt_fmt = wb.add_format({
            'font_name': 'Figtree',
            'font_size': 9,
            'valign': 'vcenter',
            'bg_color': '#F8FAF9',
            'border': 1,
            'border_color': '#E6EEEC'
        })
        status_reviewed_fmt = wb.add_format({
            'font_name': 'Figtree',
            'font_size': 9,
            'bold': True,
            'valign': 'vcenter',
            'align': 'center',
            'bg_color': '#DDF1EA',
            'font_color': '#0A5C50',
            'border': 1,
            'border_color': '#B5E2D5'
        })
        status_to_check_fmt = wb.add_format({
            'font_name': 'Figtree',
            'font_size': 9,
            'bold': True,
            'valign': 'vcenter',
            'align': 'center',
            'bg_color': '#FDF1D6',
            'font_color': '#8A5A00',
            'border': 1,
            'border_color': '#F6DF9F'
        })

        ws = wb.add_worksheet("Patient Records")
        ws.set_tab_color('#0E7C6B')
        ws.set_row(0, 32)
        # Freeze header and first 3 columns
        ws.freeze_panes(1, 3)

        columns = [
            ("Record ID", 18),
            ("Camp Name", 24),
            ("Person Name", 22),
            ("Review Status", 14),
            ("Camp ID", 18),
            ("Created At", 20),
            ("Doc ID", 14),
            ("Admission ID", 16),
            ("School / College Name", 26),
            ("Staff / Student Type", 16),
            ("Age", 10),
            ("Gender", 10),
            ("DOB", 14),
            ("Blood Group", 12),
            ("Year of Study", 14),
            ("Course / Department", 20),
            ("Section", 10),
            ("Shift", 10),
            ("Mobile Number", 16),
            ("Marital Status", 14),
            ("Parent / Spouse Name", 22),
            ("Number of Children", 16),
            ("Residential Address", 30),
            ("Consent: Screening", 18),
            ("Consent: First Aid", 18),
            ("Signature Present", 16),
            ("Known Allergies", 16),
            ("Allergies Details", 24),
            ("Abdominal Symptoms", 18),
            ("Abdominal Details", 24),
            ("PCOD / Menopause", 18),
            ("Gynecological Details", 24),
            ("Skin / Hair / ENT Issues", 20),
            ("ENT & Skin Details", 24),
            ("Current Medications", 18),
            ("Medication Details", 24),
            ("Previous Surgery / Hospitalization", 22),
            ("Surgery Details", 24),
            ("Physical Activity", 16),
            ("Activity Details", 20),
            ("Social History (Smoking/Alcohol)", 20),
            ("Social History Details", 22),
            ("Other Medical Issues", 24),
            ("Immediate Family Count", 18),
            ("Personal Physician", 20),
            ("Family: Diabetes", 18),
            ("Family: Hypertension", 18),
            ("Family: Heart Disease", 18),
            ("Family: Thyroid", 18),
            ("Family: Cancer", 18),
            ("Family: Epilepsy", 18),
            ("Family: Asthma", 18),
            ("Family: Other Conditions", 20),
            ("Examination Date", 16),
            ("Height (cm)", 12),
            ("Weight (kg)", 12),
            ("BMI (kg/m²)", 12),
            ("Blood Pressure", 16),
            ("Pulse (bpm)", 12),
            ("RBS (mg/dL)", 14),
            ("Waist Circumference (cm)", 18),
            ("General Sign: Pallor", 16),
            ("General Sign: Icterus", 16),
            ("General Sign: Cyanosis", 16),
            ("General Sign: Clubbing", 16),
            ("General Sign: Edema", 16),
            ("General Sign: Lymphadenopathy", 20),
            ("General Sign: Deformity", 18),
            ("ECG Findings", 20),
            ("Echo Findings", 20),
            ("Ear Exam", 16),
            ("Nose Exam", 16),
            ("Throat Exam", 16),
            ("Respiratory System (RS)", 22),
            ("Cardiovascular System (CVS)", 22),
            ("Abdomen Exam", 18),
            ("Central Nervous System (CNS)", 22),
            ("Musculoskeletal System", 22),
            ("Skin Exam", 16),
            ("Hair Exam", 16),
            ("Psychological Consultation", 20),
            ("Other Systemic Findings", 22),
            ("Vision Category", 16),
            ("Distant Vision: Right", 18),
            ("Distant Vision: Left", 18),
            ("Dental Status", 14),
            ("Dental Abnormality", 20),
            ("Dental Advice", 22),
            ("Specialist Referred To", 22),
            ("Referral Notes", 24),
            ("Doctor Signature Present", 18),
            ("Examination Details", 26),
            ("Reviewed By", 18),
            ("Reviewed At", 20),
            ("Reviewer Notes", 26),
            ("Extraction Notes", 26)
        ]

        for col_idx, (col_name, col_width) in enumerate(columns):
            ws.write_string(0, col_idx, col_name, header_fmt)
            ws.set_column(col_idx, col_idx, col_width)

        def _val(obj: Any) -> str:
            if obj is None: return ""
            if isinstance(obj, dict):
                v = obj.get("value")
                return str(v) if v is not None else ""
            return str(obj).strip()

        def _details(obj: Any) -> str:
            if isinstance(obj, dict):
                return str(obj.get("details") or obj.get("relationship") or obj.get("notes") or "")
            return ""

        def _calc_bmi(h_str: Any, w_str: Any) -> str:
            try:
                h_m = re.search(r"(\d+(?:\.\d+)?)", str(h_str or ""))
                w_m = re.search(r"(\d+(?:\.\d+)?)", str(w_str or ""))
                if h_m and w_m:
                    h = float(h_m.group(1))
                    w = float(w_m.group(1))
                    if h > 30: h = h / 100.0
                    if h > 0: return str(round(w / (h * h), 1))
            except Exception: pass
            return ""

        for row_idx, r in enumerate(records, start=1):
            row_fmt = cell_alt_fmt if row_idx % 2 == 0 else cell_fmt
            sr = r.get("structured_record") or {}
            p = sr.get("personal_information") or {}
            m = sr.get("medical_history") or {}
            f = sr.get("family_history") or {}
            h = sr.get("health_assessment") or {}
            s = sr.get("system_examination") or {}
            v = sr.get("vision") or {}
            d = sr.get("dental") or {}
            ref = sr.get("referral") or {}

            person_name = _val(p.get("full_name")) or r.get("person_name") or ""
            age = _val(p.get("age")) or r.get("age") or ""
            gender = _val(p.get("gender")) or r.get("gender") or ""
            height = _val(h.get("height_cm")) or r.get("height") or ""
            weight = _val(h.get("weight_kg")) or r.get("weight") or ""
            bp = _val(h.get("blood_pressure_mmhg")) or r.get("blood_pressure") or ""
            exam_det = r.get("examination_details") or ""
            ref_spec = _val(ref.get("specialist_referred_to")) or r.get("referral_details") or ""
            bmi = _calc_bmi(height, weight)

            status = r.get("status") or "needs_review"
            st_fmt = status_reviewed_fmt if status == "reviewed" else status_to_check_fmt

            row_data = [
                str(r.get("id") or ""),
                str(r.get("camp_name") or ""),
                person_name,
                status,
                str(r.get("camp_id") or ""),
                str(r.get("created_at") or ""),
                _val(p.get("doc_id")),
                _val(p.get("admission_id")),
                _val(p.get("school_college_name")),
                _val(p.get("staff_student_type")),
                age,
                gender,
                _val(p.get("dob")),
                _val(p.get("blood_group")),
                _val(p.get("year_of_study")),
                _val(p.get("course_dept")),
                _val(p.get("section")),
                _val(p.get("shift")),
                _val(p.get("mobile_number")),
                _val(p.get("marital_status")),
                _val(p.get("parent_spouse_name")),
                _val(p.get("number_of_children")),
                _val(p.get("address")),
                _val(p.get("consent_screening")),
                _val(p.get("consent_first_aid")),
                _val(p.get("signature_present")),
                _val(m.get("known_allergies")),
                _details(m.get("known_allergies")),
                _val(m.get("abdominal_pain_indigestion_constipation")),
                _details(m.get("abdominal_pain_indigestion_constipation")),
                _val(m.get("irregular_pcod_menopause")),
                _details(m.get("irregular_pcod_menopause")),
                _val(m.get("skin_hair_ear_nose_problems")),
                _details(m.get("skin_hair_ear_nose_problems")),
                _val(m.get("current_previous_medications")),
                _details(m.get("current_previous_medications")),
                _val(m.get("previous_surgery_hospitalization")),
                _details(m.get("previous_surgery_hospitalization")),
                _val(m.get("physical_activity")),
                _details(m.get("physical_activity")),
                _val(m.get("social_history")),
                _details(m.get("social_history")),
                _val(m.get("other_medical_issues")),
                _val(m.get("immediate_family_count")),
                _val(m.get("personal_physician")),
                _details(f.get("diabetes")) or _val(f.get("diabetes")),
                _details(f.get("hypertension")) or _val(f.get("hypertension")),
                _details(f.get("heart_disease")) or _val(f.get("heart_disease")),
                _details(f.get("thyroid")) or _val(f.get("thyroid")),
                _details(f.get("cancer")) or _val(f.get("cancer")),
                _details(f.get("epilepsy")) or _val(f.get("epilepsy")),
                _details(f.get("asthma")) or _val(f.get("asthma")),
                _details(f.get("other_conditions")) or _val(f.get("other_conditions")),
                _val(h.get("examination_date")),
                height,
                weight,
                bmi,
                bp,
                _val(h.get("pulse_bpm")),
                _val(h.get("random_blood_sugar_mgdl")),
                _val(h.get("waist_circumference_cm")),
                _val(h.get("pallor")),
                _val(h.get("icterus")),
                _val(h.get("cyanosis")),
                _val(h.get("clubbing")),
                _val(h.get("edema")),
                _val(h.get("lymphadenopathy")),
                _val(h.get("deformity")),
                _val(h.get("ecg_notes")),
                _val(h.get("echo_notes")),
                _val(s.get("ear")),
                _val(s.get("nose")),
                _val(s.get("throat")),
                _val(s.get("respiratory_system")),
                _val(s.get("cardiovascular_system")),
                _val(s.get("abdomen")),
                _val(s.get("central_nervous_system")),
                _val(s.get("musculoskeletal_system")),
                _val(s.get("skin")),
                _val(s.get("hair")),
                _val(s.get("psychological_consultation")),
                _val(s.get("others")),
                _val(v.get("vision_category")),
                _val(v.get("distant_vision_right")),
                _val(v.get("distant_vision_left")),
                _val(d.get("normal")),
                _val(d.get("abnormality")),
                _val(d.get("advice")),
                ref_spec,
                _details(ref.get("specialist_referred_to")),
                _val(ref.get("doctor_signature_present")),
                exam_det,
                str(r.get("reviewed_by") or ""),
                str(r.get("reviewed_at") or ""),
                str(r.get("reviewer_notes") or ""),
                str(r.get("extraction_notes") or "")
            ]

            ws.set_row(row_idx, 20)
            for c_idx, val in enumerate(row_data):
                fmt_to_use = st_fmt if c_idx == 3 else row_fmt
                ws.write_string(row_idx, c_idx, str(val), fmt_to_use)

        # Worksheet 2: Camp Summary
        ws_camps = wb.add_worksheet("Camp Summary")
        ws_camps.set_tab_color('#E8A317')
        camp_cols = [
            ("Camp ID", 20),
            ("Camp Name", 30),
            ("Camp Date", 16),
            ("Target Records", 16),
            ("Total Scanned", 16),
            ("To Check", 16),
            ("Reviewed", 16),
            ("Progress (%)", 16),
            ("Created At", 22)
        ]
        ws_camps.set_row(0, 30)
        for c_idx, (col_name, col_width) in enumerate(camp_cols):
            ws_camps.write_string(0, c_idx, col_name, header_fmt)
            ws_camps.set_column(c_idx, c_idx, col_width)

        for row_idx, c in enumerate(camps, start=1):
            r_fmt = cell_alt_fmt if row_idx % 2 == 0 else cell_fmt
            ws_camps.set_row(row_idx, 20)
            ws_camps.write_string(row_idx, 0, str(c.get("id")), r_fmt)
            ws_camps.write_string(row_idx, 1, str(c.get("name")), r_fmt)
            ws_camps.write_string(row_idx, 2, str(c.get("date_str")), r_fmt)
            ws_camps.write_number(row_idx, 3, int(c.get("expected_records") or 0), r_fmt)
            ws_camps.write_number(row_idx, 4, int(c.get("total_scanned") or 0), r_fmt)
            ws_camps.write_number(row_idx, 5, int(c.get("to_check") or 0), r_fmt)
            ws_camps.write_number(row_idx, 6, int(c.get("reviewed") or 0), r_fmt)
            ws_camps.write_number(row_idx, 7, float(c.get("progress_pct") or 0.0), r_fmt)
            ws_camps.write_string(row_idx, 8, str(c.get("created_at") or ""), r_fmt)

        wb.close()
        output.seek(0)
        return output.getvalue()
