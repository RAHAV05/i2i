"""Unit and integration tests for GeminiService and Step 0 model verification."""

import os
import pytest
from unittest.mock import patch, MagicMock
from PIL import Image
import io

from backend.services.gemini_service import (
    GeminiService,
    preprocess_image,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_FALLBACK_MODEL
)


def test_gemini_service_defaults_and_configuration():
    service = GeminiService(api_key="test_key_1234567890123")
    assert service.is_configured()
    assert service.model == DEFAULT_GEMINI_MODEL
    assert service.fallback_model == DEFAULT_FALLBACK_MODEL
    assert "gemini-3.6-flash" in service.provider_name()


def test_gemini_service_custom_models():
    service = GeminiService(
        api_key="test_key_1234567890123",
        model="gemini-3.5-flash-lite",
        fallback_model="gemini-3.6-flash"
    )
    assert service.model == "gemini-3.5-flash-lite"
    assert service.fallback_model == "gemini-3.6-flash"
    assert "gemini-3.5-flash-lite" in service.provider_name()


def test_image_preprocessing_resize():
    # Create a 2400 x 1800 test image in memory
    img = Image.new("RGB", (2400, 1800), color=(15, 23, 42))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    original_bytes = buf.getvalue()

    processed_bytes = preprocess_image(original_bytes, max_edge=1568, quality=80)
    with Image.open(io.BytesIO(processed_bytes)) as p_img:
        w, h = p_img.size
        assert max(w, h) == 1568
        assert p_img.format == "JPEG"


def test_gemini_cost_calculation():
    service = GeminiService(api_key="test_key_1234567890123")
    # 7000 in, 1500 out, 100 think -> in USD: (7000 * 1.5 + 1600 * 7.5)/1M = (10500 + 12000)/1M = $0.0225
    # In INR at 97: 0.0225 * 97 = Rs 2.1825
    cost = service.calculate_cost_rs(tokens_in=7000, tokens_out=1500, tokens_think=100, is_fallback=False)
    assert 2.0 <= cost <= 2.5


def test_gemini_escalation_rules():
    service = GeminiService(api_key="test_key_1234567890123")
    
    # 1. Normal clean record should not escalate
    clean_data = {
        "uncertain_fields": [],
        "missing_fields": [],
        "blood_pressure": "120/80"
    }
    should, reason = service._should_escalate(clean_data)
    assert not should

    # 2. Blank fields with confidence 1 should NEVER cause escalation
    blank_and_few = {
        "uncertain_fields": ["allergy", "eye_sight"],
        "missing_fields": ["address", "other_conditions"],
        "blood_pressure": "120/80"
    }
    should, reason = service._should_escalate(blank_and_few)
    assert not should, "Blank fields should never count towards escalation"

    # 3. 3+ low-confidence fields should escalate
    flagged_3 = {
        "uncertain_fields": ["allergy", "eye_sight", "dental_caries"],
        "missing_fields": [],
        "blood_pressure": "120/80"
    }
    should, reason = service._should_escalate(flagged_3)
    assert should
    assert "low-confidence" in reason

    # 4. Record with flagged critical BP should escalate
    flagged_bp = {
        "uncertain_fields": ["blood_pressure"],
        "missing_fields": []
    }
    should, reason = service._should_escalate(flagged_bp)
    assert should
    assert "blood_pressure" in reason

    # 5. Implausible BP (systolic < diastolic or impossible value)
    implausible_bp = {
        "uncertain_fields": [],
        "missing_fields": [],
        "blood_pressure": "70/120"
    }
    should, reason = service._should_escalate(implausible_bp)
    assert should



@pytest.mark.skipif(not os.getenv("GEMINI_API_KEY"), reason="Requires GEMINI_API_KEY")
def test_step0_real_scan_call():
    """Step 0 Acceptance Test: Call the default model with real sample forms and confirm valid JSON, no 404."""
    p1_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "filled_sample_page_1.jpeg")
    p2_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "filled_sample_page_2.jpeg")
    
    if not os.path.exists(p1_path) or not os.path.exists(p2_path):
        pytest.skip("Sample fixtures not found")

    with open(p1_path, "rb") as f1, open(p2_path, "rb") as f2:
        p1_bytes = f1.read()
        p2_bytes = f2.read()

    service = GeminiService()
    assert service.is_configured()
    res = service.extract_from_images(p1_bytes, "image/jpeg", p2_bytes, "image/jpeg")

    assert res.get("success") is True, f"Extraction failed: {res.get('error')}"
    assert res.get("code") != "MODEL_NOT_FOUND"
    assert "usage" in res
    assert res["usage"]["tokens_in"] > 0
    assert res["usage"]["cost_rs"] > 0
    assert "data" in res
