"""Unit and integration tests for Phase 3: Extraction v2 & Medical Glossary Service.
"""

import io
import pytest
from PIL import Image
from backend.services.glossary_service import ClinicalGlossaryService, default_glossary_service
from backend.services.extraction_v2 import ExtractionV2Pipeline, default_extraction_pipeline
from backend.services.template_engine import default_template_engine


# ==============================================================================
# 1. FIELD-SCOPED GLOSSARY TESTS
# ==============================================================================

def test_scoped_disambiguation_od_medication_vs_vision():
    """Verify 'OD' expands to 'Once Daily' in meds, but 'Right Eye' in vision."""
    glossary = default_glossary_service
    
    med_res = glossary.normalize_text("OD", scope="medications")
    assert med_res["is_expanded"] is True
    assert "Once Daily" in med_res["normalized"]

    vis_res = glossary.normalize_text("OD", scope="vision")
    assert vis_res["is_expanded"] is True
    assert "Right Eye" in vis_res["normalized"]


def test_scoped_disambiguation_os_and_ou_in_vision():
    """Verify 'OS' and 'OU' expand correctly in vision scope."""
    glossary = default_glossary_service
    
    os_res = glossary.normalize_text("OS", scope="vision")
    assert "Left Eye" in os_res["normalized"]

    ou_res = glossary.normalize_text("OU", scope="vision")
    assert "Both Eyes" in ou_res["normalized"]


def test_referral_abbreviations():
    """Verify 'Ref' -> 'Referred' and 'R/V' -> 'Review Advised'."""
    glossary = default_glossary_service
    
    ref_res = glossary.normalize_text("Ref", scope="referral")
    assert "Referred" in ref_res["normalized"]

    rv_res = glossary.normalize_text("R/V", scope="referral")
    assert "Review Advised" in rv_res["normalized"]


def test_examination_abbreviations():
    """Verify 'NAD', 'CVS', and 'RS' in systemic examination scope."""
    glossary = default_glossary_service
    
    nad_res = glossary.normalize_text("NAD", scope="examination")
    assert "No Abnormality Detected" in nad_res["normalized"]

    cvs_res = glossary.normalize_text("CVS", scope="examination")
    assert "Cardiovascular System" in cvs_res["normalized"]


def test_fuzzy_matching_controlled_terminology():
    """Verify minor misspelling matches controlled clinical vocabulary."""
    glossary = default_glossary_service
    
    # 'conjunctivitiss' -> 'Conjunctivitis'
    res = glossary.normalize_text("conjunctivitiss", scope="examination")
    assert res["normalized"].lower() == "conjunctivitis"
    assert res["status"] == "fuzzy_match"


def test_invariant_numbers_never_altered_by_glossary():
    """Numbers and clinical measurements must never be modified by glossary."""
    glossary = default_glossary_service
    
    for val in ["120/80", "168.5 cm", "72 kg", "98 bpm", "140 mg/dL"]:
        res = glossary.normalize_text(val)
        assert res["normalized"] == val
        assert res["is_expanded"] is False


def test_unknown_abbreviation_kept_as_written():
    """Unrecognized abbreviations are preserved raw and marked 'new_term'."""
    glossary = default_glossary_service
    
    res = glossary.normalize_text("XYZ_UNKNOWN_SHORT", scope="examination")
    assert res["normalized"] == "XYZ_UNKNOWN_SHORT"
    assert res["status"] == "new_term"
    assert res["is_expanded"] is False


# ==============================================================================
# 2. EXTRACTION V2 PIPELINE TESTS
# ==============================================================================

def test_build_dynamic_prompt_from_template():
    """Verify dynamic multimodal prompt generation includes form fields."""
    pipe = default_extraction_pipeline
    tpl = default_template_engine.get_template("tpl_adult_examination_v9_1")
    prompt = pipe.build_system_prompt_for_template(tpl)
    
    assert "NEVER GUESS" in prompt
    assert "PRESERVE NUMBERS EXACTLY" in prompt
    assert "blood_pressure" in prompt
    assert "height" in prompt
    assert "weight" in prompt


def test_process_extracted_payload_with_scoped_glossary():
    """Verify payload processing normalizes fields according to template definitions."""
    pipe = default_extraction_pipeline
    raw_json = {
        "person_name": {"raw_text": "Rohan Gupta", "confidence": 0.95},
        "exam_date": {"raw_text": "26/10/2026", "confidence": 0.98},
        "height": {"raw_text": "172", "confidence": 0.92},
        "weight": {"raw_text": "68", "confidence": 0.94},
        "blood_pressure": {"raw_text": "120/80", "confidence": 0.95},
        "sys_respiratory": {"raw_text": "NAD", "confidence": 0.90},
        "sys_cvs": {"raw_text": "S1S2", "confidence": 0.88},
        "vision_distant_right": {"raw_text": "6/6", "confidence": 0.95},
        "vision_distant_left": {"raw_text": "6/9", "confidence": 0.92},
        "doctor_name": {"raw_text": "Dr. Ananya Rao", "confidence": 0.96},
        "doctor_signature": {"raw_text": "Present", "confidence": 0.99}
    }

    result = pipe.process_extracted_payload("tpl_adult_examination_v9_1", raw_json)
    fields = result["fields"]

    assert fields["person_name"]["normalized_value"] == "Rohan Gupta"
    assert fields["sys_respiratory"]["normalized_value"] == "No Abnormality Detected"
    assert "Heart Sounds" in fields["sys_cvs"]["normalized_value"]
    assert fields["vision_distant_right"]["status"] == "valid"
    assert len(result["missing_fields"]) == 0
    assert result["needs_escalation"] is False


def test_snellen_fraction_validation():
    """Verify invalid Snellen vision fractions are flagged."""
    pipe = default_extraction_pipeline
    field_def = {"key": "vision_distant_right", "type": "fraction", "glossary_scope": "vision"}
    
    # Valid
    val_ok = pipe.validate_and_normalize_field(field_def, {"raw_text": "6/6", "confidence": 0.9})
    assert val_ok["status"] == "valid"

    # Ambiguous / Non-standard Snellen
    val_bad = pipe.validate_and_normalize_field(field_def, {"raw_text": "20/2000", "confidence": 0.9})
    assert val_bad["status"] == "ambiguous_vision"


def test_blood_pressure_bounds_validation():
    """Verify implausible blood pressure readings are flagged."""
    pipe = default_extraction_pipeline
    field_def = {"key": "blood_pressure", "type": "blood_pressure"}
    
    # Valid BP
    bp_ok = pipe.validate_and_normalize_field(field_def, {"raw_text": "124/82", "confidence": 0.95})
    assert bp_ok["status"] == "valid"
    assert bp_ok["normalized_value"] == "124/82"

    # Implausible BP (e.g. systolic 450)
    bp_bad = pipe.validate_and_normalize_field(field_def, {"raw_text": "450/280", "confidence": 0.95})
    assert bp_bad["status"] == "implausible_vitals"


def test_crop_bounding_box_generation():
    """Verify crop_region correctly crops image bytes based on normalized coordinates."""
    # Create simple 100x100 RGB image
    img = Image.new("RGB", (100, 100), color="blue")
    in_buf = io.BytesIO()
    img.save(in_buf, format="JPEG")
    raw_bytes = in_buf.getvalue()

    # Crop center 50x50 box: x=0.25, y=0.25, w=0.5, h=0.5
    cropped_bytes = ExtractionV2Pipeline.crop_region(raw_bytes, {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5})
    cropped_img = Image.open(io.BytesIO(cropped_bytes))
    
    assert cropped_img.size == (50, 50)


def test_image_hash_deduplication():
    """Verify SHA-256 deduplication hash consistency."""
    sample_bytes = b"sample_jpeg_payload_for_testing"
    h1 = ExtractionV2Pipeline.compute_image_hash(sample_bytes)
    h2 = ExtractionV2Pipeline.compute_image_hash(sample_bytes)
    assert h1 == h2
    assert len(h1) == 64


def test_pass2_crop_escalation_micro_reread():
    """Verify Pass 2 micro-crop escalation re-reads low-confidence vital signs."""
    pipe = default_extraction_pipeline
    img = Image.new("RGB", (200, 200), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_bytes = buf.getvalue()

    # Pass 1 produces low confidence for blood_pressure
    pass1_payload = {
        "person_name": {"raw_text": "Aarav Sharma", "confidence": 0.95},
        "exam_date": {"raw_text": "26/10/2026", "confidence": 0.95},
        "blood_pressure": {"raw_text": "12?/8?", "confidence": 0.45}
    }
    pass1_res = pipe.process_extracted_payload("tpl_adult_examination_v9_1", pass1_payload)
    assert "blood_pressure" in pass1_res["uncertain_fields"]

    # Mock secondary escalation model caller
    called_crops = []
    def mock_model_caller(crop_bytes, field_label, field_type, model):
        called_crops.append(field_label)
        return {"raw_text": "120/80", "confidence": 0.96}

    pass2_res = pipe.execute_pass2_crop_escalation(
        image_bytes=img_bytes,
        template_id="tpl_adult_examination_v9_1",
        pass1_result=pass1_res,
        model_caller=mock_model_caller
    )

    assert "blood_pressure" in pass2_res["escalated_fields"]
    assert pass2_res["fields"]["blood_pressure"]["normalized_value"] == "120/80"
    assert pass2_res["fields"]["blood_pressure"]["escalated"] is True
    assert pass2_res["fields"]["blood_pressure"]["pass2_applied"] is True
    assert "blood_pressure" not in pass2_res["uncertain_fields"]
    assert len(called_crops) == 1


def test_pass2_skips_blanks_to_save_tokens():
    """Blanks and unrecorded fields must NEVER be queried in Pass 2."""
    pipe = default_extraction_pipeline
    img = Image.new("RGB", (200, 200), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_bytes = buf.getvalue()

    pass1_payload = {
        "person_name": {"raw_text": "Aarav Sharma", "confidence": 0.95},
        "exam_date": {"raw_text": "26/10/2026", "confidence": 0.95},
        "blood_pressure": {"raw_text": "", "confidence": 0.10}
    }
    pass1_res = pipe.process_extracted_payload("tpl_adult_examination_v9_1", pass1_payload)

    caller_invoked = []
    def mock_caller(crop_bytes, field_label, field_type, model):
        caller_invoked.append(field_label)
        return {"raw_text": "120/80", "confidence": 0.95}

    pass2_res = pipe.execute_pass2_crop_escalation(
        image_bytes=img_bytes,
        template_id="tpl_adult_examination_v9_1",
        pass1_result=pass1_res,
        model_caller=mock_caller
    )

    # Empty raw_text should not be re-queried
    assert len(caller_invoked) == 0
    assert len(pass2_res["escalated_fields"]) == 0

