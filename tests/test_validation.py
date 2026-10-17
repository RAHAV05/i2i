"""Unit tests for extraction schema validation and sanitization."""

import pytest
from backend.services.ai_service import validate_and_sanitize_extraction

def test_validate_and_sanitize_valid_dict():
    raw = {
        "person_name": "Suresh Raina",
        "age": "38",
        "gender": "Male",
        "height": "175 cm",
        "weight": "73 kg",
        "blood_pressure": "125/82 mmHg",
        "examination_details": "Normal chest sounds, vision 6/6",
        "referral_details": "None",
        "missing_fields": [],
        "uncertain_fields": ["blood_pressure"],
        "extraction_notes": "Page 1 handwriting clear."
    }
    sanitized = validate_and_sanitize_extraction(raw)
    assert sanitized["person_name"] == "Suresh Raina"
    assert sanitized["age"] == "38"
    assert sanitized["uncertain_fields"] == ["blood_pressure"]
    assert sanitized["missing_fields"] == []

def test_validate_and_sanitize_null_handling():
    raw = {
        "person_name": "Kavitha M",
        "age": "N/A",
        "gender": "Female",
        "height": None,
        "weight": "undefined",
        "blood_pressure": "",
        "examination_details": "Mild wheezing",
        "referral_details": None,
        "missing_fields": ["blood_pressure"],
        "uncertain_fields": None
    }
    sanitized = validate_and_sanitize_extraction(raw)
    assert sanitized["person_name"] == "Kavitha M"
    assert sanitized["age"] is None  # "N/A" converted to null
    assert sanitized["height"] is None
    assert sanitized["weight"] is None
    assert sanitized["blood_pressure"] is None
    assert "height" in sanitized["missing_fields"]
    assert "weight" in sanitized["missing_fields"]
    assert "age" in sanitized["missing_fields"]
    assert isinstance(sanitized["uncertain_fields"], list)

def test_validate_from_markdown_codeblock():
    markdown_str = """Here is the extracted information:
```json
{
  "person_name": "Pooja V",
  "age": 29,
  "gender": "Female",
  "height": "160 cm",
  "weight": "55 kg",
  "blood_pressure": "110/70",
  "examination_details": "Clear lungs",
  "referral_details": null,
  "missing_fields": [],
  "uncertain_fields": []
}
```
"""
    sanitized = validate_and_sanitize_extraction(markdown_str)
    assert sanitized["person_name"] == "Pooja V"
    assert sanitized["age"] == "29"
    assert sanitized["referral_details"] is None
