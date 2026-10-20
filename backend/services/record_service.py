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

try:
    from backend import database
except ImportError:
    import database

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

    @staticmethod
    def validate_record_fields(data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate clinical fields according to medical norms.
        Returns: { 'is_valid': bool, 'errors': dict, 'warnings': dict }
        """
        errors = {}
        warnings = {}

        # 1. Person Name
        name = (data.get("person_name") or "").strip()
        if not name:
            errors["person_name"] = "Patient name is required."

        # 2. Age: Numeric, 0-150
        age = data.get("age")
        if age not in (None, ""):
            age_str = str(age).strip()
            digits = re.findall(r'\d+', age_str)
            if digits:
                val = int(digits[0])
                if val < 0 or val > 150:
                    errors["age"] = f"Age must be between 0 and 150 (got {val})."
            else:
                warnings["age"] = f"Could not determine numeric age from '{age_str}'."

        # 3. Blood Pressure: XXX/XX format & realistic systolic/diastolic ranges
        bp = data.get("blood_pressure")
        if bp not in (None, ""):
            bp_str = str(bp).strip()
            if "/" in bp_str:
                parts = bp_str.split("/")
                s_digits = re.findall(r'\d+', parts[0])
                d_digits = re.findall(r'\d+', parts[1])
                if s_digits and d_digits:
                    systolic = int(s_digits[0])
                    diastolic = int(d_digits[0])
                    if systolic < 50 or systolic > 260:
                        warnings["blood_pressure"] = f"Systolic pressure {systolic} is outside standard clinical range (50-260 mmHg)."
                    if diastolic < 30 or diastolic > 160:
                        warnings["blood_pressure"] = f"Diastolic pressure {diastolic} is outside standard clinical range (30-160 mmHg)."
                    if systolic <= diastolic:
                        errors["blood_pressure"] = f"Systolic ({systolic}) must be greater than diastolic ({diastolic})."
                else:
                    warnings["blood_pressure"] = "BP format should be systolic/diastolic like 120/80."
            else:
                warnings["blood_pressure"] = "BP should be in format XXX/XX like 120/80."

        # 4. Height: 30-250 cm
        height = data.get("height")
        if height not in (None, ""):
            h_str = str(height).strip()
            h_digits = re.findall(r'\d+', h_str)
            if h_digits:
                h_val = int(h_digits[0])
                if h_val < 30 or h_val > 250:
                    warnings["height"] = f"Height {h_val} cm seems outside typical range (30-250 cm)."

        # 5. Weight: 0.5-300 kg
        weight = data.get("weight")
        if weight not in (None, ""):
            w_str = str(weight).strip()
            w_digits = re.findall(r'[\d.]+', w_str)
            if w_digits:
                try:
                    w_val = float(w_digits[0])
                    if w_val < 0.5 or w_val > 300:
                        warnings["weight"] = f"Weight {w_val} kg seems outside typical range (0.5-300 kg)."
                except ValueError:
                    pass

        # 6. Gender: Male, Female, Other
        gender = data.get("gender")
        if gender not in (None, ""):
            g_clean = str(gender).strip().capitalize()
            if not any(k in g_clean for k in ("Male", "Female", "Other")):
                warnings["gender"] = f"Unusual gender value '{gender}' (expected Male, Female, or Other)."

        return {
            "is_valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }

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
        """Generate formatted Excel (.xlsx) workbook with columns in exact form field order (Page 1 → Page 2)."""
        status_filter = "reviewed" if only_reviewed else None
        records = database.list_records(camp_id=camp_id, status=status_filter, limit=10000, db_path=self.db_path)
        camps = database.list_camps_with_stats(self.db_path)
        if camp_id:
            camps = [c for c in camps if c["id"] == camp_id]

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True, 'remove_timezone': True})

        # ── Formats ──────────────────────────────────────────────────────────
        header_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': '#D81B60', 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#F8BBD0', 'text_wrap': True
        })
        section_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': '#4A0E2A', 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#7B1FA2', 'text_wrap': True
        })
        cell_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9,
            'valign': 'vcenter', 'border': 1, 'border_color': '#F1F4F7'
        })
        cell_alt_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'valign': 'vcenter',
            'bg_color': '#FDF9FB', 'border': 1, 'border_color': '#F1F4F7'
        })
        status_reviewed_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#ECFDF5', 'font_color': '#059669',
            'border': 1, 'border_color': '#A7F3D0'
        })
        status_to_check_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#FFFBEB', 'font_color': '#D97706',
            'border': 1, 'border_color': '#FDE68A'
        })

        sheet_name = "Patient Records"
        if camp_id and camps:
            c_name = re.sub(r'[:/\\?*\[\]]', '', str(camps[0].get("name", "Records")))[:30].strip()
            sheet_name = c_name or "Patient Records"

        ws = wb.add_worksheet(sheet_name)
        ws.set_tab_color('#D81B60')
        ws.set_row(0, 40)
        ws.freeze_panes(1, 3)

        # ── Column definitions in EXACT form order ───────────────────────────
        # Format: (header_label, col_width, is_section_divider)
        # Section dividers are written with section_fmt to visually separate form pages
        columns = [
            # ── Admin / ID columns (operational) ────────────────────────────
            ("Record ID",                      18, False),
            ("Camp Name",                      24, False),
            ("Review Status",                  14, False),

            # ── PAGE 1 — STUDENT'S PERSONAL INFORMATION ──────────────────────
            ("Moc Doc ID",                     16, True),
            ("Admission ID",                   16, False),
            ("Name of School / College",       28, False),
            ("Staff / Student",                16, False),
            ("Full Name",                      26, False),
            ("Gender",                         10, False),
            ("Year of Study",                  14, False),
            ("Course / Dept",                  20, False),
            ("Section",                        10, False),
            ("Shift",                          10, False),
            ("Blood Group",                    12, False),
            ("DOB",                            14, False),
            ("Age",                            10, False),
            ("Mobile",                         16, False),
            ("Marital Status",                 14, False),
            ("Parent / Spouse Name",           22, False),
            ("No of Children",                 14, False),
            ("Address",                        32, False),

            # ── PAGE 1 — MEDICAL HISTORY ─────────────────────────────────────
            ("Any Known Allergy",              18, True),
            ("Allergy Details",                24, False),
            ("Abdominal Issues",               18, False),
            ("Abdominal Details",              24, False),
            ("Irregular / PCOD / Menopause",   20, False),
            ("PCOD / Menopause Details",       24, False),
            ("Skin / Hair / ENT Problem",      20, False),
            ("Skin / ENT Details",             24, False),
            ("Current / Previous Medications", 20, False),
            ("Medication Details",             24, False),
            ("Previous Surgery / Hospitalization", 22, False),
            ("Surgery / Hospitalization Details", 24, False),
            ("Social History",                 18, False),
            ("Social History Details",         22, False),
            ("Physical Activity",              18, False),
            ("Physical Activity Details",      22, False),
            ("Any Other Medical Issues",       26, False),
            ("No of Immediate Family Members", 20, False),
            ("Personal Physician",             22, False),

            # ── PAGE 1 — FAMILY HISTORY ───────────────────────────────────────
            ("Family Thyroid",                 18, True),
            ("Family Diabetes",                18, False),
            ("Family Heart Disease",           18, False),
            ("Family Hypertension",            18, False),
            ("Family Cancer",                  18, False),
            ("Family Epilepsy",                18, False),
            ("Family Asthma",                  18, False),
            ("Family Others",                  18, False),

            # ── PAGE 2 — HEALTH ASSESSMENT ───────────────────────────────────
            ("Health Assessment Name",         22, True),
            ("Date of Examination",            18, False),
            ("Height",                         12, False),
            ("Weight",                         12, False),
            ("BMI",                            12, False),
            ("Pulse",                          12, False),
            ("Blood Pressure",                 16, False),
            ("RBS",                            14, False),
            ("Waist Circumference",            18, False),
            ("ECG",                            24, False),
            ("ECHO",                           24, False),

            # ── General Signs ─────────────────────────────────────────────────
            ("Pallor",                         14, True),
            ("Lymphadenopathy",                16, False),
            ("Deformity",                      14, False),
            ("Icterus",                        14, False),
            ("Cyanosis",                       14, False),
            ("Edema",                          14, False),
            ("Clubbing",                       14, False),

            # ── System Examination ────────────────────────────────────────────
            ("Ear",                            14, True),
            ("Ear Details",                    24, False),
            ("Nose",                           14, False),
            ("Nose Details",                   24, False),
            ("Throat",                         14, False),
            ("Throat Details",                 24, False),
            ("RS",                             14, False),
            ("RS Details",                     24, False),
            ("CVS",                            14, False),
            ("CVS Details",                    24, False),
            ("Abdomen",                        14, False),
            ("Abdomen Details",                24, False),
            ("CNS",                            14, False),
            ("CNS Details",                    24, False),
            ("Skin",                           14, False),
            ("Skin Details",                   24, False),
            ("Hair",                           14, False),
            ("Hair Details",                   24, False),
            ("Musculoskeletal System",          20, False),
            ("Musculoskeletal Details",        24, False),
            ("Others",                         14, False),
            ("Others Details",                 24, False),
            ("Psychological Consultation",     22, False),
            ("Psychological Details",          24, False),

            # ── Vision ────────────────────────────────────────────────────────
            ("Vision Category",                18, True),
            ("Distant Vision Right Eye",       18, False),
            ("Distant Vision Left Eye",        18, False),

            # ── Dental ────────────────────────────────────────────────────────
            ("Dental Status",                  16, True),
            ("Dental Abnormality",             22, False),
            ("Dental Advice",                  22, False),

            # ── Referral ──────────────────────────────────────────────────────
            ("Specialist Referred To",         22, True),
            ("Doctor Signature Present",       18, False),

            # ── Operational / Admin ───────────────────────────────────────────
            ("Camp ID",                         18, True),
            ("Created At",                      20, False),
            ("Reviewed By",                     18, False),
            ("Reviewed At",                     20, False),
            ("Reviewer Notes",                  28, False),
            ("Extraction Notes",                28, False),
        ]

        for col_idx, (col_name, col_width, is_section) in enumerate(columns):
            fmt = section_fmt if is_section else header_fmt
            ws.write_string(0, col_idx, col_name, fmt)
            ws.set_column(col_idx, col_idx, col_width)

        # ── Helper functions ──────────────────────────────────────────────────
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

        def _sys_normal(obj: Any) -> str:
            """Return Normal/Abnormal status for a system exam entry."""
            if isinstance(obj, dict):
                val = str(obj.get("value") or obj.get("status") or "").strip()
                if val: return val
            return ""

        def _sys_details(obj: Any) -> str:
            """Return abnormal details for a system exam entry."""
            if isinstance(obj, dict):
                return str(obj.get("details") or obj.get("abnormal_details") or obj.get("notes") or "")
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

        # ── Write rows ────────────────────────────────────────────────────────
        for row_idx, r in enumerate(records, start=1):
            row_fmt = cell_alt_fmt if row_idx % 2 == 0 else cell_fmt
            sr = r.get("structured_record") or {}
            p  = sr.get("personal_information") or {}
            m  = sr.get("medical_history") or {}
            f  = sr.get("family_history") or {}
            h  = sr.get("health_assessment") or {}
            s  = sr.get("system_examination") or {}
            v  = sr.get("vision") or {}
            d  = sr.get("dental") or {}
            ref = sr.get("referral") or {}

            person_name = _val(p.get("full_name")) or r.get("person_name") or ""
            age    = _val(p.get("age"))    or r.get("age")    or ""
            gender = _val(p.get("gender")) or r.get("gender") or ""
            height = _val(h.get("height_cm")) or r.get("height") or ""
            weight = _val(h.get("weight_kg")) or r.get("weight") or ""
            bp     = _val(h.get("blood_pressure_mmhg")) or r.get("blood_pressure") or ""
            bmi    = _calc_bmi(height, weight)
            status = r.get("status") or "needs_review"
            st_fmt = status_reviewed_fmt if status == "reviewed" else status_to_check_fmt

            row_data = [
                # Admin
                str(r.get("id") or ""),
                str(r.get("camp_name") or ""),
                status,

                # PAGE 1 — Personal Info (form order)
                _val(p.get("doc_id")),
                _val(p.get("admission_id")),
                _val(p.get("school_college_name")),
                _val(p.get("staff_student_type")),
                person_name,
                gender,
                _val(p.get("year_of_study")),
                _val(p.get("course_dept")),
                _val(p.get("section")),
                _val(p.get("shift")),
                _val(p.get("blood_group")),
                _val(p.get("dob")),
                age,
                _val(p.get("mobile_number")),
                _val(p.get("marital_status")),
                _val(p.get("parent_spouse_name")),
                _val(p.get("number_of_children")),
                _val(p.get("address")),

                # PAGE 1 — Medical History (Y/N + Details pairs in form order)
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
                _val(m.get("social_history")),
                _details(m.get("social_history")),
                _val(m.get("physical_activity")),
                _details(m.get("physical_activity")),
                _val(m.get("other_medical_issues")),
                _val(m.get("immediate_family_count")),
                _val(m.get("personal_physician")),

                # PAGE 1 — Family History (form order: Thyroid, Diabetes, Heart, Hypertension, Cancer, Epilepsy, Asthma, Others)
                _details(f.get("thyroid"))       or _val(f.get("thyroid")),
                _details(f.get("diabetes"))      or _val(f.get("diabetes")),
                _details(f.get("heart_disease")) or _val(f.get("heart_disease")),
                _details(f.get("hypertension"))  or _val(f.get("hypertension")),
                _details(f.get("cancer"))        or _val(f.get("cancer")),
                _details(f.get("epilepsy"))      or _val(f.get("epilepsy")),
                _details(f.get("asthma"))        or _val(f.get("asthma")),
                _details(f.get("other_conditions")) or _val(f.get("other_conditions")),

                # PAGE 2 — Health Assessment (form order: Ht, Wt, BMI, Pulse, BP, RBS, Waist, ECG, ECHO)
                person_name,           # Name (P2) — repeated from header of page 2
                _val(h.get("examination_date")),
                height,
                weight,
                bmi,
                _val(h.get("pulse_bpm")),
                bp,
                _val(h.get("random_blood_sugar_mgdl")),
                _val(h.get("waist_circumference_cm")),
                _val(h.get("ecg_notes")),
                _val(h.get("echo_notes")),

                # General Signs (form order: Pallor, Lymphadenopathy, Deformity, Icterus, Cyanosis, Edema, Clubbing)
                _val(h.get("pallor")),
                _val(h.get("lymphadenopathy")),
                _val(h.get("deformity")),
                _val(h.get("icterus")),
                _val(h.get("cyanosis")),
                _val(h.get("edema")),
                _val(h.get("clubbing")),

                # System Examination (Normal/Details pairs in form order)
                _sys_normal(s.get("ear")),          _sys_details(s.get("ear")),
                _sys_normal(s.get("nose")),         _sys_details(s.get("nose")),
                _sys_normal(s.get("throat")),       _sys_details(s.get("throat")),
                _sys_normal(s.get("respiratory_system")),    _sys_details(s.get("respiratory_system")),
                _sys_normal(s.get("cardiovascular_system")), _sys_details(s.get("cardiovascular_system")),
                _sys_normal(s.get("abdomen")),      _sys_details(s.get("abdomen")),
                _sys_normal(s.get("central_nervous_system")), _sys_details(s.get("central_nervous_system")),
                _sys_normal(s.get("skin")),         _sys_details(s.get("skin")),
                _sys_normal(s.get("hair")),         _sys_details(s.get("hair")),
                _sys_normal(s.get("musculoskeletal_system")), _sys_details(s.get("musculoskeletal_system")),
                _sys_normal(s.get("others")),       _sys_details(s.get("others")),
                _sys_normal(s.get("psychological_consultation")), _sys_details(s.get("psychological_consultation")),

                # Vision
                _val(v.get("vision_category")),
                _val(v.get("distant_vision_right")),
                _val(v.get("distant_vision_left")),

                # Dental
                _val(d.get("normal")),
                _val(d.get("abnormality")),
                _val(d.get("advice")),

                # Referral
                _val(ref.get("specialist_referred_to")) or r.get("referral_details") or "",
                _val(ref.get("doctor_signature_present")),

                # Operational
                str(r.get("camp_id") or ""),
                str(r.get("created_at") or ""),
                str(r.get("reviewed_by") or ""),
                str(r.get("reviewed_at") or ""),
                str(r.get("reviewer_notes") or ""),
                str(r.get("extraction_notes") or ""),
            ]

            ws.set_row(row_idx, 20)
            for c_idx, val in enumerate(row_data):
                fmt_to_use = st_fmt if c_idx == 2 else row_fmt
                ws.write_string(row_idx, c_idx, str(val), fmt_to_use)

        # ── Sheet 2: Camp Summary ─────────────────────────────────────────────
        ws_camps = wb.add_worksheet("Camp Summary")
        ws_camps.set_tab_color('#C2185B')
        camp_cols = [
            ("Camp ID", 20), ("Camp Name", 30), ("Camp Date", 14),
            ("Target Records", 15), ("Total Scanned", 14), ("To Check", 12),
            ("Reviewed", 12), ("Progress (%)", 14),
            ("Hypertension Suspects (>=130/80)", 22),
            ("Vision Deficits / Cataract", 20), ("Dental Issues / Caries", 18),
            ("Pallor / Anemia Suspects", 18), ("Hospital Referrals", 18), ("Created At", 22)
        ]
        ws_camps.set_row(0, 32)
        for c_idx, (col_name, col_width) in enumerate(camp_cols):
            ws_camps.write_string(0, c_idx, col_name, header_fmt)
            ws_camps.set_column(c_idx, c_idx, col_width)

        for row_idx, c in enumerate(camps, start=1):
            r_fmt = cell_alt_fmt if row_idx % 2 == 0 else cell_fmt
            ws_camps.set_row(row_idx, 20)
            camp_identifier = str(c.get("id"))
            stats = database.get_camp_analytics(camp_identifier, self.db_path)
            abnormal_bp  = stats.get("abnormal_bp_count", 0)
            vis_data     = stats.get("vision_screening", {})
            vision_issues = vis_data.get("refraction_deficit", 0) + vis_data.get("cataract_suspect", 0)
            dent_data    = stats.get("dental_screening", {})
            dental_issues = dent_data.get("caries_identified", 0)
            pallor_data  = stats.get("pallor_indicators", {})
            pallor_pos   = pallor_data.get("pallor_positive", 0)
            ref_data     = stats.get("referral_breakdown", {})
            total_referrals = sum(v for k, v in ref_data.items() if k != "No Referral Required")

            ws_camps.write_string(row_idx, 0,  camp_identifier, r_fmt)
            ws_camps.write_string(row_idx, 1,  str(c.get("name")), r_fmt)
            ws_camps.write_string(row_idx, 2,  str(c.get("date_str")), r_fmt)
            ws_camps.write_number(row_idx, 3,  int(c.get("expected_records") or 0), r_fmt)
            ws_camps.write_number(row_idx, 4,  int(c.get("total_scanned") or 0), r_fmt)
            ws_camps.write_number(row_idx, 5,  int(c.get("to_check") or 0), r_fmt)
            ws_camps.write_number(row_idx, 6,  int(c.get("reviewed") or 0), r_fmt)
            ws_camps.write_number(row_idx, 7,  float(c.get("progress_pct") or 0.0), r_fmt)
            ws_camps.write_number(row_idx, 8,  abnormal_bp, r_fmt)
            ws_camps.write_number(row_idx, 9,  vision_issues, r_fmt)
            ws_camps.write_number(row_idx, 10, dental_issues, r_fmt)
            ws_camps.write_number(row_idx, 11, pallor_pos, r_fmt)
            ws_camps.write_number(row_idx, 12, total_referrals, r_fmt)
            ws_camps.write_string(row_idx, 13, str(c.get("created_at") or ""), r_fmt)

        wb.close()
        output.seek(0)
        return output.getvalue()

