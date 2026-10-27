"""Paediatric Clinical Screening Engine — Phase 6 (IAP Consensus 2015/2023).

Implements:
  1. IAP BMI-for-age percentile lookup (boys & girls, age 2–18 years)
     Source: IAP Growth Monitoring Guidelines 2007 & revised consensus 2015.
     Cut-offs: < 3rd = Severely Thin, 3–14.9th = Moderately Thin,
               15–84.9th = Normal, 85–94.9th = Overweight, ≥ 95th = Obese.

  2. Paediatric BP-for-height percentile classification (age 1–17 years)
     Source: AAP CPG 2017 table (used by IAP) — 50th / 90th / 95th / 99th
     percentile of BP by age + height percentile band.
     Alert stages: Normal, Elevated, Stage-1-HTN, Stage-2-HTN.

  3. Adams Forward Bend Test (ATR) Alert
     ATR ≥ 5° → paediatric scoliosis referral flag per SRS / IAP guidelines.

Public API
----------
  classify_paediatric_bmi(bmi, age_years, sex) -> dict
  classify_paediatric_bp(systolic, diastolic, age_years, height_cm, sex) -> dict
  classify_atr(atr_degrees) -> dict
  classify_paediatric(fields) -> dict          # unified dispatcher (mirrors apply_server_side_flags)
"""

from __future__ import annotations

import math
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1.  IAP BMI-for-age LMS Parameters
# ---------------------------------------------------------------------------
# L, M, S parameters for the Cole 2000 LMS method (Box-Cox power).
# Source: WHO 2006/2007 reference adapted to IAP 2015 consensus cut-offs.
# Keys: (sex, age_years) — sex = "M" | "F", age = 2..18 integer (mid-year).
# Values: (L, M, S)
#
# For ages 2–5 use WHO 2006 child growth standards.
# For ages 5–18 use WHO 2007 growth reference.
# These are the internationally published LMS values; IAP uses the same
# reference curves with Indian-specific cut-off interpretation.

_LMS_BMI: Dict[Tuple[str, int], Tuple[float, float, float]] = {
    # (sex, age): (L, M, S)
    # --- BOYS ---
    ("M", 2):  (-0.3521, 16.4370, 0.08262),
    ("M", 3):  (-0.4855, 15.9731, 0.08090),
    ("M", 4):  (-0.6164, 15.6265, 0.07938),
    ("M", 5):  (-0.6498, 15.4425, 0.07777),
    ("M", 6):  (-1.1623, 15.4176, 0.08007),
    ("M", 7):  (-1.5696, 15.4654, 0.08268),
    ("M", 8):  (-1.9218, 15.6398, 0.08568),
    ("M", 9):  (-2.1422, 15.9090, 0.08963),
    ("M", 10): (-2.2032, 16.2531, 0.09316),
    ("M", 11): (-2.1760, 16.7030, 0.09645),
    ("M", 12): (-2.0652, 17.2190, 0.09942),
    ("M", 13): (-1.9057, 17.7574, 0.10154),
    ("M", 14): (-1.7480, 18.3241, 0.10295),
    ("M", 15): (-1.6090, 18.8498, 0.10352),
    ("M", 16): (-1.5135, 19.3057, 0.10329),
    ("M", 17): (-1.4735, 19.6930, 0.10260),
    ("M", 18): (-1.5044, 19.9925, 0.10137),
    # --- GIRLS ---
    ("F", 2):  (-0.3833, 16.0280, 0.08101),
    ("F", 3):  (-0.5094, 15.5253, 0.07969),
    ("F", 4):  (-0.6355, 15.2073, 0.07963),
    ("F", 5):  (-0.8534, 15.0957, 0.08019),
    ("F", 6):  (-1.3793, 15.2278, 0.08271),
    ("F", 7):  (-1.7606, 15.4562, 0.08600),
    ("F", 8):  (-2.0247, 15.8252, 0.09017),
    ("F", 9):  (-2.2107, 16.3186, 0.09418),
    ("F", 10): (-2.2980, 16.9076, 0.09714),
    ("F", 11): (-2.2726, 17.5509, 0.09918),
    ("F", 12): (-2.1577, 18.1952, 0.10003),
    ("F", 13): (-1.9899, 18.7945, 0.09999),
    ("F", 14): (-1.8321, 19.2974, 0.09999),
    ("F", 15): (-1.7052, 19.6956, 0.09984),
    ("F", 16): (-1.6167, 19.9748, 0.09953),
    ("F", 17): (-1.5768, 20.1515, 0.09938),
    ("F", 18): (-1.6004, 20.2212, 0.09958),
}

# IAP BMI percentile cut-offs (IAP 2015 consensus)
_BMI_CENTILE_CUTOFFS = [
    (3,   "Severely Thin",    "critical", True),   # < 3rd
    (15,  "Moderately Thin",  "high",     True),   # 3rd – 14.9th
    (85,  "Normal Weight",    "optimal",  False),  # 15th – 84.9th
    (95,  "Overweight",       "advisory", False),  # 85th – 94.9th
    (101, "Obese",            "high",     True),   # ≥ 95th
]


def _norm_cdf(z: float) -> float:
    """Standard normal CDF (Abramowitz & Stegun approximation, error < 7.5e-8)."""
    t = 1.0 / (1.0 + 0.2316419 * abs(z))
    poly = t * (0.319381530
                + t * (-0.356563782
                       + t * (1.781477937
                              + t * (-1.821255978
                                     + t * 1.330274429))))
    p = 1.0 - 0.3989422803601 * math.exp(-0.5 * z * z) * poly
    return p if z >= 0 else 1.0 - p


def _bmi_centile(bmi: float, L: float, M: float, S: float) -> float:
    """Convert raw BMI to centile using Cole LMS method."""
    if L == 0:
        z = math.log(bmi / M) / S
    else:
        z = ((bmi / M) ** L - 1.0) / (L * S)
    return _norm_cdf(z) * 100.0


def classify_paediatric_bmi(
    bmi: Optional[float],
    age_years: Optional[float],
    sex: Optional[str] = None,
) -> Dict[str, Any]:
    """IAP BMI-for-age percentile classification.

    Args:
        bmi: Computed BMI value (kg/m²).
        age_years: Age in decimal years (e.g. 10.5).
        sex: 'M'/'Male'/'Boy' or 'F'/'Female'/'Girl' (case-insensitive).

    Returns:
        dict with keys: centile, category, alert_level, is_high_risk,
                        staging, age_used, sex_used, note.
    """
    UNKNOWN = {
        "centile": None, "category": "Unknown", "alert_level": "none",
        "is_high_risk": False, "staging": "N/A", "age_used": None,
        "sex_used": None, "note": "Insufficient data for paediatric BMI classification."
    }

    if bmi is None or age_years is None:
        return UNKNOWN

    try:
        bmi = float(bmi)
        age_years = float(age_years)
    except (ValueError, TypeError):
        return UNKNOWN

    if not (2.0 <= age_years <= 18.9):
        return {**UNKNOWN, "note": f"Age {age_years:.1f}y out of paediatric range (2–18). Use adult classification."}

    # Normalise sex
    sex_norm = "M"
    if sex:
        s = str(sex).strip().upper()
        if s[0] in ("F", "G", "W"):  # Female / Girl / Woman
            sex_norm = "F"

    age_int = min(18, max(2, round(age_years)))

    lms = _LMS_BMI.get((sex_norm, age_int))
    if lms is None:
        return {**UNKNOWN, "note": f"No LMS table for sex={sex_norm}, age={age_int}."}

    L, M, S = lms
    try:
        centile = _bmi_centile(bmi, L, M, S)
    except (ValueError, ZeroDivisionError, OverflowError):
        return {**UNKNOWN, "note": "BMI centile computation failed (math error)."}

    for threshold, category, alert_level, is_high_risk in _BMI_CENTILE_CUTOFFS:
        if centile < threshold:
            staging = f"IAP 2015: {category} (BMI {bmi:.1f} @ {centile:.1f}th centile, age {age_int}y {sex_norm})"
            return {
                "centile": round(centile, 1),
                "category": category,
                "alert_level": alert_level,
                "is_high_risk": is_high_risk,
                "staging": staging,
                "age_used": age_int,
                "sex_used": sex_norm,
                "note": None,
            }
    # Fallback (centile ≥ 95 caught by Obese threshold 101)
    return {
        "centile": round(centile, 1),
        "category": "Obese",
        "alert_level": "high",
        "is_high_risk": True,
        "staging": f"IAP 2015: Obese (BMI {bmi:.1f} @ {centile:.1f}th centile)",
        "age_used": age_int,
        "sex_used": sex_norm,
        "note": None,
    }


# ---------------------------------------------------------------------------
# 2.  Paediatric BP-for-height percentile (AAP CPG 2017 simplified tables)
# ---------------------------------------------------------------------------
# Lookup: (age_years_int, height_percentile_band) → (p50_sys, p90_sys, p95_sys,
#                                                     p50_dia, p90_dia, p95_dia)
# Height bands: 5, 25, 50, 75, 95 (closest used)
# Source: AAP 2017 Clinical Practice Guideline (Table B1/B2).
#
# For brevity we store the 50th height-percentile row only, which is the
# most commonly used reference for screening purposes.  A full table would
# require ≈ 400 rows; the 50th percentile row gives clinically adequate
# precision for camp screening use.

_BP_50TH_HT: Dict[Tuple[str, int], Tuple[int, int, int, int, int, int]] = {
    # (sex, age): (p50_sys, p90_sys, p95_sys, p50_dia, p90_dia, p95_dia)
    # --- BOYS ---
    ("M", 1):  (85, 98, 102, 40, 52, 54),
    ("M", 2):  (87, 101, 105, 42, 54, 58),
    ("M", 3):  (88, 102, 105, 44, 56, 60),
    ("M", 4):  (89, 102, 106, 46, 58, 62),
    ("M", 5):  (90, 103, 107, 48, 60, 63),
    ("M", 6):  (91, 104, 108, 50, 62, 65),
    ("M", 7):  (92, 105, 109, 52, 64, 67),
    ("M", 8):  (94, 107, 111, 54, 66, 69),
    ("M", 9):  (95, 109, 113, 55, 67, 70),
    ("M", 10): (97, 111, 115, 57, 68, 72),
    ("M", 11): (99, 113, 117, 58, 70, 74),
    ("M", 12): (101, 115, 119, 59, 72, 75),
    ("M", 13): (104, 117, 121, 60, 73, 76),
    ("M", 14): (106, 120, 124, 60, 73, 76),
    ("M", 15): (109, 122, 126, 61, 74, 77),
    ("M", 16): (111, 125, 129, 63, 75, 78),
    ("M", 17): (114, 127, 131, 65, 77, 80),
    # --- GIRLS ---
    ("F", 1):  (84, 97, 101, 41, 53, 57),
    ("F", 2):  (85, 98, 102, 43, 56, 59),
    ("F", 3):  (86, 100, 104, 44, 57, 61),
    ("F", 4):  (88, 101, 105, 46, 59, 62),
    ("F", 5):  (89, 102, 106, 48, 61, 64),
    ("F", 6):  (91, 104, 108, 50, 62, 65),
    ("F", 7):  (93, 106, 110, 52, 63, 66),
    ("F", 8):  (95, 108, 112, 53, 65, 68),
    ("F", 9):  (96, 110, 114, 55, 67, 70),
    ("F", 10): (98, 112, 116, 56, 68, 71),
    ("F", 11): (100, 114, 118, 57, 69, 72),
    ("F", 12): (102, 116, 120, 58, 71, 74),
    ("F", 13): (104, 117, 121, 60, 72, 75),
    ("F", 14): (105, 118, 122, 61, 73, 76),
    ("F", 15): (107, 120, 124, 62, 74, 77),
    ("F", 16): (108, 121, 125, 63, 75, 78),
    ("F", 17): (108, 122, 126, 64, 76, 79),
}


def classify_paediatric_bp(
    systolic: Optional[int],
    diastolic: Optional[int] = None,
    age_years: Optional[float] = None,
    height_cm: Optional[float] = None,
    sex: Optional[str] = None,
) -> Dict[str, Any]:
    """Paediatric BP staging using AAP CPG 2017 / IAP tables.

    Stages:
      Normal       : sys < p90 AND dia < p90
      Elevated     : sys p90–p95 OR sys ≥ 120 (whichever lower) — no dia criteria
      Stage 1 HTN  : sys p95–p99+5 OR dia p95–p99+5
      Stage 2 HTN  : sys > p99+5   OR dia > p99+5

    Returns dict with: stage, alert_level, is_high_risk, description,
                       reference_p90_sys, reference_p95_sys, age_used, sex_used.
    """
    UNKNOWN = {
        "stage": "Unknown", "alert_level": "none", "is_high_risk": False,
        "description": "Insufficient data for paediatric BP classification.",
        "reference_p90_sys": None, "reference_p95_sys": None,
        "age_used": None, "sex_used": None,
    }

    if systolic is None or age_years is None:
        return UNKNOWN

    try:
        sys_v = int(systolic)
        dia_v = int(diastolic) if diastolic is not None else None
        age_f = float(age_years)
    except (ValueError, TypeError):
        return UNKNOWN

    age_int = min(17, max(1, round(age_f)))

    sex_norm = "M"
    if sex:
        s = str(sex).strip().upper()
        if s[0] in ("F", "G", "W"):
            sex_norm = "F"

    row = _BP_50TH_HT.get((sex_norm, age_int))
    if row is None:
        return {**UNKNOWN, "description": f"No BP reference for sex={sex_norm}, age={age_int}."}

    p50_sys, p90_sys, p95_sys, p50_dia, p90_dia, p95_dia = row
    # p99 approximation: p95 + 12 mmHg systolic (AAP CPG 2017 rule)
    p99_sys = p95_sys + 12
    p99_dia = p95_dia + 12

    label_suffix = f" (age {age_int}y {sex_norm}, ref p90/p95 sys {p90_sys}/{p95_sys} mmHg)"

    # Stage 2: sys > p99+5 OR dia > p99+5
    if sys_v > p99_sys + 5 or (dia_v is not None and dia_v > p99_dia + 5):
        return {
            "stage": "Stage 2 HTN",
            "alert_level": "critical",
            "is_high_risk": True,
            "description": f"🚨 Stage 2 HTN{label_suffix}. Immediate evaluation required.",
            "reference_p90_sys": p90_sys,
            "reference_p95_sys": p95_sys,
            "age_used": age_int,
            "sex_used": sex_norm,
        }

    # Stage 1: sys p95–p99+5 OR dia p95–p99+5
    if sys_v >= p95_sys or (dia_v is not None and dia_v >= p95_dia):
        return {
            "stage": "Stage 1 HTN",
            "alert_level": "high",
            "is_high_risk": True,
            "description": f"⚠️ Stage 1 HTN{label_suffix}. Physician review recommended.",
            "reference_p90_sys": p90_sys,
            "reference_p95_sys": p95_sys,
            "age_used": age_int,
            "sex_used": sex_norm,
        }

    # Elevated: sys p90–p95 OR sys ≥ 120 (whichever is lower threshold)
    elevated_threshold = min(p90_sys, 120)
    if sys_v >= elevated_threshold or (dia_v is not None and dia_v >= p90_dia):
        return {
            "stage": "Elevated BP",
            "alert_level": "advisory",
            "is_high_risk": False,
            "description": f"Elevated BP{label_suffix}. Repeat measurement recommended.",
            "reference_p90_sys": p90_sys,
            "reference_p95_sys": p95_sys,
            "age_used": age_int,
            "sex_used": sex_norm,
        }

    return {
        "stage": "Normal",
        "alert_level": "normal",
        "is_high_risk": False,
        "description": f"Normal BP{label_suffix}.",
        "reference_p90_sys": p90_sys,
        "reference_p95_sys": p95_sys,
        "age_used": age_int,
        "sex_used": sex_norm,
    }


# ---------------------------------------------------------------------------
# 3.  Adams Forward Bend Test (ATR) Alert
# ---------------------------------------------------------------------------

def classify_atr(atr_degrees: Optional[Any]) -> Dict[str, Any]:
    """Adams Forward Bend Test scoliosis screening alert.

    ATR < 5°  : Within normal variation — no referral.
    ATR 5–9°  : Positive screen — orthopedic review recommended.
    ATR ≥ 10° : High suspicion — urgent orthopedic referral.

    Reference: Scoliosis Research Society (SRS) 2019, IAP Paediatric Orthopaedics
               recommendation for school screening programmes.

    Args:
        atr_degrees: Angle of trunk rotation in degrees (numeric or string).

    Returns:
        dict with: atr_value, alert_triggered, alert_level, is_high_risk, description.
    """
    NEGATIVE = {
        "atr_value": None, "alert_triggered": False, "alert_level": "normal",
        "is_high_risk": False,
        "description": "ATR value not recorded or not applicable.",
    }

    if atr_degrees is None:
        return NEGATIVE

    try:
        atr = float(re.findall(r"[\d.]+", str(atr_degrees))[0])
    except (IndexError, ValueError, TypeError):
        return NEGATIVE

    if atr < 5.0:
        return {
            "atr_value": round(atr, 1),
            "alert_triggered": False,
            "alert_level": "normal",
            "is_high_risk": False,
            "description": f"ATR {atr:.1f}° — within normal range (< 5°).",
        }
    elif atr < 10.0:
        return {
            "atr_value": round(atr, 1),
            "alert_triggered": True,
            "alert_level": "advisory",
            "is_high_risk": False,
            "description": (
                f"⚠️ ATR {atr:.1f}° ≥ 5° — Positive Adams FBT screen. "
                "Orthopedic review recommended for paediatric scoliosis."
            ),
        }
    else:
        return {
            "atr_value": round(atr, 1),
            "alert_triggered": True,
            "alert_level": "critical",
            "is_high_risk": True,
            "description": (
                f"🚨 ATR {atr:.1f}° ≥ 10° — High suspicion for structural scoliosis. "
                "Urgent orthopedic referral indicated (SRS / IAP guideline)."
            ),
        }


# ---------------------------------------------------------------------------
# 4.  Unified Paediatric Dispatcher
# ---------------------------------------------------------------------------

def _parse_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        digits = re.findall(r"[\d.]+", str(val))
        return float(digits[0]) if digits else None
    except (ValueError, IndexError):
        return None


def _parse_int(val: Any) -> Optional[int]:
    v = _parse_float(val)
    return int(v) if v is not None else None


def _parse_bp(bp_str: Any) -> Tuple[Optional[int], Optional[int]]:
    """Extract (systolic, diastolic) from '120/80' formatted string."""
    if bp_str is None:
        return None, None
    parts = re.findall(r"\d+", str(bp_str))
    if len(parts) >= 2:
        return int(parts[0]), int(parts[1])
    if len(parts) == 1:
        return int(parts[0]), None
    return None, None


def _parse_sex(sex_val: Any) -> Optional[str]:
    if not sex_val:
        return None
    s = str(sex_val).strip().upper()
    if not s:
        return None
    return "F" if s[0] in ("F", "G", "W") else "M"


def classify_paediatric(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Unified paediatric screening dispatcher.

    Accepts the same `fields` dict used by apply_server_side_flags:
    each value is either [value, confidence] or {"value": ..., "confidence": ...}.

    Returns a dict suitable for merging into the server-side flags result:
    {
        "paediatric_bmi_classification": {...},
        "paediatric_bp_classification":  {...},
        "atr_classification":            {...},
        "is_paediatric":                 bool,
        "paediatric_alerts":             [str, ...],   # human-readable alert list
    }
    """

    def _get(key: str) -> Any:
        item = fields.get(key)
        if item is None:
            return None
        if isinstance(item, list) and len(item) >= 1:
            return item[0]
        if isinstance(item, dict):
            return item.get("value")
        return item

    # Core demographics
    age_raw = _get("age") or _get("age_years")
    sex_raw = _get("gender") or _get("sex")
    weight_raw = _get("weight_kg") or _get("weight")
    height_raw = _get("height_cm") or _get("height")
    bp_raw = _get("bp") or _get("blood_pressure") or _get("blood_pressure_mmhg")
    atr_raw = _get("atr") or _get("atr_degrees") or _get("trunk_rotation")

    age_f = _parse_float(age_raw)
    sex_n = _parse_sex(sex_raw)
    wt_f = _parse_float(weight_raw)
    ht_f = _parse_float(height_raw)
    sys_i, dia_i = _parse_bp(bp_raw)

    # Only classify as paediatric if age < 18
    is_paediatric = (age_f is not None and age_f < 18.0)

    # BMI
    bmi_val: Optional[float] = None
    if wt_f and ht_f and ht_f > 30:
        ht_m = ht_f / 100.0
        bmi_val = round(wt_f / (ht_m ** 2), 1)

    if is_paediatric:
        bmi_cls = classify_paediatric_bmi(bmi_val, age_f, sex_n)
        bp_cls = classify_paediatric_bp(sys_i, dia_i, age_f, ht_f, sex_n)
    else:
        bmi_cls = {
            "centile": None, "category": "Not Applicable", "alert_level": "none",
            "is_high_risk": False, "staging": "Use adult BMI classification for age ≥ 18.",
            "age_used": None, "sex_used": None, "note": "Patient is ≥ 18 years."
        }
        bp_cls = {
            "stage": "Not Applicable", "alert_level": "none", "is_high_risk": False,
            "description": "Use adult BP classification for age ≥ 18.",
            "reference_p90_sys": None, "reference_p95_sys": None,
            "age_used": None, "sex_used": None,
        }

    atr_cls = classify_atr(atr_raw)

    # Aggregate human-readable alerts
    alerts: List[str] = []
    if is_paediatric:
        bmi_al = bmi_cls.get("alert_level", "none")
        if bmi_al in ("advisory", "high", "critical", "warning"):
            alerts.append(f"BMI Alert [{bmi_al.upper()}]: {bmi_cls.get('category')} — {bmi_cls.get('staging')}")
        bp_stage = bp_cls.get("stage", "Unknown")
        if bp_stage not in ("Normal", "Unknown", "Not Applicable"):
            alerts.append(f"BP Alert [{bp_cls.get('alert_level', '').upper()}]: {bp_cls.get('description', '')}")
    if atr_cls.get("alert_triggered"):
        alerts.append(f"Scoliosis Screen [{atr_cls.get('alert_level', '').upper()}]: {atr_cls.get('description', '')}")

    return {
        "paediatric_bmi_classification": bmi_cls,
        "paediatric_bp_classification": bp_cls,
        "atr_classification": atr_cls,
        "paediatric_calculated_bmi": bmi_val,
        "is_paediatric": is_paediatric,
        "paediatric_alerts": alerts,
    }
