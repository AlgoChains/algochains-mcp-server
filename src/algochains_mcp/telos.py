"""
AlgoChains TELOS — Business Identity OS

Adapted from danielmiessler/Personal_AI_Infrastructure (PAI) TELOS concept.
TELOS = "Telic Evolution and Life Operating System" — structured files capturing
who AlgoChains is, what it's trying to achieve, and how it gets there.

Files live in algochains-control-tower/TELOS/:
  MISSION.md    — Why AlgoChains exists
  GOALS.md      — Q2 2026 targets
  STRATEGIES.md — How goals are achieved
  MODELS.md     — Trading and business mental models
  LEARNED.md    — Key lessons from live trading
  CHALLENGES.md — Current blockers and risks
  IDEAS.md      — Future expansion ideas
  METRICS.md    — KPIs: bots, marketplace, platform

These files give every AI agent (Cursor, Claude, Windsurf, OpenClaw) instant
AlgoChains context without re-explaining every session.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.telos")

# Canonical TELOS directory (control-tower repo)
_TELOS_DIR = Path(
    os.getenv(
        "ALGOCHAINS_TELOS_DIR",
        str(Path(__file__).parent.parent.parent.parent / "algochains-control-tower" / "TELOS"),
    )
)

_VALID_SECTIONS = {
    "mission", "goals", "strategies", "models",
    "learned", "challenges", "ideas", "metrics", "all",
}

_SECTION_FILE_MAP = {
    "mission":    "MISSION.md",
    "goals":      "GOALS.md",
    "strategies": "STRATEGIES.md",
    "models":     "MODELS.md",
    "learned":    "LEARNED.md",
    "challenges": "CHALLENGES.md",
    "ideas":      "IDEAS.md",
    "metrics":    "METRICS.md",
}


def get_telos(section: str = "all") -> dict[str, Any]:
    """
    Read AlgoChains TELOS files.

    Args:
        section: "all" | "mission" | "goals" | "strategies" | "models" |
                 "learned" | "challenges" | "ideas" | "metrics"

    Returns dict with content, section names, and source paths.
    """
    section = section.lower().strip()
    if section not in _VALID_SECTIONS:
        return {
            "error": f"Unknown section '{section}'. Valid: {sorted(_VALID_SECTIONS)}",
            "telos_dir": str(_TELOS_DIR),
        }

    if not _TELOS_DIR.exists():
        return {
            "error": "TELOS directory not found",
            "expected_path": str(_TELOS_DIR),
            "hint": "Set ALGOCHAINS_TELOS_DIR env var or ensure algochains-control-tower/TELOS/ exists",
        }

    sections_to_read = (
        list(_SECTION_FILE_MAP.keys()) if section == "all" else [section]
    )

    result: dict[str, Any] = {
        "telos_dir": str(_TELOS_DIR),
        "sections": {},
        "missing": [],
        "read_at": datetime.now(timezone.utc).isoformat(),
    }

    for sec in sections_to_read:
        fname = _SECTION_FILE_MAP[sec]
        fpath = _TELOS_DIR / fname
        if fpath.exists():
            try:
                content = fpath.read_text(encoding="utf-8")
                result["sections"][sec] = {
                    "content": content,
                    "file": fname,
                    "size_chars": len(content),
                }
            except Exception as exc:
                result["sections"][sec] = {"error": str(exc), "file": fname}
        else:
            result["missing"].append(fname)

    if section != "all" and section in result["sections"]:
        result["content"] = result["sections"][section].get("content", "")

    result["sections_found"] = len(result["sections"])
    result["sections_missing"] = len(result["missing"])
    return result


def update_telos(
    section: str,
    entry: str,
    action: str = "append",
) -> dict[str, Any]:
    """
    Append or prepend an entry to a TELOS file.

    Args:
        section: Which TELOS section to update (e.g. "learned", "ideas")
        entry: The content to add (markdown text)
        action: "append" (add to end) | "prepend" (add to start after header)

    Returns status dict with success/error.
    """
    section = section.lower().strip()
    if section == "all":
        return {"error": "Cannot update 'all' — specify a single section"}
    if section not in _SECTION_FILE_MAP:
        return {
            "error": f"Unknown section '{section}'. Valid: {sorted(_SECTION_FILE_MAP.keys())}",
        }
    if not entry.strip():
        return {"error": "entry cannot be empty"}
    if action not in ("append", "prepend"):
        return {"error": f"action must be 'append' or 'prepend', got '{action}'"}

    if not _TELOS_DIR.exists():
        try:
            _TELOS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return {"error": f"Cannot create TELOS directory: {exc}"}

    fpath = _TELOS_DIR / _SECTION_FILE_MAP[section]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Format the entry with timestamp if it's not already dated
    formatted_entry = entry.strip()
    if not any(formatted_entry.startswith(f"- **{timestamp}") for _ in [1]):
        if section == "learned":
            formatted_entry = f"- **{timestamp}** — {formatted_entry}"
        elif section == "ideas":
            formatted_entry = f"- **{formatted_entry}** *(added {timestamp})*"
        else:
            formatted_entry = f"\n### Added {timestamp}\n\n{formatted_entry}"

    try:
        if fpath.exists():
            existing = fpath.read_text(encoding="utf-8")
        else:
            # Bootstrap empty section file
            title = section.capitalize()
            existing = f"# AlgoChains {title}\n\n*Last updated: {timestamp}*\n\n---\n"

        if action == "append":
            new_content = existing.rstrip() + "\n\n" + formatted_entry + "\n"
        else:
            # prepend: insert after the header block (after first ---)
            parts = existing.split("---\n", 1)
            if len(parts) == 2:
                new_content = parts[0] + "---\n\n" + formatted_entry + "\n\n" + parts[1]
            else:
                new_content = formatted_entry + "\n\n" + existing

        fpath.write_text(new_content, encoding="utf-8")
        return {
            "success": True,
            "section": section,
            "file": str(fpath),
            "action": action,
            "entry_preview": formatted_entry[:200],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("TELOS update failed for section=%s: %s", section, exc)
        return {"error": str(exc), "section": section}


def get_telos_summary() -> dict[str, Any]:
    """Return a concise summary of all TELOS sections for quick agent context."""
    full = get_telos("all")
    if "error" in full:
        return full

    summary: dict[str, str] = {}
    for sec, data in full.get("sections", {}).items():
        if "content" in data:
            lines = [l for l in data["content"].split("\n") if l.strip()]
            preview_lines = []
            for line in lines[2:12]:  # skip title + last-updated
                if line.startswith("#") or line.startswith("---"):
                    continue
                preview_lines.append(line)
                if len(preview_lines) >= 5:
                    break
            summary[sec] = " | ".join(preview_lines)[:300]

    return {
        "summary": summary,
        "sections_found": full["sections_found"],
        "telos_dir": full["telos_dir"],
    }
