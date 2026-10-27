"""NVIDIA NIM Vision AI Provider (Unified Pipeline Adapter).

Delegates to the Single Unified AI Pipeline for image preprocessing,
side-by-side stitching, standardized prompt dispatch, schema sanitization, and model escalation.
"""

import os
from typing import Dict, Any, Optional, Tuple
from .unified_pipeline import (
    UnifiedAIPipeline,
    preprocess_clinical_image as preprocess_image,
    stitch_pages_side_by_side as stitch_images
)

DEFAULT_NVIDIA_MODEL = "meta/llama-3.2-11b-vision-instruct"
DEFAULT_FALLBACK_MODEL = "meta/llama-3.2-90b-vision-instruct"


class NvidiaNimService(UnifiedAIPipeline):
    """NVIDIA NIM Vision document extractor powered by the Unified Pipeline."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        fallback_model: Optional[str] = None
    ):
        key = api_key if api_key is not None else os.getenv("NVIDIA_API_KEY", "")
        m = model if (model is not None and model.strip()) else os.getenv("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL)
        fb = fallback_model if (fallback_model is not None and fallback_model.strip()) else os.getenv("NVIDIA_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)
        super().__init__(
            api_key=key,
            provider="nvidia",
            model=m,
            fallback_model=fb
        )

