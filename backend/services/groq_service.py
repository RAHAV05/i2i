"""Groq Vision AI Provider (Unified Pipeline Adapter).

Delegates to the Single Unified AI Pipeline for image preprocessing,
standardized prompt dispatch, schema sanitization, and model escalation.
"""

import os
from typing import Dict, Any, Optional
from .unified_pipeline import UnifiedAIPipeline, preprocess_clinical_image

DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"


class GroqService(UnifiedAIPipeline):
    """Groq Vision document extractor powered by the Unified Pipeline."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None
    ):
        key = api_key if api_key is not None else os.getenv("GROQ_API_KEY", "")
        m = model if (model is not None and model.strip()) else os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL)
        super().__init__(
            api_key=key,
            provider="groq",
            model=m
        )
