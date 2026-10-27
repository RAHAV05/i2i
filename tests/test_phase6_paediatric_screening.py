"""Phase 6 tests: Paediatric IAP Percentile Lookup, BP Staging & ATR Alert Engine.

Tests:
  1. IAP BMI-for-age percentile classification (boys & girls, all 5 categories).
  2. Paediatric BP-for-height staging (Normal, Elevated, Stage 1/2 HTN).
  3. ATR caliper alert (< 5°, 5–9°, ≥ 10°).
  4. classify_paediatric() unified dispatcher with field dict input.
  5. is_paediatric=False guard (adult patient → skips paediatric classifications).
  6. Integration: apply_server_side_flags returns paediatric_alerts for a 14-year-old.
"""

import math
import pytest
from backend.services.paediatric_screening import (
    classify_paediatric_bmi,
    classify_paediatric_bp,
    classify_atr,
    classify_paediatric,
)
from backend.services.ai_service import apply_server_side_flags


# ---------------------------------------------------------------------------
# 1. IAP BMI-for-age percentile — boys
# ---------------------------------------------------------------------------

class TestPaediatricBMIBoys:
    def test_normal_weight(self):
        # Boy, age 10, BMI ~17 → should be Normal (15th–84.9th centile)
        result = classify_paediatric_bmi(bmi=17.0, age_years=10, sex="M")
        assert result["category"] == "Normal Weight"
        assert result["alert_level"] == "optimal"
        assert result["is_high_risk"] is False
        assert result["centile"] is not None
        assert 15.0 <= result["centile"] <= 85.0

    def test_overweight(self):
        # Boy, age 10, BMI 19 → Overweight (85th–94.9th centile, actual centile ≈ 92.2)
        result = classify_paediatric_bmi(bmi=19.0, age_years=10, sex="Male")
        assert result["category"] == "Overweight"
        assert result["alert_level"] == "advisory"
        assert result["is_high_risk"] is False
        assert result["centile"] is not None

    def test_obese(self):
        # Boy, age 10, BMI ~26 → Obese (≥ 95th centile)
        result = classify_paediatric_bmi(bmi=26.0, age_years=10, sex="Boy")
        assert result["category"] == "Obese"
        assert result["alert_level"] in ("high", "critical")
        assert result["is_high_risk"] is True

    def test_severely_thin(self):
        # Boy, age 10, BMI ~10 → Severely Thin (< 3rd centile)
        result = classify_paediatric_bmi(bmi=10.5, age_years=10, sex="M")
        assert result["category"] == "Severely Thin"
        assert result["alert_level"] == "critical"
        assert result["is_high_risk"] is True

    def test_moderately_thin(self):
        # Boy, age 10, BMI 14.5 → Moderately Thin (3rd–14.9th centile, actual ≈ 8th)
        result = classify_paediatric_bmi(bmi=14.5, age_years=10, sex="M")
        assert result["category"] == "Moderately Thin"
        assert result["alert_level"] == "high"
        assert result["is_high_risk"] is True


# ---------------------------------------------------------------------------
# 2. IAP BMI-for-age percentile — girls
# ---------------------------------------------------------------------------

class TestPaediatricBMIGirls:
    def test_normal_weight_girl(self):
        # Girl, age 12, BMI 17 → Normal Weight (actual centile ≈ 23.2)
        result = classify_paediatric_bmi(bmi=17.0, age_years=12, sex="F")
        assert result["category"] == "Normal Weight"
        assert result["alert_level"] == "optimal"

    def test_overweight_girl(self):
        result = classify_paediatric_bmi(bmi=23.0, age_years=14, sex="Female")
        assert result["category"] in ("Overweight", "Obese")
        assert result["is_high_risk"] is not None

    def test_sex_aliases(self):
        # 'Girl', 'Woman', 'female' should all map to sex_used="F"
        for alias in ("Girl", "woman", "Female", "F", "f"):
            r = classify_paediatric_bmi(bmi=17.0, age_years=10, sex=alias)
            assert r["sex_used"] == "F"


# ---------------------------------------------------------------------------
# 3. Edge cases: None, out-of-range age, invalid values
# ---------------------------------------------------------------------------

class TestPaediatricBMIEdgeCases:
    def test_none_bmi(self):
        r = classify_paediatric_bmi(None, 10, "M")
        assert r["category"] == "Unknown"

    def test_none_age(self):
        r = classify_paediatric_bmi(17.0, None, "M")
        assert r["category"] == "Unknown"

    def test_adult_age_returns_note(self):
        r = classify_paediatric_bmi(22.0, 25.0, "M")
        assert r["category"] == "Unknown"
        assert "adult" in r["note"].lower()

    def test_invalid_bmi_string(self):
        r = classify_paediatric_bmi("abc", 10, "M")
        assert r["category"] == "Unknown"

    def test_age_boundary_2(self):
        r = classify_paediatric_bmi(16.0, 2, "M")
        assert r["category"] != "Unknown"

    def test_age_boundary_18(self):
        r = classify_paediatric_bmi(20.0, 18, "F")
        assert r["category"] != "Unknown"


# ---------------------------------------------------------------------------
# 4. Paediatric BP staging
# ---------------------------------------------------------------------------

class TestPaediatricBP:
    def test_normal(self):
        r = classify_paediatric_bp(systolic=90, diastolic=55, age_years=10, sex="M")
        assert r["stage"] == "Normal"
        assert r["alert_level"] == "normal"
        assert r["is_high_risk"] is False

    def test_elevated(self):
        # systolic ≥ p90 but < p95 for boy age 10 (p90=111, p95=115)
        r = classify_paediatric_bp(systolic=112, diastolic=65, age_years=10, sex="M")
        assert r["stage"] == "Elevated BP"
        assert r["alert_level"] == "advisory"
        assert r["is_high_risk"] is False

    def test_stage_1_htn(self):
        # systolic ≥ p95 for boy age 10 (p95=115)
        r = classify_paediatric_bp(systolic=116, diastolic=73, age_years=10, sex="M")
        assert r["stage"] == "Stage 1 HTN"
        assert r["alert_level"] == "high"
        assert r["is_high_risk"] is True

    def test_stage_2_htn(self):
        # systolic > p99+5 = (115+12)+5 = 132 for boy age 10
        r = classify_paediatric_bp(systolic=140, diastolic=90, age_years=10, sex="Male")
        assert r["stage"] == "Stage 2 HTN"
        assert r["alert_level"] == "critical"
        assert r["is_high_risk"] is True

    def test_no_diastolic(self):
        r = classify_paediatric_bp(systolic=95, diastolic=None, age_years=8, sex="F")
        assert r["stage"] in ("Normal", "Elevated BP", "Stage 1 HTN", "Stage 2 HTN")

    def test_none_systolic(self):
        r = classify_paediatric_bp(None, None, 10, sex="M")
        assert r["stage"] == "Unknown"

    def test_none_age(self):
        r = classify_paediatric_bp(100, 60, None, sex="M")
        assert r["stage"] == "Unknown"

    def test_reference_values_present(self):
        r = classify_paediatric_bp(100, 65, 12, sex="F")
        assert r["reference_p90_sys"] is not None
        assert r["reference_p95_sys"] is not None
        assert isinstance(r["reference_p90_sys"], int)

    def test_age_used_clipped_to_range(self):
        # Age 0 should clip to 1
        r = classify_paediatric_bp(80, 50, 0.5, sex="M")
        assert r["age_used"] == 1


# ---------------------------------------------------------------------------
# 5. ATR Caliper Alert
# ---------------------------------------------------------------------------

class TestATRAlert:
    def test_negative_below_5(self):
        r = classify_atr(3.5)
        assert r["alert_triggered"] is False
        assert r["alert_level"] == "normal"

    def test_zero(self):
        r = classify_atr(0)
        assert r["alert_triggered"] is False

    def test_exactly_5_triggers(self):
        r = classify_atr(5.0)
        assert r["alert_triggered"] is True
        assert r["alert_level"] == "advisory"
        assert r["is_high_risk"] is False

    def test_7_degrees_advisory(self):
        r = classify_atr(7.2)
        assert r["alert_triggered"] is True
        assert r["alert_level"] == "advisory"
        assert "orthopedic" in r["description"].lower()

    def test_10_degrees_critical(self):
        r = classify_atr(10.0)
        assert r["alert_triggered"] is True
        assert r["alert_level"] == "critical"
        assert r["is_high_risk"] is True

    def test_high_atr_urgent_referral(self):
        r = classify_atr(15)
        assert "urgent" in r["description"].lower()

    def test_none_returns_no_alert(self):
        r = classify_atr(None)
        assert r["alert_triggered"] is False
        assert r["atr_value"] is None

    def test_string_input(self):
        # Accept "7.5°" as numeric
        r = classify_atr("7.5°")
        assert r["atr_value"] == 7.5
        assert r["alert_triggered"] is True


# ---------------------------------------------------------------------------
# 6. Unified classify_paediatric() dispatcher
# ---------------------------------------------------------------------------

class TestClassifyPaediatric:
    def _fields(self, age, sex, weight, height, bp="100/65", atr=None):
        """Helper: build field dict in [value, confidence] format."""
        d = {
            "age": [str(age), 0.99],
            "gender": [sex, 0.99],
            "weight_kg": [str(weight), 0.99],
            "height_cm": [str(height), 0.99],
            "bp": [bp, 0.95],
        }
        if atr is not None:
            d["atr"] = [str(atr), 0.99]
        return d

    def test_paediatric_flag_set(self):
        r = classify_paediatric(self._fields(14, "Male", 62, 165))
        assert r["is_paediatric"] is True

    def test_adult_skips_paediatric_staging(self):
        r = classify_paediatric(self._fields(22, "Male", 72, 175))
        assert r["is_paediatric"] is False
        assert r["paediatric_bmi_classification"].get("category") == "Not Applicable"

    def test_bmi_computed_correctly(self):
        # BMI = 62 / (1.65^2) = 22.77
        r = classify_paediatric(self._fields(14, "Male", 62, 165))
        bmi = r["paediatric_calculated_bmi"]
        assert bmi is not None
        assert abs(bmi - 22.8) < 0.3

    def test_atr_alert_appears_in_paediatric_alerts(self):
        r = classify_paediatric(self._fields(12, "Female", 45, 155, atr=8.0))
        assert any("Scoliosis" in a for a in r["paediatric_alerts"])

    def test_no_alerts_for_healthy_child(self):
        # Healthy 10-year-old boy, normal vitals, no ATR
        r = classify_paediatric(self._fields(10, "M", 32, 140, bp="92/58"))
        # Should have normal BMI (category=Normal), normal BP
        assert r["is_paediatric"] is True
        bmi_cat = r["paediatric_bmi_classification"].get("category")
        assert bmi_cat == "Normal Weight"

    def test_alerts_list_not_empty_for_hypertensive_child(self):
        # Stage 2 HTN boy age 10 (p99+5 ≈ 132 sys)
        r = classify_paediatric(self._fields(10, "M", 32, 140, bp="145/95"))
        assert any("BP Alert" in a for a in r["paediatric_alerts"])


# ---------------------------------------------------------------------------
# 7. Integration: apply_server_side_flags passes through paediatric fields
# ---------------------------------------------------------------------------

class TestApplyServerSideFlagsIntegration:
    def test_paediatric_keys_in_result(self):
        fields = {
            "age": ["14", 0.99],
            "gender": ["Male", 0.99],
            "height_cm": ["165", 0.99],
            "weight_kg": ["78", 0.99],    # BMI ≈ 28.7 → Obese (paediatric)
            "bp": ["116/75", 0.95],
            "atr": ["6.5", 0.90],
        }
        result = apply_server_side_flags(fields)
        assert "is_paediatric" in result
        assert "paediatric_bmi_classification" in result
        assert "paediatric_bp_classification" in result
        assert "atr_classification" in result
        assert "paediatric_alerts" in result
        assert result["is_paediatric"] is True

    def test_paediatric_obese_alert_raised(self):
        fields = {
            "age": ["10", 0.99],
            "gender": ["M", 0.99],
            "height_cm": ["140", 0.99],
            "weight_kg": ["55", 0.99],   # BMI ≈ 28.1 → definitely Obese percentile
            "bp": ["96/62", 0.95],
        }
        result = apply_server_side_flags(fields)
        bmi_cls = result["paediatric_bmi_classification"]
        assert bmi_cls.get("category") in ("Obese", "Overweight")
        assert bmi_cls.get("is_high_risk") is True

    def test_adult_patient_no_paediatric_staging(self):
        fields = {
            "age": ["35", 0.99],
            "gender": ["Female", 0.99],
            "height_cm": ["160", 0.99],
            "weight_kg": ["65", 0.99],
            "bp": ["118/76", 0.95],
        }
        result = apply_server_side_flags(fields)
        assert result["is_paediatric"] is False
        assert result["paediatric_bmi_classification"].get("category") == "Not Applicable"

    def test_atr_alert_surfaces_in_flags_result(self):
        fields = {
            "age": ["13", 0.99],
            "gender": ["Female", 0.99],
            "height_cm": ["158", 0.99],
            "weight_kg": ["52", 0.99],
            "bp": ["100/65", 0.99],
            "atr": ["12", 0.88],         # ≥ 10° → urgent referral
        }
        result = apply_server_side_flags(fields)
        atr_cls = result["atr_classification"]
        assert atr_cls["alert_triggered"] is True
        assert atr_cls["alert_level"] == "critical"
        assert result["paediatric_alerts"]   # non-empty
