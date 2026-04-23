"""
Numerai pipeline configuration.

No side effects at import time. All values read from environment at call time.
HK-6: NUMERAI_SECRET_KEY must never appear in logs — only boolean presence is logged.
HK-4: VERSION is pinned in config, not in individual module code.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Dataset version — update here when Numerai releases v5.x+1.
# Surface a warning in download.py when this changes from what is cached.
DATASET_VERSION: str = "v5.2"

# Feature set to use for training baseline (small | medium | all).
# "small" for fast CI; "medium" for production baseline per HK-14 (OOM guard).
DEFAULT_FEATURE_SET: str = "medium"

# Tournament target column — re-verify against live docs each season (§14).
TARGET_COLUMN: str = "target_cyrus20"  # Ender20-family; confirm via features.json

# Era split defaults (§7 rule: holdout >= 4 eras, embargo >= 4 eras).
DEFAULT_HOLDOUT_ERAS: int = 4
DEFAULT_EMBARGO_ERAS: int = 4


@dataclass
class NumeraiConfig:
    """Runtime configuration; constructed from env at call time."""

    # Dataset
    version: str = DATASET_VERSION
    feature_set: str = DEFAULT_FEATURE_SET
    target_column: str = TARGET_COLUMN

    # Era split
    holdout_eras: int = DEFAULT_HOLDOUT_ERAS
    embargo_eras: int = DEFAULT_EMBARGO_ERAS

    # Storage — never /tmp (HK-2 / §7 rule 8).
    state_dir: Path = field(default_factory=lambda: _resolve_state_dir())

    # Auth
    public_id_configured: bool = field(default_factory=lambda: _check_public_id())
    secret_configured: bool = field(default_factory=lambda: _check_secret())

    # Gate flags
    allow_live: bool = field(default_factory=lambda: _allow_live())

    def data_dir(self) -> Path:
        p = self.state_dir / "numerai" / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def models_dir(self) -> Path:
        p = self.state_dir / "numerai" / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def submissions_dir(self) -> Path:
        p = self.state_dir / "numerai" / "submissions"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def logs_dir(self) -> Path:
        p = self.state_dir / "numerai" / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def status_dict(self) -> dict:
        """Safe status dict — no secret values, only boolean flags (HK-6)."""
        return {
            "dataset_version": self.version,
            "feature_set": self.feature_set,
            "target_column": self.target_column,
            "holdout_eras": self.holdout_eras,
            "embargo_eras": self.embargo_eras,
            "state_dir": str(self.state_dir),
            "public_id_configured": self.public_id_configured,
            "secret_configured": self.secret_configured,
            "allow_live": self.allow_live,
            "live_cadence": "Tuesday–Saturday",
            "scoring_lag_days": "~20 business days + 2 lag (20D2L)",
            "note_proxy_mmc": (
                "All local MMC metrics are proxy_mmc — not bit-identical to Numerai server "
                "(official: tie-kept rank → Gaussian → orthogonalize → covariance). "
                "See §15 / §25 of Numeroo Bot Blueprint."
            ),
            "note_bmx": (
                "BMC in diagnostics ≠ BMC on leaderboard (highest-stake vs stake-weighted benchmark)."
            ),
        }


def get_config() -> NumeraiConfig:
    """Construct config from environment. Call at runtime, not at import."""
    cfg = NumeraiConfig()
    logger.info(
        "Numerai config: version=%s feature_set=%s public_id=%s secret=%s allow_live=%s",
        cfg.version,
        cfg.feature_set,
        cfg.public_id_configured,
        cfg.secret_configured,
        cfg.allow_live,
    )
    return cfg


def _resolve_state_dir() -> Path:
    """Resolve ALGOCHAINS_STATE_DIR → state/ fallback. Never /tmp."""
    env_val = os.getenv("ALGOCHAINS_STATE_DIR", "")
    if env_val:
        return Path(env_val)
    # Fall back to mcp-server repo root / state
    repo_root = Path(__file__).resolve().parents[5]
    return repo_root / "state"


def _check_public_id() -> bool:
    return bool(os.getenv("NUMERAI_PUBLIC_ID", "").strip())


def _check_secret() -> bool:
    return bool(os.getenv("NUMERAI_SECRET_KEY", "").strip())


def _allow_live() -> bool:
    return os.getenv("NUMERAI_ALLOW_LIVE", "").strip() in ("1", "true", "yes")


def _get_napi():
    """
    Construct a NumerAPI client from env only.
    HK-6: never log the actual key values.
    Raises RuntimeError if keys not configured.
    """
    from numerapi import NumerAPI  # import deferred to avoid top-level dep

    public_id = os.getenv("NUMERAI_PUBLIC_ID", "").strip()
    secret_key = os.getenv("NUMERAI_SECRET_KEY", "").strip()

    if not public_id or not secret_key:
        raise RuntimeError(
            "NUMERAI_PUBLIC_ID and NUMERAI_SECRET_KEY must be set in environment. "
            "public_id_configured=%s secret_configured=%s"
            % (bool(public_id), bool(secret_key))
        )
    return NumerAPI(public_id=public_id, secret_key=secret_key)
