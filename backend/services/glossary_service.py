"""Clinical Glossary and Medical Abbreviation Disambiguation Service (Phase 3).

Implements Appendix B of the Ink 2 Insight Master Plan:
1. Field-scoped expansion for doctor short forms (148 forms across 8 groups).
2. Controlled medical terminology fuzzy matching (277 terms across 15 clinical topics).
3. Context-aware disambiguation:
   - 'OD' in medication -> 'Once Daily'; in vision -> 'Right Eye (Oculus Dexter)'.
   - 'OS' in medication -> 'Left Eye (Oculus Sinister)'.
   - 'RE' in vision -> 'Right Eye' (or 'Refractive Error' with diopter context).
   - 'Ref' -> 'Referred'; 'R/V' -> 'Review Advised'.
4. Client Safety Invariants:
   - Never invent a diagnosis from a single measurement.
   - Numbers are preserved exactly as written.
   - Unrecognized abbreviations are kept raw and flagged 'new_term'.
"""

import os
import re
import json
import difflib
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("i2i.glossary")

GLOSSARY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "clinical_glossary.json"
)

# Field-scoped explicit overrides
SCOPED_DISAMBIGUATION = {
    "medications": {
        "od": "Once Daily (omni die)",
        "o.d": "Once Daily (omni die)",
        "bd": "Twice Daily (bis die)",
        "b.d": "Twice Daily (bis die)",
        "bid": "Twice Daily (bis in die)",
        "tds": "Three Times Daily",
        "t.d.s": "Three Times Daily",
        "tid": "Three Times Daily",
        "qid": "Four Times Daily",
        "sos": "As Needed (si opus sit)",
        "s.o.s": "As Needed (si opus sit)",
        "stat": "Immediately (statim)",
        "hs": "At Bedtime (hora somni)",
        "h.s": "At Bedtime (hora somni)",
        "pr": "Per Rectum",
        "po": "By Mouth (per os)",
        "tab": "Tablet",
        "cap": "Capsule",
        "syr": "Syrup",
        "inj": "Injection"
    },
    "vision": {
        "od": "Right Eye (Oculus Dexter)",
        "o.d": "Right Eye (Oculus Dexter)",
        "os": "Left Eye (Oculus Sinister)",
        "o.s": "Left Eye (Oculus Sinister)",
        "ou": "Both Eyes (Oculus Uterque)",
        "o.u": "Both Eyes (Oculus Uterque)",
        "re": "Right Eye",
        "r.e": "Right Eye",
        "le": "Left Eye",
        "l.e": "Left Eye",
        "va": "Visual Acuity",
        "dva": "Distant Visual Acuity",
        "nva": "Near Visual Acuity",
        "cf": "Counting Fingers",
        "hm": "Hand Movements",
        "pl": "Perception of Light"
    },
    "vitals": {
        "bp": "Blood Pressure",
        "b.p": "Blood Pressure",
        "pr": "Pulse Rate",
        "p.r": "Pulse Rate",
        "hr": "Heart Rate",
        "rr": "Respiratory Rate",
        "wt": "Weight",
        "ht": "Height",
        "bmi": "Body Mass Index",
        "wc": "Waist Circumference",
        "cbg": "Capillary Blood Glucose",
        "rbs": "Random Blood Sugar",
        "fbs": "Fasting Blood Sugar",
        "t": "Temperature",
        "temp": "Temperature"
    },
    "examination": {
        "nad": "No Abnormality Detected",
        "n.a.d": "No Abnormality Detected",
        "wnl": "Within Normal Limits",
        "w.n.l": "Within Normal Limits",
        "rs": "Respiratory System",
        "cvs": "Cardiovascular System",
        "cns": "Central Nervous System",
        "pa": "Per Abdomen",
        "p/a": "Per Abdomen",
        "ent": "Ear, Nose and Throat",
        "nv": "Normal Vesicular Breath Sounds",
        "s1s2": "Normal Heart Sounds (S1, S2 Present)",
        "s1 s2": "Normal Heart Sounds (S1, S2 Present)",
        "s1, s2": "Normal Heart Sounds (S1, S2 Present)"
    },
    "referral": {
        "ref": "Referred to Specialist",
        "refer": "Referred to Specialist",
        "r/v": "Review Advised",
        "rv": "Review Advised",
        "f/u": "Follow-Up Advised",
        "fu": "Follow-Up Advised",
        "sos": "Review SOS if symptoms worsen"
    }
}


class ClinicalGlossaryService:
    """Provides field-scoped abbreviation expansion and controlled medical vocabulary normalization."""

    def __init__(self, glossary_path: str = GLOSSARY_PATH):
        self.glossary_path = glossary_path
        self.version = "1.0.0"
        self.short_forms: Dict[str, Dict[str, Any]] = {}
        self.controlled_terms: List[Dict[str, Any]] = []
        self._term_lookup: Dict[str, str] = {}
        self.load_glossary()

    def load_glossary(self) -> None:
        """Load parsed clinical glossary JSON."""
        if not os.path.exists(self.glossary_path):
            logger.warning("Glossary file %s not found. Using defaults.", self.glossary_path)
            return

        with open(self.glossary_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.version = data.get("version", "1.0.0")
        self.short_forms = data.get("short_forms", {})
        self.controlled_terms = data.get("controlled_terms", [])

        # Build lowercase fast lookup for controlled terms
        for item in self.controlled_terms:
            t = item.get("term", "").strip()
            if t:
                self._term_lookup[t.lower()] = t

        logger.info(
            "Clinical Glossary loaded: %d short forms, %d controlled terms (version %s).",
            len(self.short_forms), len(self.controlled_terms), self.version
        )

    def normalize_text(
        self,
        raw_text: Optional[str],
        scope: Optional[str] = None,
        field_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Normalize a clinical text entry using field-scoped rules and controlled vocabularies.
        
        Returns:
            {
                'raw_text': str,
                'normalized': str,
                'is_expanded': bool,
                'scope_used': str or None,
                'rule_applied': str,
                'status': 'exact' | 'scoped_expansion' | 'general_expansion' | 'fuzzy_match' | 'new_term'
            }
        """
        raw = str(raw_text or "").strip()
        if not raw:
            return {
                "raw_text": "",
                "normalized": "",
                "is_expanded": False,
                "scope_used": scope,
                "rule_applied": "empty",
                "status": "exact"
            }

        # Invariant: Numbers and dates should not be altered by glossary
        if re.match(r'^\d+(\.\d+)?(\s*(cm|kg|mmhg|bpm|mg/dl|years|yrs|%))?$', raw, re.IGNORECASE):
            return {
                "raw_text": raw,
                "normalized": raw,
                "is_expanded": False,
                "scope_used": scope,
                "rule_applied": "number_preserved",
                "status": "exact"
            }

        raw_lower = raw.lower().strip()

        # 1. Field-scoped expansion (Highest Priority)
        # Prevents 'OD' in meds from becoming 'Right Eye'
        if scope and scope in SCOPED_DISAMBIGUATION:
            scope_map = SCOPED_DISAMBIGUATION[scope]
            if raw_lower in scope_map:
                return {
                    "raw_text": raw,
                    "normalized": scope_map[raw_lower],
                    "is_expanded": True,
                    "scope_used": scope,
                    "rule_applied": f"scoped_{scope}_{raw_lower}",
                    "status": "scoped_expansion"
                }

        # 2. General Doctor Short Forms Lookup
        # Exact match in short forms table
        clean_token = re.sub(r'[^a-z0-9]', '', raw_lower)
        for abbrev, entry in self.short_forms.items():
            abbrev_clean = re.sub(r'[^a-z0-9]', '', abbrev.lower())
            if abbrev_clean and clean_token == abbrev_clean:
                return {
                    "raw_text": raw,
                    "normalized": entry["expansion"],
                    "is_expanded": True,
                    "scope_used": entry.get("group"),
                    "rule_applied": f"short_form_{abbrev}",
                    "status": "general_expansion"
                }

        # 3. Exact match against Controlled Medical Terminology
        if raw_lower in self._term_lookup:
            return {
                "raw_text": raw,
                "normalized": self._term_lookup[raw_lower],
                "is_expanded": False,
                "scope_used": scope,
                "rule_applied": "controlled_exact",
                "status": "exact"
            }

        # 4. Fuzzy match against Controlled Medical Terminology
        # Tolerates minor OCR misspellings (e.g., 'otitis mediaa' -> 'Otitis media')
        matches = difflib.get_close_matches(raw_lower, self._term_lookup.keys(), n=1, cutoff=0.84)
        if matches:
            canonical = self._term_lookup[matches[0]]
            return {
                "raw_text": raw,
                "normalized": canonical,
                "is_expanded": True,
                "scope_used": scope,
                "rule_applied": f"fuzzy_match_{matches[0]}",
                "status": "fuzzy_match"
            }

        # 5. Unknown / Ambiguous shorthand: Keep as written, flag 'new_term'
        return {
            "raw_text": raw,
            "normalized": raw,
            "is_expanded": False,
            "scope_used": scope,
            "rule_applied": "unrecognized_kept_as_written",
            "status": "new_term"
        }

    def expand_multiple_tokens(self, text: str, scope: Optional[str] = None) -> str:
        """Tokenize text and expand known abbreviations in multi-word doctor notes."""
        if not text:
            return ""
        tokens = re.split(r'(\s+|,|\.)', text)
        expanded_tokens = []
        for tok in tokens:
            stripped = tok.strip().lower()
            if not stripped:
                expanded_tokens.append(tok)
                continue
            norm = self.normalize_text(stripped, scope=scope)
            if norm["is_expanded"]:
                expanded_tokens.append(norm["normalized"])
            else:
                expanded_tokens.append(tok)
        return "".join(expanded_tokens)


# Global Singleton
default_glossary_service = ClinicalGlossaryService()
