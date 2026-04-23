"""
Numerai Classic tournament pipeline for AlgoChains.

Pipeline: download → era_split → train → neutralize → validate → submit

RULES (non-negotiable, per §26.10 / §28.5):
- No code from this package may be imported into FUTURES_SCALPER*, CL_FUTURES_SCALPER*,
  or any live broker order path.
- models/numerai/ is the ONLY artifact namespace; never append feature_* columns to
  cl_feature_names.pkl or MNQ schemas.
- NUMERAI_SECRET_KEY must never appear in logs or MCP tool responses.
- Default = dry-run; uploads only when NUMERAI_ALLOW_LIVE=1 AND model_id is set.
"""

from .config import NumeraiConfig, get_config
from .era_utils import era_split, embargo_filter
from .download import download_training_data, download_live_data
from .neutralize import neutralize_predictions
from .validate import validate_metrics
from .submit import build_submission, upload_predictions_gated
from .train import train_baseline

__all__ = [
    "NumeraiConfig",
    "get_config",
    "era_split",
    "embargo_filter",
    "download_training_data",
    "download_live_data",
    "neutralize_predictions",
    "validate_metrics",
    "build_submission",
    "upload_predictions_gated",
    "train_baseline",
]
