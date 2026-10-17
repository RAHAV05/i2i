"""Unit tests for AI service abstraction and Groq service error handling."""

import pytest
from unittest.mock import patch, MagicMock
from backend.services.groq_service import GroqService

def test_groq_service_unconfigured():
    service = GroqService(api_key="")
    assert not service.is_configured()
    res = service.extract_from_images(b"fake_p1", "image/jpeg", b"fake_p2", "image/jpeg")
    assert res["success"] is False
    assert res["code"] == "AI_NOT_CONFIGURED"
    assert res["manual_entry_allowed"] is True

def test_groq_service_configured_flag():
    service = GroqService(api_key="gsk_test_12345678901234567890")
    assert service.is_configured()

def test_groq_service_default_model_and_override():
    service = GroqService(api_key="gsk_test_12345678901234567890")
    assert "qwen/qwen3.8-27b" in service.provider_name()

    custom_service = GroqService(api_key="gsk_test_12345678901234567890", model="custom/model-v1")
    assert "custom/model-v1" in custom_service.provider_name()

@patch("requests.post")
def test_groq_service_auth_error(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Invalid API key"
    mock_post.return_value = mock_resp

    service = GroqService(api_key="gsk_invalid_key_testing")
    res = service.extract_from_images(b"img1", "image/jpeg", b"img2", "image/jpeg")
    assert res["success"] is False
    assert res["code"] == "AUTH_ERROR"
    assert res["manual_entry_allowed"] is True

@patch("requests.post")
def test_groq_service_successful_extraction(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '{"person_name": "Rohan M", "age": "42", "gender": "Male", "height": "168 cm", "weight": "68 kg", "blood_pressure": "118/78", "examination_details": "Normal", "referral_details": null, "missing_fields": [], "uncertain_fields": [], "extraction_notes": "Clean scan"}'
                }
            }
        ]
    }
    mock_post.return_value = mock_resp

    service = GroqService(api_key="gsk_valid_testing_key")
    res = service.extract_from_images(b"img1", "image/jpeg", b"img2", "image/jpeg")
    assert res["success"] is True
    assert res["data"]["person_name"] == "Rohan M"
    assert res["data"]["age"] == "42"
    assert res["data"]["gender"] == "Male"
