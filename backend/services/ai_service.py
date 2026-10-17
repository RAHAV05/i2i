"""AI Provider Service Interface and Validation Schema for Health Assessment Forms.

Designed specifically for the two-page Student/Staff Health Screening Form:
- Page 1: Personal Information, Medical History Table, Family History, Consent & Signature
- Page 2: Health Assessment Vitals, General Signs, System-wise Examination, Vision, Dental, Referral

Provides standard field metadata:
{
  "value": Any,
  "confidence": float (0.0 to 1.0),
  "status": "extracted" | "uncertain" | "missing" | "not_applicable",
  "source_page": 1 | 2,
  "notes": str or None
}
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import json
import re

SYSTEM_EXTRACTION_PROMPT = """You are an accurate, strict medical document transcription assistant specializing in Student/Staff Health Screening Forms.
Extract all handwritten and printed clinical data from the two scanned pages into valid JSON.

Page 1 (Student/Staff Personal Information & Medical History):
- personal_information: doc_id, admission_id, school_college_name, staff_student_type, full_name, gender, year_of_study, course_dept, section, shift, blood_group, dob, age, mobile_number, marital_status, parent_spouse_name, number_of_children, address, consent_screening ("checked"|"unchecked"|"uncertain"), consent_first_aid ("checked"|"unchecked"|"uncertain"), signature_present ("present"|"not_present"|"uncertain")
- medical_history: known_allergies, abdominal_pain_indigestion_constipation, irregular_pcod_menopause, skin_hair_ear_nose_problems, current_previous_medications, previous_surgery_hospitalization, social_history, physical_activity (for each of these 8 items, return {"value": "yes"|"no"|"unchecked"|"uncertain", "details": string or null for handwritten notes}), other_medical_issues, immediate_family_count, personal_physician
- family_history: thyroid, diabetes, heart_disease, hypertension, cancer, epilepsy, asthma, other_conditions (for each condition, return {"value": "checked"|"unchecked"|"uncertain", "relationship": string or null})

Page 2 (Health Assessment):
- health_assessment: patient_name, examination_date, height, weight, pulse, blood_pressure, rbs, waist_circumference, ecg_notes, echo_notes, pallor, lymphadenopathy, deformity, icterus, cyanosis, edema, clubbing (general signs are "yes"|"no"|"unchecked"|"uncertain")
- system_examination: ear, nose, throat, respiratory_system, cardiovascular_system, abdomen, central_nervous_system, skin, hair, musculoskeletal_system, others, psychological_consultation (for each system, return {"value": "normal"|"abnormal"|"not_examined"|"uncertain", "details": string or null})
- vision: visual_category ("unaided"|"aided_with_glass"|"aided_lens"|"not_brought_glass"|"couldnt_be_assessed"), distant_vision_right, distant_vision_left
- dental: normality ("normal"|"abnormal"|"not_examined"), abnormality, advice
- referral: specialist_referred_to, doctor_signature_present ("present"|"not_present"|"uncertain")

CRITICAL RULES:
1. Extract ONLY information explicitly printed or handwritten on the pages.
2. Interpret handwriting conservatively. Preserve original units (cm, kg, mt, mmHg, mg/dL) and punctuation.
3. NEVER invent, hallucinate, guess, or infer missing values, diagnoses, or names.
4. For Yes/No checkboxes or columns:
   - If checked/written Yes -> "yes"
   - If checked/written No -> "no"
   - If unmarked or empty -> "unchecked" (DO NOT assume an unchecked box means negative medical history)
5. If a field is blank or unreadable, set value to null.
6. Return ONLY a single, valid JSON object matching these sections. Do not include markdown code block formatting if JSON mode is active."""

class AIService(ABC):
    """Abstract interface for document vision AI providers."""

    @abstractmethod
    def extract_from_images(
        self,
        page1_bytes: bytes,
        page1_mime: str,
        page2_bytes: bytes,
        page2_mime: str
    ) -> Dict[str, Any]:
        """Process Page 1 and Page 2 and return structured extraction JSON."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the provider has the necessary credentials configured."""
        pass

    @abstractmethod
    def provider_name(self) -> str:
        """Return friendly name of the provider."""
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
    """Ensure a clean list of unique string tokens."""
    if not val or not isinstance(val, list):
        return []
    out = []
    for item in val:
        if item is not None:
            clean_item = str(item).strip()
            if clean_item and clean_item not in out:
                out.append(clean_item)
    return out


def _format_meta_field(raw: Any, default_page: int = 1) -> Dict[str, Any]:
    """Wrap raw data into the standard field metadata structure:
    {value, confidence, status, source_page, notes, details}
    """
    if isinstance(raw, dict) and "value" in raw:
        val = raw.get("value")
        # Check if value is null-like
        clean_v = _clean_str(val) if isinstance(val, (str, int, float)) else val
        conf = float(raw.get("confidence", 0.95)) if clean_v is not None else 0.0
        stat = raw.get("status") or ("extracted" if clean_v is not None else "missing")
        return {
            "value": clean_v,
            "confidence": min(1.0, max(0.0, conf)),
            "status": stat,
            "source_page": int(raw.get("source_page", default_page)),
            "notes": _clean_str(raw.get("notes")),
            "details": _clean_str(raw.get("details")) or _clean_str(raw.get("relationship"))
        }

    # If raw is a direct primitive or string or dict without 'value'
    if isinstance(raw, dict):
        # Extract status / specify / details if provided
        status_val = raw.get("status") or raw.get("selection")
        details_val = raw.get("details") or raw.get("specify") or raw.get("relationship")
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
    stat = "extracted" if clean_v is not None else "missing"
    return {
        "value": clean_v,
        "confidence": 0.95 if clean_v is not None else 0.0,
        "status": stat,
        "source_page": default_page,
        "notes": None,
        "details": None
    }


def validate_and_sanitize_extraction(raw_payload: Any) -> Dict[str, Any]:
    """Validate and sanitize raw AI response against the complete health screening schema.
    Produces both:
    1. Grouped `structured_record` containing all 8 form sections with field metadata.
    2. Backward-compatible top-level keys (`person_name`, `age`, `height`, etc.) for existing DB and API contracts.
    """
    if isinstance(raw_payload, str):
        # Strip markdown json codeblock if present
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw_payload)
        cleaned = json_match.group(1) if json_match else raw_payload.strip()
        data = json.loads(cleaned)
    elif isinstance(raw_payload, dict):
        data = raw_payload
    else:
        raise ValueError("Invalid payload type for extraction response")

    # Handle flat or legacy response by mapping to groups if necessary
    p_info_raw = data.get("personal_information") or {}
    med_hist_raw = data.get("medical_history") or {}
    fam_hist_raw = data.get("family_history") or {}
    health_raw = data.get("health_assessment") or {}
    system_raw = data.get("system_examination") or data.get("systemic_examination") or {}
    vision_raw = data.get("vision") or {}
    dental_raw = data.get("dental") or {}
    referral_raw = data.get("referral") or {}

    # If the model returned page_1 and page_2 top keys (like Qwen often does)
    if "page_1" in data and isinstance(data["page_1"], dict):
        p1 = data["page_1"]
        if "personal_information" in p1: p_info_raw = p1["personal_information"]
        if "medical_history" in p1: med_hist_raw = p1["medical_history"]
        if "family_history" in p1: fam_hist_raw = p1["family_history"]
    if "page_2" in data and isinstance(data["page_2"], dict):
        p2 = data["page_2"]
        if "health_assessment" in p2: health_raw = p2["health_assessment"]
        elif "health_assessment_vitals" in p2: health_raw = {**p2["health_assessment_vitals"], "date_of_exam": p2.get("date_of_exam")}
        if "general_signs" in p2: health_raw = {**health_raw, **p2["general_signs"]}
        if "system_examination" in p2: system_raw = p2["system_examination"]
        elif "systemic_examination" in p2: system_raw = p2["systemic_examination"]
        if "vision" in p2: vision_raw = p2["vision"]
        if "dental" in p2: dental_raw = p2["dental"]
        if "referral" in p2: referral_raw = p2["referral"]
        elif "specialist_referred_to" in p2: referral_raw = {"specialist_referred_to": p2.get("specialist_referred_to")}

    # Build Group 1: personal_information
    personal_information = {
        "doc_id": _format_meta_field(p_info_raw.get("doc_id") or p_info_raw.get("moc_doc_id"), 1),
        "admission_id": _format_meta_field(p_info_raw.get("admission_id"), 1),
        "school_college_name": _format_meta_field(p_info_raw.get("school_college_name"), 1),
        "staff_student_type": _format_meta_field(p_info_raw.get("staff_student_type"), 1),
        "full_name": _format_meta_field(p_info_raw.get("full_name") or data.get("person_name") or health_raw.get("patient_name") or health_raw.get("name"), 1),
        "gender": _format_meta_field(p_info_raw.get("gender") or data.get("gender"), 1),
        "year_of_study": _format_meta_field(p_info_raw.get("year_of_study"), 1),
        "course_dept": _format_meta_field(p_info_raw.get("course_dept"), 1),
        "section": _format_meta_field(p_info_raw.get("section"), 1),
        "shift": _format_meta_field(p_info_raw.get("shift"), 1),
        "blood_group": _format_meta_field(p_info_raw.get("blood_group"), 1),
        "dob": _format_meta_field(p_info_raw.get("dob"), 1),
        "age": _format_meta_field(p_info_raw.get("age") or data.get("age"), 1),
        "mobile_number": _format_meta_field(p_info_raw.get("mobile_number") or p_info_raw.get("mobile"), 1),
        "marital_status": _format_meta_field(p_info_raw.get("marital_status"), 1),
        "parent_spouse_name": _format_meta_field(p_info_raw.get("parent_spouse_name"), 1),
        "number_of_children": _format_meta_field(p_info_raw.get("number_of_children"), 1),
        "address": _format_meta_field(p_info_raw.get("address"), 1),
        "consent_screening": _format_meta_field(p_info_raw.get("consent_screening") or data.get("consent", {}).get("attend_scheduled_basic_health_screening"), 1),
        "consent_first_aid": _format_meta_field(p_info_raw.get("consent_first_aid") or data.get("consent", {}).get("receive_care_from_shine_nursing_officer"), 1),
        "signature_present": _format_meta_field(p_info_raw.get("signature_present"), 1)
    }

    # Build Group 2: medical_history
    medical_history = {
        "known_allergies": _format_meta_field(med_hist_raw.get("known_allergies") or med_hist_raw.get("any_known_allergy"), 1),
        "abdominal_pain_indigestion_constipation": _format_meta_field(med_hist_raw.get("abdominal_pain_indigestion_constipation"), 1),
        "irregular_pcod_menopause": _format_meta_field(med_hist_raw.get("irregular_pcod_menopause"), 1),
        "skin_hair_ear_nose_problems": _format_meta_field(med_hist_raw.get("skin_hair_ear_nose_problems") or med_hist_raw.get("skin_hair_ear_nose_problem"), 1),
        "current_previous_medications": _format_meta_field(med_hist_raw.get("current_previous_medications") or med_hist_raw.get("any_current_previous_medications"), 1),
        "previous_surgery_hospitalization": _format_meta_field(med_hist_raw.get("previous_surgery_hospitalization") or med_hist_raw.get("any_previous_surgery_hospitalization"), 1),
        "social_history": _format_meta_field(med_hist_raw.get("social_history") or med_hist_raw.get("social_history_alcohol_smoking_tobacco"), 1),
        "physical_activity": _format_meta_field(med_hist_raw.get("physical_activity"), 1),
        "other_medical_issues": _format_meta_field(med_hist_raw.get("other_medical_issues") or med_hist_raw.get("any_other_medical_issues"), 1),
        "immediate_family_count": _format_meta_field(med_hist_raw.get("immediate_family_count"), 1),
        "personal_physician": _format_meta_field(med_hist_raw.get("personal_physician"), 1)
    }

    # Build Group 3: family_history
    family_history = {
        "thyroid": _format_meta_field(fam_hist_raw.get("thyroid"), 1),
        "diabetes": _format_meta_field(fam_hist_raw.get("diabetes"), 1),
        "heart_disease": _format_meta_field(fam_hist_raw.get("heart_disease"), 1),
        "hypertension": _format_meta_field(fam_hist_raw.get("hypertension"), 1),
        "cancer": _format_meta_field(fam_hist_raw.get("cancer"), 1),
        "epilepsy": _format_meta_field(fam_hist_raw.get("epilepsy"), 1),
        "asthma": _format_meta_field(fam_hist_raw.get("asthma"), 1),
        "other_conditions": _format_meta_field(fam_hist_raw.get("other_conditions") or fam_hist_raw.get("others"), 1)
    }

    # Build Group 4: health_assessment
    health_assessment = {
        "patient_name": _format_meta_field(health_raw.get("patient_name") or health_raw.get("name") or data.get("person_name"), 2),
        "examination_date": _format_meta_field(health_raw.get("examination_date") or health_raw.get("date_of_exam") or health_raw.get("doe"), 2),
        "height_cm": _format_meta_field(health_raw.get("height_cm") or data.get("height"), 2),
        "weight_kg": _format_meta_field(health_raw.get("weight_kg") or data.get("weight"), 2),
        "pulse_bpm": _format_meta_field(health_raw.get("pulse_bpm") or health_raw.get("pulse_mt") or health_raw.get("pulse"), 2),
        "blood_pressure_mmhg": _format_meta_field(health_raw.get("blood_pressure_mmhg") or health_raw.get("bp_mmhg") or data.get("blood_pressure"), 2),
        "random_blood_sugar_mgdl": _format_meta_field(health_raw.get("random_blood_sugar_mgdl") or health_raw.get("rbs_mg_dl") or health_raw.get("rbs"), 2),
        "waist_circumference_cm": _format_meta_field(health_raw.get("waist_circumference_cm"), 2),
        "ecg_notes": _format_meta_field(health_raw.get("ecg_notes") or health_raw.get("ecg"), 2),
        "echo_notes": _format_meta_field(health_raw.get("echo_notes") or health_raw.get("echo"), 2),
        "pallor": _format_meta_field(health_raw.get("pallor"), 2),
        "lymphadenopathy": _format_meta_field(health_raw.get("lymphadenopathy"), 2),
        "deformity": _format_meta_field(health_raw.get("deformity"), 2),
        "icterus": _format_meta_field(health_raw.get("icterus"), 2),
        "cyanosis": _format_meta_field(health_raw.get("cyanosis"), 2),
        "edema": _format_meta_field(health_raw.get("edema"), 2),
        "clubbing": _format_meta_field(health_raw.get("clubbing"), 2)
    }

    # Build Group 5: system_examination
    system_examination = {
        "ear": _format_meta_field(system_raw.get("ear"), 2),
        "nose": _format_meta_field(system_raw.get("nose"), 2),
        "throat": _format_meta_field(system_raw.get("throat"), 2),
        "respiratory_system": _format_meta_field(system_raw.get("respiratory_system") or system_raw.get("rs"), 2),
        "cardiovascular_system": _format_meta_field(system_raw.get("cardiovascular_system") or system_raw.get("cvs"), 2),
        "abdomen": _format_meta_field(system_raw.get("abdomen"), 2),
        "central_nervous_system": _format_meta_field(system_raw.get("central_nervous_system") or system_raw.get("cns"), 2),
        "skin": _format_meta_field(system_raw.get("skin"), 2),
        "hair": _format_meta_field(system_raw.get("hair"), 2),
        "musculoskeletal_system": _format_meta_field(system_raw.get("musculoskeletal_system"), 2),
        "others": _format_meta_field(system_raw.get("others"), 2),
        "psychological_consultation": _format_meta_field(system_raw.get("psychological_consultation"), 2)
    }

    # Build Group 6: vision
    vision = {
        "vision_category": _format_meta_field(vision_raw.get("vision_category") or ("unaided" if vision_raw.get("unaided") else None), 2),
        "distant_vision_right": _format_meta_field(vision_raw.get("distant_vision_right") or vision_raw.get("right_eye_distant_va") or vision_raw.get("right_eye"), 2),
        "distant_vision_left": _format_meta_field(vision_raw.get("distant_vision_left") or vision_raw.get("left_eye_distant_va") or vision_raw.get("left_eye"), 2)
    }

    # Build Group 7: dental
    dental = {
        "normal": _format_meta_field(dental_raw.get("normal"), 2),
        "abnormality": _format_meta_field(dental_raw.get("abnormality"), 2),
        "advice": _format_meta_field(dental_raw.get("advice"), 2)
    }

    # Build Group 8: referral
    referral = {
        "specialist_referred_to": _format_meta_field(referral_raw.get("specialist_referred_to") or data.get("referral_details"), 2),
        "doctor_signature_present": _format_meta_field(referral_raw.get("doctor_signature_present"), 2)
    }

    structured_record = {
        "personal_information": personal_information,
        "medical_history": medical_history,
        "family_history": family_history,
        "health_assessment": health_assessment,
        "system_examination": system_examination,
        "vision": vision,
        "dental": dental,
        "referral": referral
    }

    # Derive top-level backwards-compatible attributes
    person_name = personal_information["full_name"]["value"] or health_assessment["patient_name"]["value"]
    age = personal_information["age"]["value"]
    gender = personal_information["gender"]["value"]
    
    # Format vitals with units if not already present
    ht_raw = health_assessment["height_cm"]["value"]
    height = f"{ht_raw} cm" if ht_raw and "cm" not in str(ht_raw).lower() else ht_raw
    
    wt_raw = health_assessment["weight_kg"]["value"]
    weight = f"{wt_raw} kg" if wt_raw and "kg" not in str(wt_raw).lower() else wt_raw
    
    bp_raw = health_assessment["blood_pressure_mmhg"]["value"]
    blood_pressure = f"{bp_raw} mmHg" if bp_raw and "mmhg" not in str(bp_raw).lower() else bp_raw

    # Derive missing and uncertain fields
    missing_fields = _clean_list(data.get("missing_fields"))
    uncertain_fields = _clean_list(data.get("uncertain_fields"))

    # Automatically identify missing core fields if not flagged
    core_map = {
        "person_name": person_name,
        "age": age,
        "height": height,
        "weight": weight,
        "blood_pressure": blood_pressure
    }
    for f_name, f_val in core_map.items():
        if f_val is None and f_name not in missing_fields:
            missing_fields.append(f_name)

    # Build examination details summary
    exam_parts = []
    pulse = health_assessment["pulse_bpm"]["value"]
    if pulse: exam_parts.append(f"Pulse: {pulse} bpm")
    rbs = health_assessment["random_blood_sugar_mgdl"]["value"]
    if rbs: exam_parts.append(f"RBS: {rbs} mg/dL")
    waist = health_assessment["waist_circumference_cm"]["value"]
    if waist: exam_parts.append(f"Waist: {waist} cm")

    # General signs
    gen_signs = []
    for sign in ("pallor", "lymphadenopathy", "deformity", "icterus", "cyanosis", "edema", "clubbing"):
        val = health_assessment[sign]["value"]
        if val and str(val).lower() not in ("no", "unchecked", "none"):
            gen_signs.append(f"{sign.capitalize()}: {val}")
    if gen_signs:
        exam_parts.append("General Signs: " + ", ".join(gen_signs))

    # Systemic findings
    sys_findings = []
    for sys_name, sys_meta in system_examination.items():
        if sys_meta["value"] == "abnormal" or sys_meta["details"]:
            sys_findings.append(f"{sys_name.replace('_', ' ').title()}: {sys_meta['details'] or 'Abnormal'}")
    if sys_findings:
        exam_parts.append("Systemic: " + "; ".join(sys_findings))
    elif not exam_parts:
        exam_parts.append("Systemic examination completed.")

    examination_details = "; ".join(exam_parts)
    referral_details = referral["specialist_referred_to"]["value"]

    extraction_notes = _clean_str(data.get("extraction_notes")) or "Health screening document parsed successfully."

    return {
        "person_name": person_name,
        "age": age,
        "gender": gender,
        "height": height,
        "weight": weight,
        "blood_pressure": blood_pressure,
        "examination_details": examination_details,
        "referral_details": referral_details,
        "missing_fields": missing_fields,
        "uncertain_fields": uncertain_fields,
        "extraction_notes": extraction_notes,
        "structured_record": structured_record,
        "personal_information": personal_information,
        "medical_history": medical_history,
        "family_history": family_history,
        "health_assessment": health_assessment,
        "system_examination": system_examination,
        "vision": vision,
        "dental": dental,
        "referral": referral
    }
