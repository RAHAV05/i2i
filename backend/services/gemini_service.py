"""Google Gemini Vision AI Provider Implementation.

Uses Google's Gemini models:
- Default: gemini-3.6-flash (configurable via GEMINI_MODEL)
- Escalation Fallback: gemini-3.1-pro-preview (configurable via GEMINI_FALLBACK_MODEL)
- Thinking level: low (configurable via GEMINI_THINKING_LEVEL)
Performs image preprocessing (1,568 px max edge, JPEG quality 80),
extracts clinical form data from student_form and health_assessment sheets,
calculates token usage and INR cost, and escalates to fallback model when required.
"""

import os
import io
import time
import json
import logging
from typing import Dict, Any, Optional, Tuple
from PIL import Image

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

from .ai_service import (
    AIService,
    SYSTEM_EXTRACTION_PROMPT,
    validate_and_sanitize_extraction,
    should_escalate_record,
    apply_server_side_flags
)

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_FALLBACK_MODEL = "gemini-3.1-pro-preview"
DEFAULT_THINKING_LEVEL = "low"
DEFAULT_MAX_OUTPUT_TOKENS = 4000
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RETRIES = 2

# List pricing in USD per 1M tokens
DEFAULT_PRICE_IN_USD_PER_M = 1.50
DEFAULT_PRICE_OUT_USD_PER_M = 7.50
DEFAULT_FALLBACK_PRICE_IN_USD_PER_M = 2.00
DEFAULT_FALLBACK_PRICE_OUT_USD_PER_M = 12.00
DEFAULT_USD_INR = 97.0

# Thresholds
FLAG_CONFIDENCE = float(os.getenv("FLAG_CONFIDENCE", "0.8"))
FLAG_CONFIDENCE_MEASURE = float(os.getenv("FLAG_CONFIDENCE_MEASURE", "0.9"))
IMAGE_MAX_EDGE_PX = int(os.getenv("IMAGE_MAX_EDGE_PX", "1568"))


def preprocess_image(img_bytes: bytes, max_edge: int = IMAGE_MAX_EDGE_PX, quality: int = 80) -> bytes:
    """Resize image so its longest edge is at most max_edge px, compressed as JPEG quality 80."""
    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            if im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            w, h = im.size
            if max(w, h) > max_edge:
                if w >= h:
                    new_w = max_edge
                    new_h = max(1, int(h * (max_edge / float(w))))
                else:
                    new_h = max_edge
                    new_w = max(1, int(w * (max_edge / float(h))))
                im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue()
    except Exception as e:
        logger.warning("Image preprocessing failed, using original bytes: %s", e)
        return img_bytes


class GeminiService(AIService):
    """Hardened Google Gemini Vision document extractor with model escalation and cost metering."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        fallback_model: Optional[str] = None
    ):
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        configured_model = os.getenv("GEMINI_MODEL", "").strip()
        self.model = (model or configured_model or DEFAULT_GEMINI_MODEL).strip()
        
        configured_fallback = os.getenv("GEMINI_FALLBACK_MODEL", "").strip()
        self.fallback_model = (fallback_model or configured_fallback or DEFAULT_FALLBACK_MODEL).strip()

        self.thinking_level = os.getenv("GEMINI_THINKING_LEVEL", DEFAULT_THINKING_LEVEL).strip()
        self.max_output_tokens = int(os.getenv("AI_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS)))
        self.timeout_seconds = int(os.getenv("AI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
        self.max_retries = int(os.getenv("AI_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)))

        self.price_in = float(os.getenv("PRICE_IN_USD_PER_M", str(DEFAULT_PRICE_IN_USD_PER_M)))
        self.price_out = float(os.getenv("PRICE_OUT_USD_PER_M", str(DEFAULT_PRICE_OUT_USD_PER_M)))
        self.fallback_price_in = float(os.getenv("FALLBACK_PRICE_IN_USD_PER_M", str(DEFAULT_FALLBACK_PRICE_IN_USD_PER_M)))
        self.fallback_price_out = float(os.getenv("FALLBACK_PRICE_OUT_USD_PER_M", str(DEFAULT_FALLBACK_PRICE_OUT_USD_PER_M)))
        self.usd_inr = float(os.getenv("USD_INR", str(DEFAULT_USD_INR)))

        self._client = None
        if GENAI_AVAILABLE and self.api_key:
            try:
                self._client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.warning("Failed to initialize genai.Client: %s", e)

    def is_configured(self) -> bool:
        """Check whether a non-empty Gemini API key is present."""
        return bool(self.api_key and len(self.api_key) > 10)

    def provider_name(self) -> str:
        return f"Google Gemini ({self.model})"

    def calculate_cost_rs(self, tokens_in: int, tokens_out: int, tokens_think: int, is_fallback: bool = False) -> float:
        """Calculate call cost in INR based on token usage and model prices."""
        p_in = self.fallback_price_in if is_fallback else self.price_in
        p_out = self.fallback_price_out if is_fallback else self.price_out
        # In Gemini 3 thinking tokens are billed as output tokens
        total_out = tokens_out + tokens_think
        cost_usd = (tokens_in * p_in + total_out * p_out) / 1_000_000.0
        return round(cost_usd * self.usd_inr, 4)

    def _should_escalate(self, sanitized_data: Dict[str, Any]) -> Tuple[bool, str]:
        """Determine if a record should be escalated to the fallback model.
        Uses exact rule: count ONLY low-confidence fields (< FLAG_CONFIDENCE),
        never blank fields with confidence 1. Escalate if 3+ low-confidence,
        or any critical measurement is low-confidence or implausible, or invalid schema.
        """
        schema_valid = sanitized_data.get("schema_valid", True)
        fields = sanitized_data.get("fields")
        flag_details = sanitized_data.get("flag_details")

        if not flag_details:
            if fields:
                flag_details = apply_server_side_flags(fields)
            else:
                temp_fields = {}
                for k, v in sanitized_data.items():
                    if k in ("uncertain_fields", "missing_fields", "schema_valid", "flag_details", "flags"):
                        continue
                    temp_fields[k] = [v, 1.0]
                # Any field in uncertain_fields gets low confidence (0.50 < FLAG_CONFIDENCE)
                for uf in sanitized_data.get("uncertain_fields", []):
                    temp_fields[uf] = [temp_fields.get(uf, [None, 0.50])[0], 0.50]
                    if uf in ("blood_pressure", "blood_pressure_mmhg"):
                        temp_fields["bp"] = [temp_fields.get("bp", [temp_fields[uf][0], 0.50])[0], 0.50]
                    elif uf in ("pulse_bpm", "pulse"):
                        temp_fields["pulse"] = [temp_fields.get("pulse", [temp_fields[uf][0], 0.50])[0], 0.50]
                    elif uf in ("weight", "weight_kg"):
                        temp_fields["weight_kg"] = [temp_fields.get("weight_kg", [temp_fields[uf][0], 0.50])[0], 0.50]
                    elif uf in ("height", "height_cm"):
                        temp_fields["height_cm"] = [temp_fields.get("height_cm", [temp_fields[uf][0], 0.50])[0], 0.50]
                    elif uf in ("rbs", "random_blood_sugar_mgdl"):
                        temp_fields["rbs"] = [temp_fields.get("rbs", [temp_fields[uf][0], 0.50])[0], 0.50]
                fields = temp_fields
                flag_details = apply_server_side_flags(temp_fields)

        return should_escalate_record(schema_valid, fields or {}, flag_details)

    def _call_model(
        self,
        model_name: str,
        student_form_bytes: bytes,
        health_assessment_bytes: bytes,
        is_fallback: bool = False
    ) -> Dict[str, Any]:
        """Execute a single model extraction with retry and token tracking."""
        start_time = time.time()
        prompt_text = (
            SYSTEM_EXTRACTION_PROMPT + "\n\n"
            "You are reading two photographed sheets of one person's medical camp record:\n"
            "- Image 1: student_form (filled by student or staff)\n"
            "- Image 2: health_assessment (filled by doctor)\n"
            "Extract printed text, handwriting, and ticked checkboxes. Return ONLY valid JSON."
        )

        last_error = None
        for attempt in range(1 + self.max_retries):
            try:
                if self._client:
                    cfg_kwargs: Dict[str, Any] = {
                        "temperature": 0.0,
                        "max_output_tokens": self.max_output_tokens,
                        "response_mime_type": "application/json",
                    }
                    if self.thinking_level:
                        try:
                            cfg_kwargs["thinking_config"] = types.ThinkingConfig(
                                thinking_level=self.thinking_level
                            )
                        except Exception as e:
                            logger.debug("ThinkingConfig could not be attached: %s", e)

                    config = types.GenerateContentConfig(**cfg_kwargs)
                    response = self._client.models.generate_content(
                        model=model_name,
                        contents=[
                            prompt_text,
                            types.Part.from_bytes(data=student_form_bytes, mime_type="image/jpeg"),
                            types.Part.from_bytes(data=health_assessment_bytes, mime_type="image/jpeg"),
                        ],
                        config=config
                    )

                    duration = round(time.time() - start_time, 2)
                    raw_text = response.text or ""

                    # Usage metadata
                    u = getattr(response, "usage_metadata", None)
                    tokens_in = getattr(u, "prompt_token_count", 0) or 0
                    tokens_out = getattr(u, "candidates_token_count", 0) or 0
                    tokens_think = getattr(u, "thoughts_token_count", 0) or 0
                    cost_rs = self.calculate_cost_rs(tokens_in, tokens_out, tokens_think, is_fallback=is_fallback)

                    sanitized = validate_and_sanitize_extraction(raw_text)
                    return {
                        "success": True,
                        "data": sanitized,
                        **sanitized,
                        "model": model_name,
                        "is_fallback": is_fallback,
                        "usage": {
                            "model": model_name,
                            "tokens_in": tokens_in,
                            "tokens_out": tokens_out,
                            "tokens_think": tokens_think,
                            "seconds": duration,
                            "cost_rs": cost_rs,
                            "attempt": attempt + 1
                        }
                    }
                else:
                    raise RuntimeError("genai.Client is not available or not configured")

            except Exception as e:
                last_error = e
                logger.warning(
                    "Extraction call to %s failed (attempt %d/%d): %s",
                    model_name, attempt + 1, 1 + self.max_retries, e
                )
                if attempt < self.max_retries:
                    time.sleep(1.0 * (attempt + 1))

        duration = round(time.time() - start_time, 2)
        err_msg = str(last_error)
        code = "API_ERROR"
        if "404" in err_msg or "not found" in err_msg.lower():
            code = "MODEL_NOT_FOUND"
        elif "429" in err_msg or "quota" in err_msg.lower():
            code = "RATE_LIMIT"
        elif "401" in err_msg or "403" in err_msg:
            code = "AUTH_ERROR"

        return {
            "success": False,
            "error": f"Failed extraction with model '{model_name}': {err_msg}",
            "code": code,
            "model": model_name,
            "manual_entry_allowed": True,
            "usage": {
                "model": model_name,
                "tokens_in": 0,
                "tokens_out": 0,
                "tokens_think": 0,
                "seconds": duration,
                "cost_rs": 0.0,
                "attempt": self.max_retries + 1
            }
        }

    def extract_from_images(
        self,
        student_form_bytes: bytes,
        student_form_mime: str = "image/jpeg",
        health_assessment_bytes: bytes = b"",
        health_assessment_mime: str = "image/jpeg",
        # Backward-compatible parameter aliases
        page1_bytes: Optional[bytes] = None,
        page1_mime: Optional[str] = None,
        page2_bytes: Optional[bytes] = None,
        page2_mime: Optional[str] = None
    ) -> Dict[str, Any]:
        """Preprocesses images, runs primary extraction with gemini-3.6-flash, and escalates if needed.
        Accepts sheet names student_form and health_assessment everywhere.
        """
        sf_bytes = page1_bytes if page1_bytes is not None else student_form_bytes
        ha_bytes = page2_bytes if page2_bytes is not None else health_assessment_bytes
        if not self.is_configured():
            return {
                "success": False,
                "error": "Gemini API key is not configured. Please set GEMINI_API_KEY in backend/.env to enable automated AI document transcription.",
                "code": "AI_NOT_CONFIGURED",
                "manual_entry_allowed": True
            }

        # Step 1: Image prep - resize longest edge to 1,568 px at JPEG quality 80
        p1_proc = preprocess_image(sf_bytes, max_edge=IMAGE_MAX_EDGE_PX, quality=80)
        p2_proc = preprocess_image(ha_bytes, max_edge=IMAGE_MAX_EDGE_PX, quality=80)

        # Step 2: Call primary model (gemini-3.6-flash)
        primary_res = self._call_model(self.model, p1_proc, p2_proc, is_fallback=False)

        # Step 3: Check for escalation requirement
        if not primary_res.get("success"):
            # If primary model failed (invalid JSON or error), escalate to fallback model
            logger.info("Primary model %s failed; escalating to fallback model %s", self.model, self.fallback_model)
            fallback_res = self._call_model(self.fallback_model, p1_proc, p2_proc, is_fallback=True)
            if fallback_res.get("success"):
                fallback_res["escalated"] = True
                fallback_res["escalation_reason"] = f"Primary model failed: {primary_res.get('error')}"
                fallback_res["calls"] = [primary_res.get("usage"), fallback_res.get("usage")]
                fallback_res["provider"] = f"Google Gemini Escalated ({self.fallback_model})"
                return fallback_res
            return primary_res

        # If primary returned success, check clinical flag triggers
        sanitized_data = primary_res.get("data", {})
        should_escalate, reason = self._should_escalate(sanitized_data)

        if should_escalate and self.fallback_model != self.model:
            logger.info("Escalating to fallback model %s due to: %s", self.fallback_model, reason)
            fallback_res = self._call_model(self.fallback_model, p1_proc, p2_proc, is_fallback=True)
            if fallback_res.get("success"):
                fallback_res["escalated"] = True
                fallback_res["escalation_reason"] = reason
                fallback_res["calls"] = [primary_res.get("usage"), fallback_res.get("usage")]
                fallback_res["provider"] = f"Google Gemini Escalated ({self.fallback_model})"
                return fallback_res

        primary_res["provider"] = self.provider_name()
        primary_res["calls"] = [primary_res.get("usage")]
        return primary_res
