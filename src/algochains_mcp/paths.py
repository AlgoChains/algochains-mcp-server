"""
paths.py — Unified control-tower and heartbeat path resolution.

Before this module existed, `server.py` correctly honored the
ALGOCHAINS_CONTROL_TOWER env var, but six other modules
(`live_bot_intelligence/bot_ops.py`, `.../metrics_parser.py`,
`.../academic_registry.py`, `.../heartbeat.py`, `dashboard/live_dashboard.py`,
`skills_registry.py`) used hardcoded path lists that did NOT read the env var.

Effect on the desktop tower (Ubuntu/WSL at `/home/trrey/algochains-control-tower`):
  - `skills_registry` listed only Mac paths → found 0 skills
  - `dashboard/live_dashboard` used `~/CascadeProjects/algochains-control-tower`
    which does not exist on the desktop → empty dashboard
  - `heartbeat` looked for `/home/trrey/mac_heartbeat.json` while the Mac writes
    the heartbeat under `scripts/mac_heartbeat.json` → mismatch.

This module unifies resolution:

  1. ALGOCHAINS_CONTROL_TOWER env (preferred, matches server.py)
  2. ALGOCHAINS_CONTROL_TOWER_PATH env (legacy alias)
  3. First existing path in the legacy `_POSSIBLE_ROOTS` list (back-compat —
     preserves Mac behavior bit-for-bit; same selection function as before)
  4. __file__-relative sibling ``algochains-control-tower`` directory
  5. Hardcoded Mac fallback (original behavior)

Using this helper everywhere means the env var works on both hosts AND
the legacy path list keeps working on unmanaged installs where no env is set.
Callers that want to retain their *own* extra candidate paths can pass them
via ``extra_candidates``; those are tried BEFORE step 3 to preserve the
original selection order.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

# Legacy fallback list — same entries the old modules used.
# Kept here as the single source of truth so any future path additions
# (VPS failover, new WSL layouts) land in one place.
_LEGACY_POSSIBLE_ROOTS: tuple[Path, ...] = (
    Path("/Users/treycsa/CascadeProjects/algochains-control-tower"),
    Path("/home/trrey/algochains-control-tower"),
    Path("/mnt/c/Users/trrey/algochains-control-tower"),
)

_HARDCODED_FALLBACK = Path("/Users/treycsa/CascadeProjects/algochains-control-tower")


def default_control_tower(
    extra_candidates: Optional[Iterable[Path]] = None,
    *,
    require_exists: bool = False,
) -> Path:
    """Resolve the AlgoChains control-tower directory.

    Args:
        extra_candidates: module-specific paths to try before the shared
            legacy list. Useful when a caller has its own historical default
            (e.g. dashboard's ``~/CascadeProjects/algochains-control-tower``).
        require_exists: if True, only return paths that exist; fall back to
            the next candidate otherwise.  If nothing exists, return the
            hardcoded Mac fallback (matches old selection when no path found).

    Returns:
        ``Path`` object pointing at the resolved control-tower directory.
        Env-var paths are returned even if they don't exist — the caller is
        responsible for reporting missing directories (fail-closed per
        `.cursor/rules/01-real-data-only.mdc`).
    """
    # 1 + 2: environment variables win, unconditionally.
    for var in ("ALGOCHAINS_CONTROL_TOWER", "ALGOCHAINS_CONTROL_TOWER_PATH"):
        val = os.environ.get(var)
        if val:
            return Path(val)

    # 3: caller-provided candidates, then shared legacy list.
    candidates: list[Path] = []
    if extra_candidates:
        candidates.extend(Path(p) for p in extra_candidates)
    candidates.extend(_LEGACY_POSSIBLE_ROOTS)

    for p in candidates:
        try:
            if p.exists():
                return p
        except OSError:
            continue

    # 4: sibling layout (developer checkout side-by-side with mcp-server).
    try:
        sibling = Path(__file__).resolve().parents[3] / "algochains-control-tower"
        if sibling.exists():
            return sibling
    except Exception:
        pass

    # 5: hardcoded last-resort (matches prior behavior).
    if require_exists:
        # Callers that insist on existence get the legacy fallback too.
        return _HARDCODED_FALLBACK
    return _HARDCODED_FALLBACK


def default_heartbeat_paths() -> list[Path]:
    """Canonical ordered heartbeat-file candidates for dual-node awareness.

    The Mac bot writes `mac_heartbeat.json` under `<control-tower>/scripts/`.
    Desktop WSL historically also checked `/mnt/c/Users/trrey/...` and
    `/home/trrey/mac_heartbeat.json`; those paths are kept for back-compat
    but the control-tower/scripts path is authoritative.
    """
    ct = default_control_tower()
    return [
        ct / "scripts" / "mac_heartbeat.json",                  # canonical (both hosts)
        Path("/mnt/c/Users/trrey/mac_heartbeat.json"),          # WSL → Windows legacy
        Path("/home/trrey/mac_heartbeat.json"),                 # Ubuntu home legacy
        # Preserve old hardcoded Mac absolute path as a final fallback.
        Path("/Users/treycsa/CascadeProjects/algochains-control-tower/scripts/mac_heartbeat.json"),
    ]
