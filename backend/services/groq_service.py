"""Groq Vision AI Provider Implementation.

Uses Groq's vision models (default: qwen/qwen3.8-27b or configurable via GROQ_MODEL)
to extract clinical form data from Page 1 and Page 2.
"""

import os
import base64
import logging
from typing import Dict, Any, Optional
import requests

from .ai_service import AIService, SYSTEM_EXTRACTION_PROMPT, validate_and_sanitize_extraction

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"

def _optimize_image_for_groq(image_bytes: bytes, max_dim: int = 700) -> tuple[bytes, str]:
    """Resize image to safe bounds for Groq vision token budget (7,000 ITPM limit)."""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(image_bytes))
        if max(im.size) > max_dim:
            im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=82)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        logger.warning("Could not optimize image via PIL: %s", e)
        return image_bytes, "image/jpeg"

class GroqService(AIService):
    """Groq Vision document extractor."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        if api_key is not None:
            self.api_key = api_key.strip()
        else:
            self.api_key = os.getenv("GROQ_API_KEY", "").strip()

        if model is not None and model.strip():
            self.model = model.strip()
        else:
            configured_model = os.getenv("GROQ_MODEL", "").strip()
            self.model = configured_model if configured_model else DEFAULT_GROQ_MODEL

    def is_configured(self) -> bool:
        """Check whether a non-empty API key is present."""
        return bool(self.api_key and len(self.api_key) > 8)

    def provider_name(self) -> str:
        return f"Groq Vision ({self.model})"

    def extract_from_images(
        self,
        page1_bytes: bytes,
        page1_mime: str,
        page2_bytes: bytes,
        page2_mime: str
    ) -> Dict[str, Any]:
        """Send images to Groq Chat Completions API with vision format."""
        if not self.is_configured():
            return {
                "success": False,
                "error": "Groq API key is not configured. Please set GROQ_API_KEY in backend/.env to enable automated AI document transcription.",
                "code": "AI_NOT_CONFIGURED",
                "manual_entry_allowed": True
            }

        p1_opt, p1_mime_opt = _optimize_image_for_groq(page1_bytes, max_dim=700)
        p2_opt, p2_mime_opt = _optimize_image_for_groq(page2_bytes, max_dim=700)

        p1_b64 = base64.b64encode(p1_opt).decode("utf-8")
        p2_b64 = base64.b64encode(p2_opt).decode("utf-8")

        p1_data_url = f"data:{p1_mime_opt};base64,{p1_b64}"
        p2_data_url = f"data:{p2_mime_opt};base64,{p2_b64}"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        user_content = [
            {
                "type": "text",
                "text": SYSTEM_EXTRACTION_PROMPT + "\n\nPlease extract data from these two scanned health camp form pages (Page 1 and Page 2):"
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": p1_data_url
                }
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": p2_data_url
                }
            }
        ]

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            "temperature": 0.05,
            "max_tokens": 2500,
            "response_format": {"type": "json_object"}
        }

        try:
            logger.info("Sending request to Groq vision model %s (payload sizes: p1=%d, p2=%d bytes)",
                        self.model, len(p1_opt), len(p2_opt))
            
            response = requests.post(
                GROQ_API_URL,
                headers=headers,
                json=payload,
                timeout=60
            )

            # If response_format json_object is not supported by a specific custom model, retry without it
            if response.status_code == 400 and "response_format" in response.text:
                logger.warning("Groq rejected response_format; retrying without it...")
                del payload["response_format"]
                response = requests.post(
                    GROQ_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=45
                )

            # Auto-retry on brief 429 rate limit
            if response.status_code == 429:
                import time
                logger.info("Groq 429 rate limit hit; waiting 4 seconds and retrying once...")
                time.sleep(4)
                response = requests.post(
                    GROQ_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=60
                )

            if response.status_code == 401:
                return {
                    "success": False,
                    "error": "Groq API authentication failed. Please check that GROQ_API_KEY is valid.",
                    "code": "AUTH_ERROR",
                    "manual_entry_allowed": True
                }

            if response.status_code == 429:
                try:
                    err_msg = response.json().get("error", {}).get("message", "")
                except Exception:
                    err_msg = ""
                return {
                    "success": False,
                    "error": f"Groq vision rate limit reached (7,000 ITPM limit). {err_msg or 'Please wait a few seconds before retrying or use manual entry.'}",
                    "code": "RATE_LIMIT",
                    "manual_entry_allowed": True
                }

            if response.status_code == 413:
                return {
                    "success": False,
                    "error": "Document image size or token limit exceeded for vision model. Please verify images.",
                    "code": "PAYLOAD_TOO_LARGE",
                    "manual_entry_allowed": True
                }

            if response.status_code != 200:
                err_snippet = response.text[:200]
                return {
                    "success": False,
                    "error": f"Groq API error ({response.status_code}): {err_snippet}",
                    "code": "API_ERROR",
                    "manual_entry_allowed": True
                }

            res_json = response.json()
            raw_content = res_json["choices"][0]["message"]["content"]
            sanitized_data = validate_and_sanitize_extraction(raw_content)

            return {
                "success": True,
                "data": sanitized_data,
                **sanitized_data,
                "provider": self.provider_name()
            }

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "error": "Extraction timed out after 45 seconds. The document can be filled manually.",
                "code": "TIMEOUT",
                "manual_entry_allowed": True
            }
        except requests.exceptions.RequestException as e:
            logger.error("Groq connection exception: %s", str(e))
            return {
                "success": False,
                "error": "Network connection error while contacting AI service. You may proceed with manual entry.",
                "code": "NETWORK_ERROR",
                "manual_entry_allowed": True
            }
        except Exception as e:
            logger.error("Extraction parsing error: %s", str(e))
            return {
                "success": False,
                "error": f"Failed to parse extraction results: {str(e)}. You can fill the fields manually.",
                "code": "PARSE_ERROR",
                "manual_entry_allowed": True
            }
