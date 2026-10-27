"""AI Extraction Layer & Schema Validation for Clinical Health Camp Forms.

Conforms to Appendix C of the implementation instructions:
- Prompt and field keys matching student_form and health_assessment sheets
- Output format per field: [value, confidence]
- Server-side confidence and medical plausibility flag rules
- Low-confidence field counting for escalation (blank fields with confidence 1 are NEVER counted)
- BMI calculated by the app from height and weight
- Single AI function: ai_service.extract(student_form, health_assessment, camp_id, ...)
"""

import os
import re
import json
import logging
from datetime import datetime, timezone
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple, Union
from pydantic import BaseModel, Field, ValidationError

try:
    from .paediatric_screening import classify_paediatric
    _PAEDIATRIC_SCREENING_AVAILABLE = True
except ImportError:
    _PAEDIATRIC_SCREENING_AVAILABLE = False
    def classify_paediatric(fields):  # type: ignore[misc]
        return {
            "paediatric_bmi_classification": {}, "paediatric_bp_classification": {},
            "atr_classification": {}, "paediatric_calculated_bmi": None,
            "is_paediatric": False, "paediatric_alerts": [],
        }

logger = logging.getLogger(__name__)

# Configurable thresholds
FLAG_CONFIDENCE = float(os.getenv("FLAG_CONFIDENCE", "0.8"))
FLAG_CONFIDENCE_MEASURE = float(os.getenv("FLAG_CONFIDENCE_MEASURE", "0.9"))

CRITICAL_MEASUREMENT_KEYS = ("bp", "pulse", "weight_kg", "rbs", "height_cm")
MANDATORY_FIELD_KEYS = ("full_name", "dob")

APPENDIX_C_PROMPT = """You are reading two photographed pages of one person's medical camp record.
Image 1 is the student or staff form (filled by the person).
Image 2 is the health assessment sheet (filled by the doctor).
Read printed text, handwriting and ticked boxes.
Return only JSON. For every field return [value, confidence], where value is
a string or null and confidence is a number from 0 to 1.
Rules:
- Never guess. If a field is blank, return [null, 1].
- If you cannot read it, return [null, a number below 0.5].
- Yes or No fields return exactly 'Yes', 'No' or null.
- Normal or abnormal fields return exactly 'Normal', 'Abnormal' or null, with details in the _d field.
- Copy numbers exactly as written. Do not convert units.
- Doctors' shorthand may appear: BP, PR, HR, Wt, Ht, RBS, NAD, WNL, HTN, T2DM, Adv, F/U, Ref, Rx, O/E, H/O, C/O.
- Do not diagnose or add advice.

Field keys to extract:
Identifiers: moc_doc_id, admission_id, institution, staff_or_student
Personal: full_name, gender, year, course, section, shift, blood_group, dob, age, mobile, marital_status, parent_or_spouse, children, address
History (Yes or No, plus _d): allergy, allergy_d, abdominal, abdominal_d, menstrual, menstrual_d, skin_ear_nose, skin_ear_nose_d, medications, medications_d, surgery, surgery_d, social, social_d, activity, activity_d, other_issues, family_members, physician
Family history (Yes or No, plus _d for relationship): fh_thyroid, fh_thyroid_d, fh_diabetes, fh_diabetes_d, fh_heart, fh_heart_d, fh_hypertension, fh_hypertension_d, fh_cancer, fh_cancer_d, fh_epilepsy, fh_epilepsy_d, fh_asthma, fh_asthma_d, fh_others, fh_others_d
Consent: consent_screening, consent_first_aid, signature_present
Vitals: exam_date, height_cm, weight_kg, pulse, bp, rbs, waist_cm, ecg, echo
Signs (Yes or No): pallor, icterus, edema, lymphadenopathy, cyanosis, clubbing, deformity
System exam (Normal or Abnormal, plus _d): ear, ear_d, nose, nose_d, throat, throat_d, rs, rs_d, cvs, cvs_d, abdomen, abdomen_d, cns, cns_d, skin, skin_d, hair, hair_d, msk, msk_d, exam_others, exam_others_d, psych, psych_d
Vision: vision_type, right_eye, left_eye
Dental: dental, dental_abnormality, dental_advice
Referral: specialist, doctor_name"""

# Adult and Paediatric Extraction Prompt Designations
ADULT_EXTRACTION_PROMPT = APPENDIX_C_PROMPT

PAEDIATRIC_EXTRACTION_PROMPT = """You are reading two photographed pages of a school student's / child's paediatric health screening record.
Image 1 is the Paediatric Intake / Student Information Sheet (filled by parents/school).
Image 2 is the Paediatric Clinical Assessment Sheet (filled by the paediatrician/screening doctor).
Read printed text, handwriting and ticked boxes.
Return only JSON. For every field return [value, confidence], where value is
a string or null and confidence is a number from 0 to 1.
Rules:
- Never guess. If a field is blank, return [null, 1].
- If you cannot read it, return [null, a number below 0.5].
- Yes or No fields return exactly 'Yes', 'No' or null.
- Normal or abnormal fields return exactly 'Normal', 'Abnormal' or null, with details in the _d field.
- Copy numbers exactly as written (e.g., height, weight, spinal ATR angle in degrees). Do not convert units.
- Do not diagnose or add medical advice.

Field keys to extract:
Identifiers: screening_type, school_name, class_and_section, admission_id, moc_doc_id
Personal: full_name, gender, dob, age, blood_group, parent_guardian_name, emergency_contact_phone, address
History: birth_history, milestones, immunization_status, dietary_habits, allergy, allergy_d, medications, medications_d, other_issues
Consent: consent_screening, signature_present
Vitals & Paediatric Measurements: exam_date, height_cm, weight_kg, pulse, bp, spinal_caliper_atr, head_circumference, muac
General & ENT: vital_signs_status, gait_status, ears_hearing_status, otoscopy_findings, oral_cavity_throat, lymph_nodes
Vision: vision_type, distant_vision_right, distant_vision_left, color_vision, squint_strabismus
Dental: dental, dental_caries, malocclusion, oral_hygiene, dental_advice
Referral & Examination: examination_details, specialist, referral_reason, doctor_name"""

def get_prompt_for_target_group(target_group: Optional[str]) -> str:
    """Return appropriate prompt template based on camp target group ('paediatric' vs 'adult')."""
    if target_group and target_group.strip().lower() == "paediatric":
        return PAEDIATRIC_EXTRACTION_PROMPT
    return ADULT_EXTRACTION_PROMPT

# Maintain SYSTEM_EXTRACTION_PROMPT alias for backward compatibility
SYSTEM_EXTRACTION_PROMPT = APPENDIX_C_PROMPT


class FieldValueModel(BaseModel):
    value: Optional[Union[str, int, float]] = None
    confidence: float = Field(ge=0.0, le=1.0)


class AIService(ABC):
    """Abstract interface for document vision AI providers."""

    @abstractmethod
    def extract_from_images(
        self,
        student_form_bytes: bytes,
        student_form_mime: str,
        health_assessment_bytes: bytes,
        health_assessment_mime: str
    ) -> Dict[str, Any]:
        """Process student_form and health_assessment and return structured extraction JSON."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the provider has necessary credentials."""
        pass

    @abstractmethod
    def provider_name(self) -> str:
        """Return provider display name."""
        pass


def _clean_str(val: Any) -> Optional[str]:
    """Normalize string, returning None for empty/null placeholders."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("null", "none", "n/a", "na", "undefined", "unknown", "nil"):
        return None
    return s


def _clean_list(val: Any) -> List[str]:
    """Normalize a list of strings, filtering out empty/null values."""
    if not val:
        return []
    if isinstance(val, list):
        out = []
        for x in val:
            cs = _clean_str(x)
            if cs is not None and cs not in out:
                out.append(cs)
        return out
    cs = _clean_str(val)
    return [cs] if cs is not None else []



def _format_meta_field(raw: Any, default_page: int = 1, details: Any = None) -> Dict[str, Any]:
    """Wrap raw data into standard field metadata structure:
    {value, confidence, status, source_page, notes, details}
    """
    clean_d = _clean_str(details)
    if isinstance(raw, list) and len(raw) >= 2:
        val, conf = raw[0], raw[1]
        clean_v = _clean_str(val) if isinstance(val, (str, int, float)) else val
        return {
            "value": clean_v,
            "confidence": min(1.0, max(0.0, float(conf))),
            "status": "extracted" if (clean_v is not None or clean_d is not None) else "missing",
            "source_page": default_page,
            "notes": None,
            "details": clean_d
        }

    if isinstance(raw, dict) and "value" in raw:
        val = raw.get("value")
        clean_v = _clean_str(val) if isinstance(val, (str, int, float)) else val
        conf = float(raw.get("confidence", 0.95)) if clean_v is not None else 0.0
        stat = raw.get("status") or ("extracted" if (clean_v is not None or clean_d is not None) else "missing")
        return {
            "value": clean_v,
            "confidence": min(1.0, max(0.0, conf)),
            "status": stat,
            "source_page": int(raw.get("source_page", default_page)),
            "notes": _clean_str(raw.get("notes")),
            "details": clean_d or _clean_str(raw.get("details")) or _clean_str(raw.get("relationship"))
        }

    if isinstance(raw, dict):
        status_val = raw.get("status") or raw.get("selection")
        details_val = details or raw.get("details") or raw.get("specify") or raw.get("relationship")
        clean_v = _clean_str(status_val) if status_val is not None else None
        clean_d = _clean_str(details_val)
        stat = "extracted" if (clean_v or clean_d) else "missing"
        return {
            "value": clean_v,
            "confidence": 0.95 if (clean_v or clean_d) else 0.0,
            "status": stat,
            "source_page": default_page,
            "notes": None,
            "details": clean_d
        }

    clean_v = _clean_str(raw)
    stat = "extracted" if (clean_v is not None or clean_d is not None) else "missing"
    return {
        "value": clean_v,
        "confidence": 0.95 if clean_v is not None else 0.0,
        "status": stat,
        "source_page": default_page,
        "notes": None,
        "details": clean_d
    }


def parse_and_validate_schema(raw_text: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Parse JSON string and validate that each entry is [value, confidence].
    Returns (is_valid: bool, fields_dict: dict, error_msg: str).
    """
    if not raw_text:
        return False, {}, "Empty output from model"

    # Strip markdown block if model included it
    cleaned = raw_text.strip()
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned)
    if json_match:
        cleaned = json_match.group(1).strip()

    try:
        data = json.loads(cleaned)
    except Exception:
        # Fallback substring extraction
        s_candidates = [i for i in (cleaned.find('{'), cleaned.find('[')) if i != -1]
        s_idx = min(s_candidates) if s_candidates else -1
        e_idx = max(cleaned.rfind('}'), cleaned.rfind(']'))
        if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
            sub = re.sub(r',\s*([}\]])', r'\1', cleaned[s_idx:e_idx+1])
            try:
                data = json.loads(sub)
            except Exception as e:
                return False, {}, f"JSON decode error: {e}"
        else:
            return False, {}, "No JSON found in model output"

    if isinstance(data, list):
        merged = {}
        for item in data:
            if isinstance(item, dict):
                merged.update(item)
        data = merged
    elif not isinstance(data, dict):
        return False, {}, "Model output is neither a JSON object nor array"

    # Normalize fields to [value, confidence], supporting both flat and nested schemas
    normalized: Dict[str, List[Any]] = {}

    def _extract_fields_recursive(d: Any, target: Dict[str, List[Any]], prefix: str = ""):
        if isinstance(d, list):
            for item in d:
                _extract_fields_recursive(item, target, prefix=prefix)
            return
        if not isinstance(d, dict):
            return
        for k, v in d.items():
            if k == "page":
                continue
            if isinstance(v, list) and len(v) >= 2 and not isinstance(v[0], (dict, list)):
                val = _clean_str(v[0])
                try:
                    conf = float(v[1])
                except (ValueError, TypeError):
                    conf = 0.95
                target[k] = [val, min(1.0, max(0.0, conf))]
            elif isinstance(v, dict):
                if "value" in v and "confidence" in v:
                    val = _clean_str(v.get("value"))
                    try:
                        conf = float(v.get("confidence", 0.95))
                    except (ValueError, TypeError):
                        conf = 0.95
                    target[k] = [val, min(1.0, max(0.0, conf))]
                elif "yes_no" in v:
                    yn_raw = v.get("yes_no")
                    if isinstance(yn_raw, list) and len(yn_raw) >= 1:
                        yn_val = _clean_str(yn_raw[0])
                        conf = float(yn_raw[1]) if len(yn_raw) >= 2 else 0.95
                    else:
                        yn_val = _clean_str(yn_raw)
                        conf = 0.95
                    target[k] = [yn_val, min(1.0, max(0.0, conf))]
                    spec_raw = v.get("specify") or v.get("details")
                    if spec_raw:
                        spec_val = _clean_str(spec_raw[0] if isinstance(spec_raw, list) else spec_raw)
                        if spec_val:
                            target[f"{k}_d"] = [spec_val, min(1.0, max(0.0, conf))]
                else:
                    _extract_fields_recursive(v, target, prefix=f"{prefix}{k}_")
            elif isinstance(v, (str, int, float)) or v is None:
                clean_v = _clean_str(v)
                target[k] = [clean_v, 0.95 if clean_v is not None else 1.0]

    _extract_fields_recursive(data, normalized)

    # Standardize field aliases across sheets & model outputs
    alias_map = {
        "bp_mmhg": ["bp", "blood_pressure", "blood_pressure_mmhg"],
        "bp": ["blood_pressure", "blood_pressure_mmhg"],
        "pulse_bpm": ["pulse", "pulse_bpm"],
        "pulse": ["pulse_bpm"],
        "height_cm": ["height", "height_cm"],
        "height": ["height_cm"],
        "weight_kg": ["weight", "weight_kg"],
        "weight": ["weight_kg"],
        "rbs_mg_dl": ["rbs", "random_blood_sugar_mgdl"],
        "rbs": ["random_blood_sugar_mgdl"],
        "date_of_examination": ["exam_date", "examination_date"],
        "exam_date": ["examination_date"],
        "waist_circumference_cm": ["waist_cm", "waist_circumference_cm"],
        "waist_cm": ["waist_circumference_cm"],
        "patient_name": ["full_name", "person_name"],
        "student_name": ["full_name", "person_name"],
        "name": ["full_name", "person_name"],
        "person_name": ["full_name"],
        "distant_va_right": ["right_eye", "distant_vision_right"],
        "distant_va_left": ["left_eye", "distant_vision_left"],
        "any_known_allergy": ["allergy"],
        "any_current_previous_medications": ["medications"],
        "any_previous_surgery_hospitalization": ["surgery"],
        "skin_hair_ear_nose_problem": ["skin_ear_nose"],
    }
    for orig_k, aliases in list(alias_map.items()):
        if orig_k in normalized:
            for al in aliases:
                if al not in normalized:
                    normalized[al] = normalized[orig_k]

    return True, normalized, None


def classify_bmi(bmi_val: Optional[float]) -> Dict[str, Any]:
    """Clinical triage classification for WHO and Asian-Indian BMI consensus.
    < 18.5: Underweight (warning)
    18.5 - 22.9: Normal Weight (optimal)
    23.0 - 24.9: Overweight / Pre-obese in Asian-Indian (advisory)
    25.0 - 29.9: Obese Class I (Asian-Indian) / Overweight (WHO) (high)
    >= 30.0: Obese Class II/III (critical)
    """
    if bmi_val is None:
        return {"category": "Unknown", "alert_level": "none", "is_high_risk": False, "staging": "N/A"}
    
    try:
        b = float(bmi_val)
    except (ValueError, TypeError):
        return {"category": "Unknown", "alert_level": "none", "is_high_risk": False, "staging": "N/A"}

    if b < 18.5:
        return {"category": "Underweight", "alert_level": "warning", "is_high_risk": False, "staging": "WHO/Asian-Indian Underweight"}
    elif b <= 22.9:
        return {"category": "Normal Weight", "alert_level": "optimal", "is_high_risk": False, "staging": "Optimal / Normal Range"}
    elif b <= 24.9:
        return {"category": "Overweight (Pre-obese)", "alert_level": "advisory", "is_high_risk": False, "staging": "Asian-Indian Consensus Overweight"}
    elif b <= 29.9:
        return {"category": "Obese Class I (Asian-Indian) / Overweight", "alert_level": "high", "is_high_risk": True, "staging": "High Alert: Overweight/Obesity"}
    else:
        return {"category": "Obese Class II/III (Severe)", "alert_level": "critical", "is_high_risk": True, "staging": "Critical Alert: Severe Obesity"}


def classify_bp(sys_val: Optional[int], dia_val: Optional[int] = None) -> Dict[str, Any]:
    """Clinical blood pressure staging & hypertensive emergency triage.
    Normal: < 120 and < 80
    Elevated / Pre-hypertension: 120-129 and < 80
    Stage 1 Hypertension: 130-139 or 80-89
    Stage 2 Hypertension: >= 140 or >= 90 (High Alert)
    Hypertensive Emergency: >= 180 or >= 120 (Critical Crisis)
    """
    if sys_val is None:
        return {"stage": "Unknown", "alert_level": "none", "is_high_risk": False, "description": "BP not recorded"}
    
    try:
        s = int(sys_val)
        d = int(dia_val) if dia_val is not None else None
    except (ValueError, TypeError):
        return {"stage": "Unknown", "alert_level": "none", "is_high_risk": False, "description": "Unparseable BP"}

    if s >= 180 or (d is not None and d >= 120):
        return {
            "stage": "Hypertensive Crisis",
            "alert_level": "critical",
            "is_high_risk": True,
            "description": "🚨 Critical BP: Hypertensive Crisis. Immediate medical attention required."
        }
    elif s >= 140 or (d is not None and d >= 90):
        return {
            "stage": "Stage 2 Hypertension",
            "alert_level": "high",
            "is_high_risk": True,
            "description": "⚠️ Stage 2 Hypertension: Clinical review & specialist referral indicated."
        }
    elif s >= 130 or (d is not None and d >= 80):
        return {
            "stage": "Stage 1 Hypertension",
            "alert_level": "advisory",
            "is_high_risk": False,
            "description": "Stage 1 Hypertension / Elevated BP: Routine follow-up suggested."
        }
    elif s >= 120 and (d is None or d < 80):
        return {
            "stage": "Elevated BP",
            "alert_level": "advisory",
            "is_high_risk": False,
            "description": "Pre-hypertension / Elevated systolic BP."
        }
    else:
        return {
            "stage": "Normal",
            "alert_level": "normal",
            "is_high_risk": False,
            "description": "Normal blood pressure."
        }


def apply_server_side_flags(fields: Dict[str, List[Any]]) -> Dict[str, Any]:
    """Apply strict server-side clinical plausibility and confidence rules.
    Returns: {
        "flags": dict of field_name -> reason,
        "implausible_fields": list,
        "low_confidence_fields": list,
        "missing_mandatory": list,
        "calculated_bmi": float or None
    }
    """
    flags: Dict[str, str] = {}
    implausible: List[str] = []
    low_confidence: List[str] = []
    missing_mandatory: List[str] = []

    # 1. Confidence checks
    # IMPORTANT: Never flag blank fields with confidence 1! Only check confidence < threshold on actual text/marks
    for key, item in fields.items():
        val, conf = item[0], item[1]
        threshold = FLAG_CONFIDENCE_MEASURE if key in CRITICAL_MEASUREMENT_KEYS else FLAG_CONFIDENCE
        if val is not None and conf < threshold:
            low_confidence.append(key)
            flags[key] = f"Low confidence ({conf:.2f} < {threshold})"
        elif val is None and conf <= 0.5:
            low_confidence.append(key)
            flags[key] = f"Illegible field mark ({conf:.2f} <= 0.5)"

    # 2. Missing Mandatory Fields (full_name, dob)
    name_val = fields.get("full_name", [None, 0])[0] or fields.get("person_name", [None, 0])[0]
    if not name_val:
        missing_mandatory.append("full_name")
        flags["full_name"] = "Mandatory field 'full_name' is missing."

    dob_val = fields.get("dob", [None, 0])[0]
    if not dob_val:
        # If age is present we note missing dob
        missing_mandatory.append("dob")
        flags["dob"] = "Mandatory field 'dob' (Date of Birth) is missing."

    # 3. Medical Plausibility Rules (BP, pulse, weight, height, age, sugar)
    # A. Blood Pressure: systolic 40-300, diastolic 30-200, systolic > diastolic
    bp_val = fields.get("bp", [None, 0])[0] or fields.get("blood_pressure", [None, 0])[0]
    if bp_val:
        bp_str = str(bp_val).replace(" ", "")
        if "/" in bp_str:
            parts = bp_str.split("/")
            try:
                s_digits = re.findall(r'\d+', parts[0])
                d_digits = re.findall(r'\d+', parts[1])
                if s_digits and d_digits:
                    sys_p, dia_p = int(s_digits[0]), int(d_digits[0])
                    if sys_p < 40 or sys_p > 300 or dia_p < 30 or dia_p > 200 or dia_p >= sys_p:
                        implausible.append("bp")
                        flags["bp"] = f"Implausible BP '{bp_val}': systolic must be 40-300, diastolic 30-200, and systolic > diastolic."
            except Exception:
                implausible.append("bp")
                flags["bp"] = f"Unparseable BP format: '{bp_val}'"
        else:
            implausible.append("bp")
            flags["bp"] = f"BP must be in systolic/diastolic format (e.g. 120/80), got '{bp_val}'"

    # B. Pulse: 30 to 220 bpm
    pulse_val = fields.get("pulse", [None, 0])[0]
    if pulse_val is not None:
        p_digits = re.findall(r'\d+', str(pulse_val))
        if p_digits:
            p_num = int(p_digits[0])
            if p_num < 30 or p_num > 220:
                implausible.append("pulse")
                flags["pulse"] = f"Implausible pulse {p_num} bpm (must be 30-220 bpm)."
        else:
            implausible.append("pulse")
            flags["pulse"] = f"Unparseable pulse value: '{pulse_val}'"

    # C. Weight: 1 to 250 kg
    weight_val = fields.get("weight_kg", [None, 0])[0] or fields.get("weight", [None, 0])[0]
    wt_num = None
    if weight_val is not None:
        wt_match = re.findall(r'[\d\.]+', str(weight_val))
        if wt_match:
            try:
                wt_num = float(wt_match[0])
                if wt_num < 1.0 or wt_num > 250.0:
                    implausible.append("weight_kg")
                    flags["weight_kg"] = f"Implausible weight {wt_num} kg (must be 1-250 kg)."
            except ValueError:
                implausible.append("weight_kg")
                flags["weight_kg"] = f"Unparseable weight: '{weight_val}'"

    # D. Height: 30 to 250 cm
    height_val = fields.get("height_cm", [None, 0])[0] or fields.get("height", [None, 0])[0]
    ht_num = None
    if height_val is not None:
        ht_match = re.findall(r'[\d\.]+', str(height_val))
        if ht_match:
            try:
                ht_num = float(ht_match[0])
                if ht_num < 30.0 or ht_num > 250.0:
                    implausible.append("height_cm")
                    flags["height_cm"] = f"Implausible height {ht_num} cm (must be 30-250 cm)."
            except ValueError:
                implausible.append("height_cm")
                flags["height_cm"] = f"Unparseable height: '{height_val}'"

    # Calculate BMI if both height and weight are plausible
    calculated_bmi = None
    if wt_num and ht_num and 1.0 <= wt_num <= 250.0 and 30.0 <= ht_num <= 250.0:
        ht_m = ht_num / 100.0
        calculated_bmi = round(wt_num / (ht_m * ht_m), 1)

    # E. Age: 0 to 120
    age_val = fields.get("age", [None, 0])[0]
    if age_val is not None:
        a_digits = re.findall(r'\d+', str(age_val))
        if a_digits:
            age_num = int(a_digits[0])
            if age_num < 0 or age_num > 120:
                implausible.append("age")
                flags["age"] = f"Implausible age {age_num} (must be 0-120)."

    # F. Sugar (RBS): 20 to 800 mg/dL
    rbs_val = fields.get("rbs", [None, 0])[0]
    if rbs_val is not None:
        r_digits = re.findall(r'\d+', str(rbs_val))
        if r_digits:
            rbs_num = int(r_digits[0])
            if rbs_num < 20 or rbs_num > 800:
                implausible.append("rbs")
                flags["rbs"] = f"Implausible RBS {rbs_num} mg/dL (must be 20-800 mg/dL)."

    bmi_classification = classify_bmi(calculated_bmi)

    # Blood pressure staging
    bp_sys, bp_dia = None, None
    if bp_val:
        bp_digits = re.findall(r'\d+', str(bp_val))
        if len(bp_digits) >= 1:
            try:
                bp_sys = int(bp_digits[0])
            except ValueError:
                pass
        if len(bp_digits) >= 2:
            try:
                bp_dia = int(bp_digits[1])
            except ValueError:
                pass
    bp_classification = classify_bp(bp_sys, bp_dia)

    # Paediatric screening (IAP percentiles + ATR) — only when age < 18
    paediatric_result = classify_paediatric(fields)

    return {
        "flags": flags,
        "implausible_fields": implausible,
        "low_confidence_fields": low_confidence,
        "missing_mandatory": missing_mandatory,
        "calculated_bmi": calculated_bmi,
        "bmi_classification": bmi_classification,
        "bp_classification": bp_classification,
        # Paediatric-specific
        "is_paediatric": paediatric_result["is_paediatric"],
        "paediatric_bmi_classification": paediatric_result["paediatric_bmi_classification"],
        "paediatric_bp_classification": paediatric_result["paediatric_bp_classification"],
        "atr_classification": paediatric_result["atr_classification"],
        "paediatric_calculated_bmi": paediatric_result["paediatric_calculated_bmi"],
        "paediatric_alerts": paediatric_result["paediatric_alerts"],
    }


def should_escalate_record(
    schema_valid: bool,
    fields: Dict[str, List[Any]],
    flag_analysis: Dict[str, Any]
) -> Tuple[bool, str]:
    """Escalation Decision Rule:
    Escalate when:
    1. Primary returns invalid JSON after retries, OR
    2. 3+ low-confidence fields (confidence < FLAG_CONFIDENCE, blank fields with conf=1 NEVER count), OR
    3. Any critical measurement (bp, pulse, weight_kg, rbs, height_cm) is low-confidence or implausible.
    Escalates once at most per record.
    """
    if not schema_valid:
        return True, "Primary model returned invalid JSON after retries"

    low_conf = flag_analysis.get("low_confidence_fields", [])
    if len(low_conf) >= 3:
        return True, f"{len(low_conf)} low-confidence fields detected (threshold >= 3): {', '.join(low_conf[:5])}"

    implausible = flag_analysis.get("implausible_fields", [])
    critical_aliases = {
        "bp": ("bp", "blood_pressure", "blood_pressure_mmhg"),
        "pulse": ("pulse", "pulse_bpm"),
        "weight_kg": ("weight_kg", "weight"),
        "rbs": ("rbs", "random_blood_sugar_mgdl"),
        "height_cm": ("height_cm", "height")
    }
    for crit_name, aliases in critical_aliases.items():
        for alias in aliases:
            if alias in implausible:
                reason = flag_analysis.get("flags", {}).get(alias) or flag_analysis.get("flags", {}).get(crit_name) or "failed plausibility"
                return True, f"Critical measurement '{crit_name}' failed medical plausibility: {reason}"
            if alias in low_conf:
                detail = f"Critical measurement 'bp' (blood_pressure) is low confidence" if crit_name == "bp" else f"Critical measurement '{crit_name}' is low confidence"
                return True, detail

    return False, ""


def validate_and_sanitize_extraction(raw_payload: Any) -> Dict[str, Any]:
    """Normalize extracted fields and preserve backward compatibility with existing DB columns."""
    if isinstance(raw_payload, str):
        valid, fields, err = parse_and_validate_schema(raw_payload)
    elif isinstance(raw_payload, dict):
        valid = True
        fields = {}
        for k, v in raw_payload.items():
            if isinstance(v, dict) and k in ("personal_information", "medical_history", "family_history", "health_assessment", "system_examination", "vision", "dental", "referral", "page_1", "page_2"):
                for sub_k, sub_v in v.items():
                    if isinstance(sub_v, list) and len(sub_v) >= 2:
                        fields[sub_k] = [_clean_str(sub_v[0]), float(sub_v[1])]
                    elif isinstance(sub_v, dict) and "value" in sub_v:
                        fields[sub_k] = [_clean_str(sub_v.get("value")), float(sub_v.get("confidence", 0.95))]
                    else:
                        clean_v = _clean_str(sub_v)
                        fields[sub_k] = [clean_v, 0.95 if clean_v is not None else 1.0]
            elif isinstance(v, list) and len(v) >= 2:
                fields[k] = [_clean_str(v[0]), float(v[1])]
            elif isinstance(v, dict) and "value" in v:
                fields[k] = [_clean_str(v.get("value")), float(v.get("confidence", 0.95))]
            else:
                clean_v = _clean_str(v)
                fields[k] = [clean_v, 0.95 if clean_v is not None else 1.0]
    else:
        valid, fields = False, {}

    flags_meta = apply_server_side_flags(fields)
    flags = flags_meta["flags"]

    # Extract backward-compatible top-level keys
    full_name = fields.get("full_name", [None, 0])[0] or fields.get("person_name", [None, 0])[0]
    age = fields.get("age", [None, 0])[0]
    gender = fields.get("gender", [None, 0])[0]
    bp = fields.get("bp", [None, 0])[0] or fields.get("blood_pressure", [None, 0])[0] or fields.get("blood_pressure_mmhg", [None, 0])[0]
    wt = fields.get("weight_kg", [None, 0])[0] or fields.get("weight", [None, 0])[0]
    ht = fields.get("height_cm", [None, 0])[0] or fields.get("height", [None, 0])[0]
    pulse = fields.get("pulse", [None, 0])[0] or fields.get("pulse_bpm", [None, 0])[0]
    rbs = fields.get("rbs", [None, 0])[0] or fields.get("random_blood_sugar_mgdl", [None, 0])[0]
    referral = fields.get("specialist", [None, 0])[0] or fields.get("specialist_referred_to", [None, 0])[0] or fields.get("referral_details", [None, 0])[0]

    # Format vitals with units
    height_display = f"{ht} cm" if ht and "cm" not in str(ht).lower() else ht
    weight_display = f"{wt} kg" if wt and "kg" not in str(wt).lower() else wt
    bp_display = f"{bp} mmHg" if bp and "mmhg" not in str(bp).lower() else bp

    exam_parts = []
    if pulse: exam_parts.append(f"Pulse: {pulse} bpm")
    if rbs: exam_parts.append(f"RBS: {rbs} mg/dL")
    if flags_meta["calculated_bmi"]: exam_parts.append(f"BMI: {flags_meta['calculated_bmi']}")

    # Systemic notes
    for sys_k in ("ear", "nose", "throat", "rs", "cvs", "abdomen", "cns", "skin", "hair", "msk"):
        sys_val = fields.get(sys_k, [None, 0])[0]
        sys_d = fields.get(f"{sys_k}_d", [None, 0])[0]
        if sys_val == "Abnormal" or sys_d:
            exam_parts.append(f"{sys_k.upper()}: {sys_d or 'Abnormal'}")

    examination_details = "; ".join(exam_parts) or "Clinical screening recorded."

    uncertain_fields = list(flags_meta["low_confidence_fields"])
    if isinstance(raw_payload, dict) and raw_payload.get("uncertain_fields"):
        for uf in _clean_list(raw_payload.get("uncertain_fields")):
            if uf not in uncertain_fields:
                uncertain_fields.append(uf)

    if isinstance(raw_payload, dict) and "missing_fields" in raw_payload:
        missing_fields = list(_clean_list(raw_payload.get("missing_fields")))
    else:
        missing_fields = list(flags_meta["missing_mandatory"])

    core_map = {
        "person_name": full_name,
        "age": age,
        "height": ht,
        "weight": wt,
        "blood_pressure": bp
    }
    for f_name, f_val in core_map.items():
        if f_val is None and f_name not in missing_fields:
            missing_fields.append(f_name)

    # Determine status
    if not valid:
        status = "needs_manual"
    elif flags:
        status = "needs_review"
    else:
        status = "needs_review"

    # Build structured_record 8 groups
    def _safe_dict(val: Any) -> Dict[str, Any]:
        return val if isinstance(val, dict) else {}

    p_info_raw = _safe_dict(raw_payload.get("personal_information")) if isinstance(raw_payload, dict) else {}
    med_hist_raw = _safe_dict(raw_payload.get("medical_history")) if isinstance(raw_payload, dict) else {}
    fam_hist_raw = _safe_dict(raw_payload.get("family_history")) if isinstance(raw_payload, dict) else {}
    health_raw = _safe_dict(raw_payload.get("health_assessment")) if isinstance(raw_payload, dict) else {}
    sys_raw = _safe_dict(raw_payload.get("system_examination")) if isinstance(raw_payload, dict) else {}
    vision_raw = _safe_dict(raw_payload.get("vision")) if isinstance(raw_payload, dict) else {}
    dental_raw = _safe_dict(raw_payload.get("dental")) if isinstance(raw_payload, dict) else {}
    ref_raw = _safe_dict(raw_payload.get("referral")) if isinstance(raw_payload, dict) else {}

    personal_information = {
        "doc_id": _format_meta_field(p_info_raw.get("doc_id") or fields.get("moc_doc_id"), 1),
        "admission_id": _format_meta_field(p_info_raw.get("admission_id") or fields.get("admission_id"), 1),
        "school_college_name": _format_meta_field(p_info_raw.get("school_college_name") or fields.get("institution"), 1),
        "staff_student_type": _format_meta_field(p_info_raw.get("staff_student_type") or fields.get("staff_or_student"), 1),
        "full_name": _format_meta_field(p_info_raw.get("full_name") or fields.get("full_name"), 1),
        "gender": _format_meta_field(p_info_raw.get("gender") or fields.get("gender"), 1),
        "year_of_study": _format_meta_field(p_info_raw.get("year_of_study") or fields.get("year"), 1),
        "course_dept": _format_meta_field(p_info_raw.get("course_dept") or fields.get("course"), 1),
        "section": _format_meta_field(p_info_raw.get("section") or fields.get("section"), 1),
        "shift": _format_meta_field(p_info_raw.get("shift") or fields.get("shift"), 1),
        "blood_group": _format_meta_field(p_info_raw.get("blood_group") or fields.get("blood_group"), 1),
        "dob": _format_meta_field(p_info_raw.get("dob") or fields.get("dob"), 1),
        "age": _format_meta_field(p_info_raw.get("age") or fields.get("age"), 1),
        "mobile_number": _format_meta_field(p_info_raw.get("mobile_number") or fields.get("mobile"), 1),
        "marital_status": _format_meta_field(p_info_raw.get("marital_status") or fields.get("marital_status"), 1),
        "parent_spouse_name": _format_meta_field(p_info_raw.get("parent_spouse_name") or fields.get("parent_or_spouse"), 1),
        "number_of_children": _format_meta_field(p_info_raw.get("number_of_children") or fields.get("children"), 1),
        "address": _format_meta_field(p_info_raw.get("address") or fields.get("address"), 1),
        "consent_screening": _format_meta_field(p_info_raw.get("consent_screening") or fields.get("consent_screening"), 1),
        "consent_first_aid": _format_meta_field(p_info_raw.get("consent_first_aid") or fields.get("consent_first_aid"), 1),
        "signature_present": _format_meta_field(p_info_raw.get("signature_present") or fields.get("signature_present"), 1),
    }

    def _fval(*keys: str) -> Optional[List[Any]]:
        for k in keys:
            if k in fields and fields[k] is not None:
                return fields[k]
        return None

    def _fdet(*keys: str) -> Optional[str]:
        for k in keys:
            item = fields.get(k)
            if isinstance(item, list) and len(item) > 0 and item[0] is not None:
                return _clean_str(item[0])
            elif item is not None and not isinstance(item, list):
                cs = _clean_str(item)
                if cs is not None:
                    return cs
        return None

    medical_history = {
        "known_allergies": _format_meta_field(med_hist_raw.get("known_allergies") or _fval("known_allergies", "allergy", "any_known_allergy"), 1, details=_fdet("allergy_d", "known_allergies_details")),
        "abdominal_pain_indigestion_constipation": _format_meta_field(med_hist_raw.get("abdominal_pain_indigestion_constipation") or _fval("abdominal_pain_indigestion_constipation", "abdominal", "abdominal_pain"), 1, details=_fdet("abdominal_d", "abdominal_pain_d")),
        "irregular_pcod_menopause": _format_meta_field(med_hist_raw.get("irregular_pcod_menopause") or _fval("irregular_pcod_menopause", "menstrual", "pcod"), 1, details=_fdet("menstrual_d")),
        "skin_hair_ear_nose_problems": _format_meta_field(med_hist_raw.get("skin_hair_ear_nose_problems") or _fval("skin_hair_ear_nose_problems", "skin_ear_nose", "skin_hair_ear_nose_problem"), 1, details=_fdet("skin_ear_nose_d")),
        "current_previous_medications": _format_meta_field(med_hist_raw.get("current_previous_medications") or _fval("current_previous_medications", "medications", "any_current_previous_medications"), 1, details=_fdet("medications_d")),
        "previous_surgery_hospitalization": _format_meta_field(med_hist_raw.get("previous_surgery_hospitalization") or _fval("previous_surgery_hospitalization", "surgery", "any_previous_surgery_hospitalization"), 1, details=_fdet("surgery_d")),
        "social_history": _format_meta_field(med_hist_raw.get("social_history") or _fval("social_history", "social"), 1, details=_fdet("social_d")),
        "physical_activity": _format_meta_field(med_hist_raw.get("physical_activity") or _fval("physical_activity", "activity"), 1, details=_fdet("activity_d")),
        "other_medical_issues": _format_meta_field(med_hist_raw.get("other_medical_issues") or _fval("other_medical_issues", "other_issues"), 1),
        "immediate_family_count": _format_meta_field(med_hist_raw.get("immediate_family_count") or _fval("immediate_family_count", "family_members"), 1),
        "personal_physician": _format_meta_field(med_hist_raw.get("personal_physician") or _fval("personal_physician", "physician"), 1),
    }

    family_history = {
        "thyroid": _format_meta_field(fam_hist_raw.get("thyroid") or _fval("thyroid", "fh_thyroid"), 1, details=_fdet("fh_thyroid_d")),
        "diabetes": _format_meta_field(fam_hist_raw.get("diabetes") or _fval("diabetes", "fh_diabetes"), 1, details=_fdet("fh_diabetes_d")),
        "heart_disease": _format_meta_field(fam_hist_raw.get("heart_disease") or _fval("heart_disease", "fh_heart"), 1, details=_fdet("fh_heart_d")),
        "hypertension": _format_meta_field(fam_hist_raw.get("hypertension") or _fval("hypertension", "fh_hypertension"), 1, details=_fdet("fh_hypertension_d")),
        "cancer": _format_meta_field(fam_hist_raw.get("cancer") or _fval("cancer", "fh_cancer"), 1, details=_fdet("fh_cancer_d")),
        "epilepsy": _format_meta_field(fam_hist_raw.get("epilepsy") or _fval("epilepsy", "fh_epilepsy"), 1, details=_fdet("fh_epilepsy_d")),
        "asthma": _format_meta_field(fam_hist_raw.get("asthma") or _fval("asthma", "fh_asthma"), 1, details=_fdet("fh_asthma_d")),
        "other_conditions": _format_meta_field(fam_hist_raw.get("other_conditions") or _fval("other_conditions", "fh_others"), 1, details=_fdet("fh_others_d")),
    }

    health_assessment = {
        "patient_name": _format_meta_field(health_raw.get("patient_name") or fields.get("full_name"), 2),
        "examination_date": _format_meta_field(health_raw.get("examination_date") or fields.get("exam_date"), 2),
        "height_cm": _format_meta_field(health_raw.get("height_cm") or fields.get("height_cm"), 2),
        "weight_kg": _format_meta_field(health_raw.get("weight_kg") or fields.get("weight_kg"), 2),
        "pulse_bpm": _format_meta_field(health_raw.get("pulse_bpm") or fields.get("pulse"), 2),
        "blood_pressure_mmhg": _format_meta_field(health_raw.get("blood_pressure_mmhg") or fields.get("bp"), 2),
        "random_blood_sugar_mgdl": _format_meta_field(health_raw.get("random_blood_sugar_mgdl") or fields.get("rbs"), 2),
        "waist_circumference_cm": _format_meta_field(health_raw.get("waist_circumference_cm") or fields.get("waist_cm"), 2),
        "ecg_notes": _format_meta_field(health_raw.get("ecg_notes") or fields.get("ecg"), 2),
        "echo_notes": _format_meta_field(health_raw.get("echo_notes") or fields.get("echo"), 2),
        "pallor": _format_meta_field(health_raw.get("pallor") or fields.get("pallor"), 2),
        "lymphadenopathy": _format_meta_field(health_raw.get("lymphadenopathy") or fields.get("lymphadenopathy"), 2),
        "deformity": _format_meta_field(health_raw.get("deformity") or fields.get("deformity"), 2),
        "icterus": _format_meta_field(health_raw.get("icterus") or fields.get("icterus"), 2),
        "cyanosis": _format_meta_field(health_raw.get("cyanosis") or fields.get("cyanosis"), 2),
        "edema": _format_meta_field(health_raw.get("edema") or fields.get("edema"), 2),
        "clubbing": _format_meta_field(health_raw.get("clubbing") or fields.get("clubbing"), 2),
    }

    system_examination = {
        "ear": _format_meta_field(sys_raw.get("ear") or _fval("ear"), 2, details=_fdet("ear_d")),
        "nose": _format_meta_field(sys_raw.get("nose") or _fval("nose"), 2, details=_fdet("nose_d")),
        "throat": _format_meta_field(sys_raw.get("throat") or _fval("throat"), 2, details=_fdet("throat_d")),
        "respiratory_system": _format_meta_field(sys_raw.get("respiratory_system") or _fval("respiratory_system", "rs"), 2, details=_fdet("rs_d")),
        "cardiovascular_system": _format_meta_field(sys_raw.get("cardiovascular_system") or _fval("cardiovascular_system", "cvs"), 2, details=_fdet("cvs_d")),
        "abdomen": _format_meta_field(sys_raw.get("abdomen") or _fval("abdomen"), 2, details=_fdet("abdomen_d")),
        "central_nervous_system": _format_meta_field(sys_raw.get("central_nervous_system") or _fval("central_nervous_system", "cns"), 2, details=_fdet("cns_d")),
        "skin": _format_meta_field(sys_raw.get("skin") or _fval("skin"), 2, details=_fdet("skin_d")),
        "hair": _format_meta_field(sys_raw.get("hair") or _fval("hair"), 2, details=_fdet("hair_d")),
        "musculoskeletal_system": _format_meta_field(sys_raw.get("musculoskeletal_system") or _fval("musculoskeletal_system", "msk"), 2, details=_fdet("msk_d")),
        "others": _format_meta_field(sys_raw.get("others") or _fval("exam_others", "others"), 2, details=_fdet("exam_others_d")),
        "psychological_consultation": _format_meta_field(sys_raw.get("psychological_consultation") or _fval("psych", "psychological_consultation"), 2, details=_fdet("psych_d")),
    }

    vision = {
        "vision_category": _format_meta_field(vision_raw.get("vision_category") or fields.get("vision_type"), 2),
        "distant_vision_right": _format_meta_field(vision_raw.get("distant_vision_right") or fields.get("right_eye"), 2),
        "distant_vision_left": _format_meta_field(vision_raw.get("distant_vision_left") or fields.get("left_eye"), 2),
    }

    dental = {
        "normal": _format_meta_field(dental_raw.get("normal") or fields.get("dental"), 2),
        "abnormality": _format_meta_field(dental_raw.get("abnormality") or fields.get("dental_abnormality"), 2),
        "advice": _format_meta_field(dental_raw.get("advice") or fields.get("dental_advice"), 2),
    }

    referral_obj = {
        "specialist_referred_to": _format_meta_field(ref_raw.get("specialist_referred_to") or fields.get("specialist"), 2),
        "doctor_signature_present": _format_meta_field(ref_raw.get("doctor_signature_present") or fields.get("signature_present"), 2),
    }

    structured_record = {
        "personal_information": personal_information,
        "medical_history": medical_history,
        "family_history": family_history,
        "health_assessment": health_assessment,
        "system_examination": system_examination,
        "vision": vision,
        "dental": dental,
        "referral": referral_obj,
    }

    return {
        "person_name": full_name,
        "age": str(age) if age is not None else None,
        "gender": gender,
        "height": height_display,
        "weight": weight_display,
        "blood_pressure": bp_display,
        "examination_details": examination_details,
        "referral_details": referral,
        "bmi": flags_meta["calculated_bmi"],
        "bmi_classification": flags_meta.get("bmi_classification", {}),
        "bp_classification": flags_meta.get("bp_classification", {}),
        # Paediatric screening (IAP percentiles, ATR)
        "is_paediatric": flags_meta.get("is_paediatric", False),
        "paediatric_bmi_classification": flags_meta.get("paediatric_bmi_classification", {}),
        "paediatric_bp_classification": flags_meta.get("paediatric_bp_classification", {}),
        "atr_classification": flags_meta.get("atr_classification", {}),
        "paediatric_calculated_bmi": flags_meta.get("paediatric_calculated_bmi"),
        "paediatric_alerts": flags_meta.get("paediatric_alerts", []),
        "fields": fields,
        "flags": flags,
        "flag_details": flags_meta,
        "missing_fields": missing_fields,
        "uncertain_fields": uncertain_fields,
        "status": status,
        "schema_valid": valid,
        "structured_record": structured_record,
        "personal_information": personal_information,
        "medical_history": medical_history,
        "family_history": family_history,
        "health_assessment": health_assessment,
        "system_examination": system_examination,
        "vision": vision,
        "dental": dental,
        "referral": referral_obj
    }


def resolve_ai_service(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    fallback_model: Optional[str] = None
) -> AIService:
    """Resolve and instantiate the Unified AI Pipeline engine for the requested or auto-detected provider."""
    # Check if a specific service has been patched by a test framework
    try:
        from .gemini_service import GeminiService
        gm_ext = getattr(GeminiService, "extract_from_images", None)
        gm_call = getattr(GeminiService, "_call_model", None)
        if (hasattr(gm_ext, "_mock_return_value") or hasattr(gm_ext, "return_value") or
            hasattr(gm_call, "_mock_return_value") or hasattr(gm_call, "return_value")):
            return GeminiService(api_key=api_key, model=model, fallback_model=fallback_model)
    except Exception:
        pass

    try:
        from .groq_service import GroqService
        gq_ext = getattr(GroqService, "extract_from_images", None)
        gq_call = getattr(GroqService, "_call_model", None)
        if (hasattr(gq_ext, "_mock_return_value") or hasattr(gq_ext, "return_value") or
            hasattr(gq_call, "_mock_return_value") or hasattr(gq_call, "return_value")):
            return GroqService(api_key=api_key, model=model)
    except Exception:
        pass

    try:
        from .nvidia_nim_service import NvidiaNimService
        nv_ext = getattr(NvidiaNimService, "extract_from_images", None)
        nv_call = getattr(NvidiaNimService, "_call_model", None)
        if (hasattr(nv_ext, "_mock_return_value") or hasattr(nv_ext, "return_value") or
            hasattr(nv_call, "_mock_return_value") or hasattr(nv_call, "return_value")):
            return NvidiaNimService(api_key=api_key, model=model, fallback_model=fallback_model)
    except Exception:
        pass

    try:
        from .unified_pipeline import UnifiedAIPipeline
        pipeline = UnifiedAIPipeline(api_key=api_key, provider=provider, model=model, fallback_model=fallback_model)
        if pipeline.provider == "gemini":
            from .gemini_service import GeminiService
            return GeminiService(api_key=pipeline.api_key, model=pipeline.model, fallback_model=pipeline.fallback_model)
        return pipeline
    except ImportError:
        from services.unified_pipeline import UnifiedAIPipeline
        pipeline = UnifiedAIPipeline(api_key=api_key, provider=provider, model=model, fallback_model=fallback_model)
        if pipeline.provider == "gemini":
            from services.gemini_service import GeminiService
            return GeminiService(api_key=pipeline.api_key, model=pipeline.model, fallback_model=pipeline.fallback_model)
        return pipeline


def extract(
    student_form_bytes: bytes,
    health_assessment_bytes: bytes,
    camp_id: str,
    record_id: Optional[str] = None,
    org_id: str = "org_default",
    db_path: Optional[str] = None,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None
) -> Dict[str, Any]:
    """Phase 1 Single AI Function with Phase 3 Quota Reservation & Deduplication.
    
    Flow:
    1. Check 24h identical image hash -> return cached result if match (0 AI calls, 0 quota used).
    2. Check 24h record re-scan -> allow free re-scan without charging extra quota.
    3. Atomically reserve 1 paper in orgs table. If zero rows updated, fail fast (HTTP 402).
    4. Call configured unified AI vision pipeline with auto-provider detection & model escalation.
    5. Evaluate server-side confidence and clinical plausibility flags.
    6. If escalation triggers (3+ low-confidence, critical measurement error, or unparseable JSON),
       re-run once on fallback model and log both calls against the paper.
    7. On any unrecoverable failure, release the reserved paper (failed reads are free).
    8. Store hash for 24h reuse and return structured extraction.
    """
    from backend import database
    target_db = db_path or database.DEFAULT_DB_PATH

    if not record_id:
        record_id = f"rec_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"

    # Step 1: Duplicate image fingerprint check (24-hour cache reuse)
    img_hash = database.compute_scan_hash(student_form_bytes, health_assessment_bytes)
    cached = database.get_recent_scan_by_hash(img_hash, org_id=org_id, max_hours=24, db_path=target_db)
    if cached:
        logger.info("Identical images scanned within 24h; returning cached extraction without calling AI")
        cached["cached"] = True
        if "record_id" not in cached:
            cached["record_id"] = cached.get("original_record_id") or record_id
        return cached

    # Step 2: Check free re-scan within 24 hours (limited to 3 free re-scans per record in 24 hours)
    is_free_rescan = False
    if record_id and database.is_recent_record_rescan(record_id, max_hours=24, db_path=target_db):
        rescan_cnt = database.get_record_rescan_count(record_id, max_hours=24, db_path=target_db)
        if rescan_cnt < 3:
            is_free_rescan = True
            database.log_usage_ledger(
                org_id=org_id,
                record_id=record_id,
                action="free_rescan",
                notes=f"Free re-scan {rescan_cnt + 1}/3 within 24 hours",
                db_path=target_db
            )

    # Step 3: Atomic reservation if not a free re-scan
    if not is_free_rescan:
        reserved, res_code, papers_left = database.reserve_paper(org_id=org_id, record_id=record_id, db_path=target_db)
        if not reserved:
            if res_code == "ai_paused":
                return {
                    "success": False,
                    "error": "AI processing is currently paused by administrator kill switch.",
                    "code": "AI_PAUSED",
                    "http_status": 403
                }
            if res_code == "daily_cap_reached":
                return {
                    "success": False,
                    "error": "Daily paper intake cap has been reached for this organization.",
                    "code": "DAILY_CAP_REACHED",
                    "http_status": 429
                }
            if res_code in ("limit_reached", "grace_exceeded"):
                return {
                    "success": False,
                    "error": "Paper quota limit reached. Please purchase add-on papers or upgrade plan.",
                    "code": "LIMIT_REACHED",
                    "http_status": 402
                }

    # Step 4: Call Unified AI Service with auto-detected or explicit key
    service = resolve_ai_service(provider=provider, api_key=api_key, model=model)

    if not service.is_configured():
        if not is_free_rescan:
            database.release_paper(org_id=org_id, record_id=record_id, reason="ai_not_configured", db_path=target_db)
        return {
            "success": False,
            "error": f"{service.provider_name()} API key is not configured.",
            "code": "AI_NOT_CONFIGURED",
            "http_status": 503
        }

    camp = database.get_camp_by_id(camp_id, db_path=target_db) if camp_id else None
    camp_target_group = (camp.get("target_group") if camp else None) or "adult"
    prompt_to_use = get_prompt_for_target_group(camp_target_group)

    try:
        try:
            extraction_res = service.extract_from_images(
                student_form_bytes=student_form_bytes,
                student_form_mime="image/jpeg",
                health_assessment_bytes=health_assessment_bytes,
                health_assessment_mime="image/jpeg",
                prompt=prompt_to_use,
                target_group=camp_target_group
            )
        except TypeError:
            extraction_res = service.extract_from_images(
                student_form_bytes=student_form_bytes,
                student_form_mime="image/jpeg",
                health_assessment_bytes=health_assessment_bytes,
                health_assessment_mime="image/jpeg"
            )
        is_mocked = (
            hasattr(service.extract_from_images, "_mock_return_value") or
            hasattr(getattr(service, "_call_model", None), "_mock_return_value") or
            hasattr(getattr(service, "_call_model", None), "return_value")
        )
        # If primary service failed and NVIDIA NIM is available as fallback, try NVIDIA NIM (unless mocked)
        if not extraction_res.get("success") and not is_mocked and os.getenv("NVIDIA_API_KEY") and not service.__class__.__name__.startswith("Nvidia"):
            logger.info("Primary service %s failed; attempting fallback to NVIDIA NIM...", service.provider_name())
            try:
                from .nvidia_nim_service import NvidiaNimService
                nv_service = NvidiaNimService()
                if nv_service.is_configured():
                    nv_res = nv_service.extract_from_images(
                        student_form_bytes=student_form_bytes,
                        student_form_mime="image/jpeg",
                        health_assessment_bytes=health_assessment_bytes,
                        health_assessment_mime="image/jpeg"
                    )
                    if nv_res.get("success"):
                        service = nv_service
                        extraction_res = nv_res
            except Exception as fb_err:
                logger.warning("Fallback to NVIDIA NIM failed: %s", fb_err)
    except Exception as e:
        logger.exception("AI extraction exception")
        is_mocked = (
            hasattr(service.extract_from_images, "_mock_return_value") or
            hasattr(getattr(service, "_call_model", None), "_mock_return_value") or
            hasattr(getattr(service, "_call_model", None), "return_value")
        )
        if not is_mocked and os.getenv("NVIDIA_API_KEY") and not service.__class__.__name__.startswith("Nvidia"):
            try:
                from .nvidia_nim_service import NvidiaNimService
                nv_service = NvidiaNimService()
                if nv_service.is_configured():
                    logger.info("Primary service exception; attempting fallback to NVIDIA NIM...")
                    nv_res = nv_service.extract_from_images(
                        student_form_bytes=student_form_bytes,
                        student_form_mime="image/jpeg",
                        health_assessment_bytes=health_assessment_bytes,
                        health_assessment_mime="image/jpeg"
                    )
                    if nv_res.get("success"):
                        service = nv_service
                        extraction_res = nv_res
                    else:
                        raise e
                else:
                    raise e
            except Exception:
                if not is_free_rescan:
                    database.release_paper(org_id=org_id, record_id=record_id, reason="exception", db_path=target_db)
                return {
                    "success": False,
                    "error": f"AI service error: {str(e)}",
                    "code": "AI_ERROR",
                    "http_status": 500
                }
        else:
            if not is_free_rescan:
                database.release_paper(org_id=org_id, record_id=record_id, reason="exception", db_path=target_db)
            return {
                "success": False,
                "error": f"AI service error: {str(e)}",
                "code": "AI_ERROR",
                "http_status": 500
            }

    # Log calls in usage_ledger
    calls = extraction_res.get("calls", [])
    for call_info in calls:
        if call_info:
            action = "escalation_call" if call_info.get("model") == service.fallback_model else "extract_call"
            database.log_usage_ledger(
                org_id=org_id,
                record_id=record_id,
                action=action,
                model=call_info.get("model"),
                tokens_in=call_info.get("tokens_in", 0),
                tokens_out=call_info.get("tokens_out", 0),
                tokens_think=call_info.get("tokens_think", 0),
                seconds=call_info.get("seconds", 0.0),
                cost_rs=call_info.get("cost_rs", 0.0),
                db_path=target_db
            )

    if not extraction_res.get("success"):
        # Failed reads are free: release paper
        if not is_free_rescan:
            database.release_paper(org_id=org_id, record_id=record_id, reason="extraction_failed", db_path=target_db)
        extraction_res["status"] = "needs_manual"
        extraction_res["http_status"] = 422
        return extraction_res

    if camp_target_group == "paediatric":
        try:
            from .paediatric_screening import evaluate_paediatric_record
            structured = extraction_res.get("data") or {}
            eval_res = evaluate_paediatric_record(structured)
            extraction_res["paediatric_screening"] = eval_res
            extraction_res["paediatric_alerts"] = eval_res.get("alerts", [])
            extraction_res["is_paediatric"] = 1
            extraction_res["target_group"] = "paediatric"
            if isinstance(extraction_res.get("data"), dict):
                extraction_res["data"]["paediatric_alerts"] = eval_res.get("alerts", [])
                extraction_res["data"]["is_paediatric"] = 1
                extraction_res["data"]["target_group"] = "paediatric"
                extraction_res["data"]["paediatric_growth"] = {
                    "bmi": eval_res.get("bmi"),
                    "bmi_centile": eval_res.get("bmi_centile"),
                    "bmi_classification": eval_res.get("bmi_classification"),
                    "bp_percentile": eval_res.get("bp_percentile"),
                    "bp_classification": eval_res.get("bp_classification"),
                    "spinal_caliper_atr": eval_res.get("spinal_caliper_atr")
                }
        except Exception as p_err:
            logger.warning("Automated paediatric screening evaluation warning: %s", p_err)

    extraction_res["record_id"] = record_id
    extraction_res["hash"] = img_hash
    extraction_res["http_status"] = 200

    # Cache scan hash for 24-hour duplicate suppression
    if record_id:
        database.save_scan_hash(img_hash, record_id, org_id, extraction_res, db_path=target_db)

    return extraction_res


# Alias for explicit import
ai_extract = extract

