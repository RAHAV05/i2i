"""Google Gemini Vision AI Provider (Unified Pipeline Adapter).

Delegates to the Single Unified AI Pipeline for image preprocessing,
standardized prompt dispatch, schema sanitization, and model escalation.
"""

import os
from typing import Dict, Any, Optional, Tuple
from .unified_pipeline import UnifiedAIPipeline, preprocess_clinical_image as preprocess_image

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_FALLBACK_MODEL = "gemini-3.1-pro-preview"


class GeminiService(UnifiedAIPipeline):
    """Gemini Vision document extractor powered by the Unified Pipeline."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        fallback_model: Optional[str] = None
    ):
        key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
        m = model if (model is not None and model.strip()) else os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        fb = fallback_model if (fallback_model is not None and fallback_model.strip()) else os.getenv("GEMINI_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)
        super().__init__(
            api_key=key,
            provider="gemini",
            model=m,
            fallback_model=fb
        )

    _call_model = UnifiedAIPipeline._call_model

