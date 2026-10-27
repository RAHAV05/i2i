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
            "original_ai_extraction": data.get("original_ai_extraction") or data.get("structured_record", {}),
            "quality_warning_bypassed": data.get("quality_warning_bypassed", []),
            "target_group": data.get("target_group") or (camp.get("target_group") if camp else "adult"),
            "is_paediatric": 1 if (data.get("is_paediatric") or (camp and camp.get("target_group") == "paediatric")) else 0,
            "paediatric_alerts": data.get("paediatric_alerts", []),
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

    @staticmethod
    def _write_attributes_sheet(wb, form_id: str, tab_color: str, header_bg: str) -> None:
        """Write declarative Form Structure & Attributes Dictionary as Sheet 2 for clinical auditability."""
        from backend.services.template_engine import default_template_engine

        ws_attrs = wb.add_worksheet("Form Attributes")
        ws_attrs.set_tab_color(tab_color)
        ws_attrs.freeze_panes(1, 2)

        attr_hdr_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': header_bg, 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#E2E8F0', 'text_wrap': True
        })
        attr_cell_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9,
            'valign': 'vcenter', 'border': 1, 'border_color': '#E2E8F0'
        })
        attr_cell_alt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'valign': 'vcenter',
            'bg_color': '#F8FAFC', 'border': 1, 'border_color': '#E2E8F0'
        })
        badge_req_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#FEF2F2', 'font_color': '#DC2626',
            'border': 1, 'border_color': '#FECACA'
        })
        badge_opt_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#F3F4F6', 'font_color': '#6B7280',
            'border': 1, 'border_color': '#E5E7EB'
        })

        cols = [
            ("Attribute Key", 22),
            ("Clinical Label", 34),
            ("Page", 10),
            ("Page Type", 14),
            ("Section", 26),
            ("Data Type", 16),
            ("Recorded By", 16),
            ("Mandatory", 12),
            ("Excel Export Header", 24),
            ("Validation Rule", 26)
        ]
        ws_attrs.set_row(0, 32)
        for c_idx, (col_name, col_width) in enumerate(cols):
            ws_attrs.set_column(c_idx, c_idx, col_width)
            ws_attrs.write_string(0, c_idx, col_name, attr_hdr_fmt)

        attributes = default_template_engine.get_form_attributes(form_id)
        for row_idx, a in enumerate(attributes, start=1):
            r_fmt = attr_cell_alt if row_idx % 2 == 0 else attr_cell_fmt
            ws_attrs.set_row(row_idx, 20)
            ws_attrs.write_string(row_idx, 0, sanitize_csv_cell(a.get("key") or ""), r_fmt)
            ws_attrs.write_string(row_idx, 1, sanitize_csv_cell(a.get("label") or ""), r_fmt)
            ws_attrs.write_number(row_idx, 2, int(a.get("page_number") or 1), r_fmt)
            ws_attrs.write_string(row_idx, 3, sanitize_csv_cell(str(a.get("page_type") or "").capitalize()), r_fmt)
            ws_attrs.write_string(row_idx, 4, sanitize_csv_cell(a.get("section_title") or ""), r_fmt)
            ws_attrs.write_string(row_idx, 5, sanitize_csv_cell(a.get("type") or ""), r_fmt)
            ws_attrs.write_string(row_idx, 6, sanitize_csv_cell(str(a.get("writer") or "").capitalize()), r_fmt)
            is_req = bool(a.get("required"))
            ws_attrs.write_string(row_idx, 7, "Required" if is_req else "Optional", badge_req_fmt if is_req else badge_opt_fmt)
            ws_attrs.write_string(row_idx, 8, sanitize_csv_cell(a.get("export_column") or a.get("key") or ""), r_fmt)
            ws_attrs.write_string(row_idx, 9, sanitize_csv_cell(a.get("validation") or ""), r_fmt)

    def generate_excel_export(
        self,
        camp_id: Optional[str] = None,
        only_reviewed: bool = False,
        form_type: Optional[str] = None
    ) -> bytes:
        """Generate formatted Excel (.xlsx) workbook with columns in exact form field order (Page 1 → Page 2)."""
        status_filter = "reviewed" if only_reviewed else None
        records = database.list_records(camp_id=camp_id, status=status_filter, limit=10000, db_path=self.db_path)
        camps = database.list_camps_with_stats(self.db_path)
        selected_camp = None
        if camp_id:
            matching = [c for c in camps if c["id"] == camp_id]
            if matching:
                selected_camp = matching[0]
            camps = matching

        # Route to segregated paediatric export if explicitly requested or camp target_group is paediatric
        norm_type = (form_type or "").strip().lower()
        if norm_type in ("paediatric", "pediatric", "school"):
            is_paediatric = True
        elif norm_type in ("adult", "university", "general"):
            is_paediatric = False
        else:
            is_paediatric = bool(selected_camp and selected_camp.get("target_group") == "paediatric")
            if not is_paediatric and records and any(r.get("target_group") == "paediatric" or r.get("is_paediatric") for r in records):
                is_paediatric = True

        if is_paediatric:
            return self._generate_paediatric_excel_export(camp_id=camp_id, camp=selected_camp, records=records, only_reviewed=only_reviewed)

        return self._generate_adult_excel_export(camp_id=camp_id, camp=selected_camp, records=records, camps=camps, only_reviewed=only_reviewed)

    def generate_adult_excel_export(self, camp_id: Optional[str] = None, only_reviewed: bool = False) -> bytes:
        """Explicitly generate Adult Health Screening (.xlsx) workbook."""
        return self.generate_excel_export(camp_id=camp_id, only_reviewed=only_reviewed, form_type="adult")

    def generate_paediatric_excel_export(self, camp_id: Optional[str] = None, only_reviewed: bool = False) -> bytes:
        """Explicitly generate Paediatric School Health Screening (.xlsx) workbook."""
        return self.generate_excel_export(camp_id=camp_id, only_reviewed=only_reviewed, form_type="paediatric")

    def _generate_adult_excel_export(
        self,
        camp_id: Optional[str],
        camp: Optional[Dict[str, Any]],
        records: List[Dict[str, Any]],
        camps: List[Dict[str, Any]],
        only_reviewed: bool = False
    ) -> bytes:
        """Generate formatted Adult Health (.xlsx) workbook with Form Attributes sheet and Camp Summary."""
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

            # ── Paediatric Screening & Consent (Phase 6 & 7) ──────────────────
            ("Consent Status",                  18, True),
            ("Paediatric BMI Centile",          22, False),
            ("Paediatric BMI Staging",          24, False),
            ("Paediatric BP Centile",           22, False),
            ("Paediatric BP Staging",           24, False),
            ("ATR Caliper Angle",               18, False),
            ("Paediatric Screening Alerts",     28, False),
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

                # Paediatric Screening & Consent (Phase 6 & 7)
                "Consent Withheld" if (r.get("consent_given") is False or _val(p.get("consent_screening")).lower() in ("no", "withheld")) else "Given",
                str((sr.get("paediatric_screening") or {}).get("bmi_centile") or r.get("paediatric_bmi_centile") or ""),
                str((sr.get("paediatric_screening") or {}).get("bmi_classification") or r.get("paediatric_bmi_classification") or ""),
                str((sr.get("paediatric_screening") or {}).get("bp_percentile") or r.get("paediatric_bp_percentile") or ""),
                str((sr.get("paediatric_screening") or {}).get("bp_classification") or r.get("paediatric_bp_classification") or ""),
                str(((sr.get("paediatric_screening") or {}).get("atr_classification") or {}).get("atr_value") or _val(h.get("adams_forward_bend_test_atr")) or ""),
                ", ".join(r.get("paediatric_alerts") or (sr.get("paediatric_screening") or {}).get("paediatric_alerts") or []),
            ]

            ws.set_row(row_idx, 20)
            for c_idx, val in enumerate(row_data):
                fmt_to_use = st_fmt if c_idx == 2 else row_fmt
                ws.write_string(row_idx, c_idx, sanitize_csv_cell(val), fmt_to_use)

        # ── Sheet 2: Form Structure & Attributes Dictionary ──────────────────
        self._write_attributes_sheet(wb, "adult", tab_color='#AD1457', header_bg='#880E4F')

        # ── Sheet 3: Camp Summary ─────────────────────────────────────────────
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

    def _generate_paediatric_excel_export(
        self,
        camp_id: Optional[str],
        camp: Optional[Dict[str, Any]],
        records: List[Dict[str, Any]],
        only_reviewed: bool = False
    ) -> bytes:
        """Generate specialized Paediatric Health Screening (.xlsx) workbook for school camps."""
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True, 'remove_timezone': True})

        # Formats
        header_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': '#0288D1', 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#81D4FA', 'text_wrap': True
        })
        section_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': '#01579B', 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#0277BD', 'text_wrap': True
        })
        cell_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9,
            'valign': 'vcenter', 'border': 1, 'border_color': '#E1F5FE'
        })
        cell_alt_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'valign': 'vcenter',
            'bg_color': '#F1F8E9', 'border': 1, 'border_color': '#E1F5FE'
        })
        alert_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'bg_color': '#FFEBEE', 'font_color': '#C62828',
            'border': 1, 'border_color': '#FFCDD2'
        })
        status_reviewed_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#E8F5E9', 'font_color': '#2E7D32',
            'border': 1, 'border_color': '#C8E6C9'
        })
        status_to_check_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#FFF8E1', 'font_color': '#F57F17',
            'border': 1, 'border_color': '#FFECB3'
        })

        sheet_name = "Paediatric Records"
        if camp:
            c_name = re.sub(r'[:/\\?*\[\]]', '', str(camp.get("name", "School Records")))[:30].strip()
            sheet_name = c_name or "Paediatric Records"

        ws = wb.add_worksheet(sheet_name)
        ws.set_tab_color('#0288D1')
        ws.set_row(0, 42)
        ws.freeze_panes(1, 4)

        # 55-column School Screening Schema
        columns = [
            ("Record ID", 18, False),
            ("School / Institution", 24, False),
            ("Class & Section", 14, False),
            ("Admission / Roll No", 16, False),
            ("Student Name", 22, False),
            ("Date of Birth", 12, False),
            ("Age (Years)", 10, False),
            ("Gender", 8, False),
            ("Blood Group", 8, False),
            ("Parent / Guardian", 20, False),
            ("Emergency Contact", 15, False),
            ("Residential Address", 26, False),
            ("Birth History", 18, False),
            ("Developmental Milestones", 20, False),
            ("Immunization Status", 18, False),
            ("Dietary Habits", 15, False),
            ("Known Allergies", 18, False),
            ("Height (cm)", 10, True),
            ("Weight (kg)", 10, False),
            ("BMI (kg/m²)", 10, False),
            ("BMI Centile (IAP LMS)", 14, False),
            ("BMI Classification", 16, False),
            ("Blood Pressure", 12, False),
            ("BP Centile (AAP 2017)", 14, False),
            ("BP Classification", 16, False),
            ("Pulse (bpm)", 10, False),
            ("Scoliosis ATR (°)", 14, True),
            ("Scoliosis Risk Status", 16, False),
            ("Head Circumference (cm)", 14, False),
            ("MUAC (cm)", 12, False),
            ("Vital Signs Status", 14, False),
            ("Gait & Locomotor", 16, False),
            ("Ears / Hearing", 14, False),
            ("Otoscopy Findings", 18, False),
            ("Oral Cavity & Throat", 18, False),
            ("Lymphadenopathy", 14, False),
            ("Vision Distant Right", 12, False),
            ("Vision Distant Left", 12, False),
            ("Color Vision", 12, False),
            ("Squint / Strabismus", 14, False),
            ("Dental Caries", 14, False),
            ("Dental Malocclusion", 14, False),
            ("Oral Hygiene", 14, False),
            ("Dental Advice", 18, False),
            ("Systemic Findings", 22, False),
            ("General Clinical Notes", 22, False),
            ("Referral Specialist", 18, False),
            ("Referral Reason", 20, False),
            ("Paediatric Alert Flags", 28, True),
            ("Parental Consent", 14, False),
            ("Screening Doctor", 18, False),
            ("Review Status", 12, False),
            ("Doctor Notes", 22, False),
            ("Reviewed By", 16, False),
            ("Reviewed At", 18, False)
        ]

        for col_idx, (col_name, col_width, is_divider) in enumerate(columns):
            ws.set_column(col_idx, col_idx, col_width)
            fmt = section_fmt if is_divider else header_fmt
            ws.write_string(0, col_idx, col_name, fmt)

        # Helper to unpack extraction fields
        def _get(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, dict):
                v = val.get("value")
                return "" if v is None else str(v).strip()
            return str(val).strip()

        for row_idx, r in enumerate(records, start=1):
            row_fmt = cell_alt_fmt if row_idx % 2 == 0 else cell_fmt
            sr = r.get("structured_record") or {}
            p = sr.get("personal_information") or {}
            v = sr.get("vitals") or {}
            d = sr.get("dental_assessment") or {}
            vis = sr.get("vision_assessment") or {}
            ref = sr.get("referral") or {}
            hist = sr.get("medical_history") or {}
            paed = sr.get("paediatric_growth") or {}
            pscreen = sr.get("paediatric_screening") or {}

            # Calculated alerts & flags
            alerts = r.get("paediatric_alerts") or pscreen.get("alerts") or []
            if isinstance(alerts, str):
                try:
                    alerts = json.loads(alerts)
                except Exception:
                    alerts = [alerts]
            alerts_str = ", ".join(alerts) if alerts else "None"

            atr_val = _get(paed.get("spinal_caliper_atr") or v.get("spinal_caliper_atr") or r.get("spinal_caliper_atr"))
            atr_status = "Negative / Normal"
            try:
                if atr_val and float(re.findall(r'[\d.]+', str(atr_val))[0]) >= 5.0:
                    atr_status = "ALERT: Scoliosis Suspect (ATR >= 5°)"
            except Exception:
                pass

            status = r.get("status") or "needs_review"
            st_fmt = status_reviewed_fmt if status in ("reviewed", "approved") else status_to_check_fmt

            row_data = [
                r.get("id") or "",
                _get(p.get("institution") or p.get("school_name") or r.get("school_name")),
                _get(r.get("class_and_section") or f"{_get(p.get('course'))} {_get(p.get('section'))}".strip()),
                _get(p.get("admission_id") or r.get("admission_id")),
                _get(p.get("full_name") or r.get("person_name")),
                _get(p.get("dob") or r.get("dob")),
                _get(p.get("age") or r.get("age")),
                _get(p.get("gender") or r.get("gender")),
                _get(p.get("blood_group") or r.get("blood_group")),
                _get(p.get("parent_or_spouse") or r.get("parent_guardian_name")),
                _get(p.get("mobile") or r.get("emergency_contact_phone")),
                _get(p.get("address") or r.get("address")),
                _get(sr.get("birth_history") or r.get("birth_history")),
                _get(sr.get("milestones") or r.get("milestones")),
                _get(sr.get("immunization_status") or r.get("immunization_status")),
                _get(sr.get("dietary_habits") or r.get("dietary_habits")),
                _get(hist.get("allergy") or r.get("known_allergies")),
                _get(v.get("height_cm") or r.get("height")),
                _get(v.get("weight_kg") or r.get("weight")),
                _get(paed.get("bmi") or pscreen.get("bmi") or r.get("bmi")),
                _get(paed.get("bmi_centile") or pscreen.get("bmi_centile") or r.get("bmi_centile")),
                _get(paed.get("bmi_classification") or pscreen.get("bmi_classification") or r.get("bmi_classification")),
                _get(v.get("bp") or r.get("blood_pressure")),
                _get(paed.get("bp_percentile") or pscreen.get("bp_percentile") or r.get("bp_percentile")),
                _get(paed.get("bp_classification") or pscreen.get("bp_classification") or r.get("bp_classification")),
                _get(v.get("pulse") or r.get("pulse")),
                atr_val,
                atr_status,
                _get(v.get("head_circumference") or r.get("head_circumference")),
                _get(v.get("muac") or r.get("muac")),
                _get(r.get("vital_signs_status")),
                _get(sr.get("gait_status") or r.get("gait_status")),
                _get(sr.get("systemic_examination", {}).get("ear") or r.get("ears_hearing_status")),
                _get(sr.get("otoscopy_findings") or r.get("otoscopy_findings")),
                _get(sr.get("systemic_examination", {}).get("throat") or r.get("oral_cavity_throat")),
                _get(sr.get("general_signs", {}).get("lymphadenopathy") or r.get("lymph_nodes")),
                _get(vis.get("right_eye") or r.get("vision_distant_right")),
                _get(vis.get("left_eye") or r.get("vision_distant_left")),
                _get(sr.get("color_vision") or r.get("color_vision")),
                _get(sr.get("squint_strabismus") or r.get("squint_strabismus")),
                _get(d.get("dental_abnormality") or r.get("dental_caries")),
                _get(sr.get("malocclusion") or r.get("malocclusion")),
                _get(d.get("dental_status") or r.get("oral_hygiene")),
                _get(d.get("dental_advice")),
                _get(r.get("systemic_findings") or sr.get("systemic_examination")),
                _get(r.get("examination_details")),
                _get(ref.get("specialist") or r.get("referral_specialist")),
                _get(ref.get("reason") or r.get("referral_reason")),
                alerts_str,
                "Given" if r.get("consent_given", True) else "Consent Withheld",
                r.get("scanned_by") or "Dr. Priya",
                status,
                r.get("reviewer_notes") or "",
                r.get("reviewed_by") or "",
                r.get("reviewed_at") or ""
            ]

            ws.set_row(row_idx, 20)
            for c_idx, val in enumerate(row_data):
                fmt_to_use = row_fmt
                if c_idx == 51:
                    fmt_to_use = st_fmt
                elif c_idx in (27, 48) and ("ALERT" in str(val) or len(alerts) > 0):
                    fmt_to_use = alert_fmt
                ws.write_string(row_idx, c_idx, sanitize_csv_cell(val), fmt_to_use)

        # Sheet 2: Form Structure & Attributes Dictionary
        self._write_attributes_sheet(wb, "paediatric", tab_color='#0277BD', header_bg='#01579B')

        # Sheet 3: Paediatric Camp Summary
        ws_summary = wb.add_worksheet("Paediatric Summary")
        ws_summary.set_tab_color('#01579B')
        summary_cols = [
            ("School / Camp Name", 28),
            ("Camp Date", 14),
            ("Total Screened", 14),
            ("Needs Review", 12),
            ("Reviewed & Approved", 16),
            ("Underweight (<3rd %ile)", 18),
            ("Overweight/Obese (>85th %ile)", 20),
            ("Hypertension Suspects", 18),
            ("Scoliosis Alerts (ATR >= 5°)", 20),
            ("Dental Caries Cases", 16),
            ("Vision Impairment Cases", 18),
            ("Parental Consent Withheld", 18)
        ]
        for col_idx, (col_name, col_width) in enumerate(summary_cols):
            ws_summary.set_column(col_idx, col_idx, col_width)
            ws_summary.write_string(0, col_idx, col_name, header_fmt)

        c_name = camp.get("name") if camp else "School Screening Camp"
        c_date = camp.get("date_str") if camp else "Today"
        total_scanned = len(records)
        needs_review = sum(1 for r in records if r.get("status") == "needs_review")
        reviewed = sum(1 for r in records if r.get("status") in ("reviewed", "approved"))
        underweight = sum(1 for r in records if "underweight" in str(r.get("bmi_classification", "")).lower())
        overweight = sum(1 for r in records if any(k in str(r.get("bmi_classification", "")).lower() for k in ("overweight", "obese")))
        htn = sum(1 for r in records if any(k in str(r.get("bp_classification", "")).lower() for k in ("stage 1", "stage 2", "hypertension")))
        scoliosis = sum(1 for r in records if any("scoliosis" in str(a).lower() for a in r.get("paediatric_alerts", [])))
        caries = sum(1 for r in records if "caries" in str(r.get("dental_caries", "")).lower())
        vision_issues = sum(1 for r in records if str(r.get("vision_distant_right", "")).strip() not in ("", "6/6") or str(r.get("vision_distant_left", "")).strip() not in ("", "6/6"))
        consent_withheld = sum(1 for r in records if r.get("consent_given") is False)

        sum_row = [
            sanitize_csv_cell(c_name),
            sanitize_csv_cell(c_date),
            total_scanned,
            needs_review,
            reviewed,
            underweight,
            overweight,
            htn,
            scoliosis,
            caries,
            vision_issues,
            consent_withheld
        ]
        ws_summary.set_row(1, 20)
        for c_idx, val in enumerate(sum_row):
            if isinstance(val, (int, float)):
                ws_summary.write_number(1, c_idx, val, cell_fmt)
            else:
                ws_summary.write_string(1, c_idx, str(val), cell_fmt)

        wb.close()
        output.seek(0)
        return output.getvalue()

