"""
Skills Registry — AlgoChains MCP Server
Indexes all skills from OpenClaw, Windsurf, Cursor, and Claude skill libraries.
Provides search, discovery, and skill-text retrieval via MCP tools.

Real data only: reads actual SKILL.md files from disk.
Fails closed if skill directories are unavailable.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from algochains_mcp.paths import default_control_tower

_CONTROL_TOWER = default_control_tower()

# Canonical skill root directories (ordered by priority).
# Skill directories anchored to the control tower now use the shared resolver
# so ALGOCHAINS_CONTROL_TOWER is honored on the desktop tower. Before this
# fix, desktop Ubuntu/WSL found 0 skills because only the Mac absolute path
# was listed.
_SKILL_ROOTS: list[tuple[str, str]] = [
    # (label, path)
    ("windsurf",   str(_CONTROL_TOWER / ".windsurf" / "skills")),
    ("openclaw",   str(Path.home() / ".openclaw" / "skills")),
    ("cursor",     str(Path.home() / ".cursor" / "skills-cursor")),
    ("claude",     str(_CONTROL_TOWER / ".claude" / "skills")),
    # algochains-mcp-server built-in skills
    ("mcp-server", str(Path(__file__).parent.parent.parent / "skills")),
]

# Skill category keywords for auto-tagging
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "trading": ["bot", "trade", "signal", "order", "fill", "pnl", "position", "futures",
                "equity", "forex", "crypto", "options", "regime", "alpha", "strategy",
                "scalper", "swing", "backtest", "drawdown", "risk", "circuit-breaker",
                "market", "momentum", "vix", "vol", "flow", "dark-pool", "unusual"],
    "research": ["researcher", "research", "hypothesis", "study", "backtest", "paper",
                 "ssrn", "academic", "data", "scanner", "analysis"],
    "operations": ["deploy", "restart", "health", "monitor", "audit", "diagnostic",
                   "heartbeat", "watchdog", "failover", "incident", "recovery"],
    "intelligence": ["onyx", "rag", "knowledge", "memory", "intel", "brain", "search",
                     "semantic", "embedding", "graph", "insight"],
    "agent": ["agent", "crew", "orchestrat", "debate", "moltbook", "autonomy",
              "self-heal", "telemetry", "evaluation", "skill", "task"],
    "comms": ["slack", "email", "gmail", "notify", "alert", "digest", "report",
              "linkedin", "notion", "gws", "workspace"],
    "risk": ["risk", "circuit", "kill-switch", "drawdown", "limit", "guard",
             "protection", "breaker", "compliance", "fat-finger"],
    "data": ["data", "csv", "ingest", "pipeline", "etl", "databento", "polygon",
             "tick", "bar", "backfill", "freshness", "quality"],
    "ml": ["ml", "model", "retrain", "calibrat", "feature", "drift", "decay",
           "vertex", "gpu", "predict", "classify"],
    "marketplace": ["marketplace", "mcpt", "promote", "listing", "subscriber",
                    "strategy-vault", "creator"],
    # Numerai tournament keywords (§9 / skill-keywords todo)
    "numerai": ["numerai", "nmr", "mmc", "tournament", "ender20", "ender", "corr",
                "numeroo", "proxy_mmc", "proxy_corr", "mmcrep", "numerapi",
                "era-based", "feature-neutralization", "submission-window",
                "classic-tournament", "signals-tournament", "staking"],
}


@dataclass
class SkillEntry:
    name: str
    platform: str
    description: str
    path: str
    categories: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    trigger: str = ""
    schedule: str = ""
    auto_run: bool = False
    raw_frontmatter: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "platform": self.platform,
            "description": self.description,
            "path": self.path,
            "categories": self.categories,
            "tools": self.tools,
            "trigger": self.trigger,
            "schedule": self.schedule,
            "auto_run": self.auto_run,
        }


def _parse_frontmatter(text: str) -> dict:
    """Parse simple YAML frontmatter between --- delimiters."""
    fm: dict = {}
    if not text.startswith("---"):
        return fm
    end = text.find("\n---", 3)
    if end == -1:
        return fm
    block = text[3:end].strip()
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # handle list values like: tools: [shell, message]
        if val.startswith("[") and val.endswith("]"):
            items = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",")]
            fm[key] = [i for i in items if i]
        elif val.lower() == "true":
            fm[key] = True
        elif val.lower() == "false":
            fm[key] = False
        else:
            fm[key] = val
    return fm


def _auto_categories(name: str, description: str) -> list[str]:
    """Auto-assign categories based on name + description keywords."""
    combined = (name + " " + description).lower()
    cats = []
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            cats.append(cat)
    return cats or ["general"]


def _load_skill_dir(root_path: str, platform: str) -> list[SkillEntry]:
    """Scan a skill directory and return SkillEntry list."""
    entries: list[SkillEntry] = []
    root = Path(root_path)
    if not root.exists():
        logger.debug(f"Skill root not found (skipping): {root_path}")
        return entries

    # Support both flat files (SKILL.md at root) and subdirectory-based layouts
    skill_files: list[Path] = []
    for item in root.iterdir():
        if item.is_dir():
            skill_md = item / "SKILL.md"
            if skill_md.exists():
                skill_files.append(skill_md)
        elif item.name.endswith(".md") and item.name not in ("HOW_TO_USE_SKILLS.md",
                                                              "SKILL_BLUEPRINT_TOP20.md",
                                                              "changelog.md"):
            skill_files.append(item)

    for skill_md in skill_files:
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            fm = _parse_frontmatter(text)
            name = fm.get("name") or skill_md.parent.name or skill_md.stem
            description = fm.get("description", "")
            # Strip description to first 200 chars if long
            if len(description) > 200:
                description = description[:197] + "..."
            tools_raw = fm.get("tools", [])
            if isinstance(tools_raw, str):
                tools_raw = [t.strip() for t in tools_raw.split(",")]
            entry = SkillEntry(
                name=str(name),
                platform=platform,
                description=description,
                path=str(skill_md),
                categories=_auto_categories(str(name), description),
                tools=tools_raw if isinstance(tools_raw, list) else [],
                trigger=str(fm.get("trigger", "")),
                schedule=str(fm.get("schedule", "")),
                auto_run=bool(fm.get("auto_run", False)),
                raw_frontmatter=fm,
            )
            entries.append(entry)
        except Exception as e:
            logger.debug(f"Could not parse skill at {skill_md}: {e}")

    return entries


class SkillsRegistry:
    """Unified registry of all skills across platforms."""

    def __init__(self) -> None:
        self._skills: Dict[str, SkillEntry] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self.reload()

    def reload(self) -> int:
        """Scan all skill roots and rebuild index. Returns total skills loaded."""
        new_skills: Dict[str, SkillEntry] = {}
        for platform, root_path in _SKILL_ROOTS:
            entries = _load_skill_dir(root_path, platform)
            for entry in entries:
                key = entry.name.lower().strip()
                if key in new_skills:
                    # Keep the one from higher-priority platform (first wins)
                    existing = new_skills[key]
                    logger.debug(
                        f"Skill '{key}' exists from '{existing.platform}', "
                        f"skipping duplicate from '{platform}'"
                    )
                else:
                    new_skills[key] = entry
        self._skills = new_skills
        self._loaded = True
        logger.info(f"Skills registry loaded: {len(self._skills)} skills across "
                    f"{len(set(e.platform for e in self._skills.values()))} platforms")
        return len(self._skills)

    def list_skills(
        self,
        category: Optional[str] = None,
        platform: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """List skills with optional filtering."""
        self._ensure_loaded()
        skills = list(self._skills.values())

        if category:
            cat = category.lower()
            skills = [s for s in skills if cat in s.categories]

        if platform:
            plat = platform.lower()
            skills = [s for s in skills if s.platform == plat]

        # Sort by name for consistency
        skills.sort(key=lambda s: s.name)
        total = len(skills)
        page = skills[offset:offset + limit]

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "platforms": sorted(set(s.platform for s in self._skills.values())),
            "categories": sorted(set(c for s in self._skills.values() for c in s.categories)),
            "skills": [s.to_dict() for s in page],
        }

    def get_skill_detail(self, name: str) -> dict:
        """Return full SKILL.md content and metadata for a skill."""
        self._ensure_loaded()
        key = name.lower().strip()
        entry = self._skills.get(key)
        if not entry:
            # Try partial match
            matches = [e for k, e in self._skills.items() if key in k]
            if not matches:
                return {
                    "error": f"Skill '{name}' not found",
                    "available_count": len(self._skills),
                    "hint": "Use list_skills or search_skills to discover available skills",
                }
            entry = matches[0]

        try:
            content = Path(entry.path).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"error": f"Could not read skill file at {entry.path}: {e}"}

        return {
            "name": entry.name,
            "platform": entry.platform,
            "description": entry.description,
            "categories": entry.categories,
            "tools": entry.tools,
            "trigger": entry.trigger,
            "schedule": entry.schedule,
            "auto_run": entry.auto_run,
            "path": entry.path,
            "content": content,
            "content_length": len(content),
        }

    def search_skills(self, query: str, limit: int = 20) -> dict:
        """Search skills by name/description keyword match."""
        self._ensure_loaded()
        q = query.lower()
        scored: list[tuple[float, SkillEntry]] = []

        for entry in self._skills.values():
            score = 0.0
            name_lower = entry.name.lower()
            desc_lower = entry.description.lower()

            # Exact name match = highest priority
            if q == name_lower:
                score += 10.0
            elif q in name_lower:
                score += 5.0

            # Description match
            if q in desc_lower:
                score += 3.0

            # Category match
            if q in " ".join(entry.categories):
                score += 2.0

            # Tool list match
            if q in " ".join(entry.tools):
                score += 1.0

            # Word-level partial match in name
            for word in q.split():
                if word in name_lower:
                    score += 1.0
                if word in desc_lower:
                    score += 0.5

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        results = [
            {**e.to_dict(), "relevance_score": round(s, 2)}
            for s, e in scored[:limit]
        ]

        return {
            "query": query,
            "total_matches": len(scored),
            "results": results,
        }

    def get_skills_for_task(self, task_description: str) -> dict:
        """Return the best skills for a given task description (keyword matching)."""
        result = self.search_skills(task_description, limit=5)
        return {
            "task": task_description,
            "recommended_skills": result["results"],
            "tip": "Call get_skill_detail(name) to read the full SKILL.md instructions",
        }

    def stats(self) -> dict:
        """Return registry statistics."""
        self._ensure_loaded()
        by_platform: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for entry in self._skills.values():
            by_platform[entry.platform] = by_platform.get(entry.platform, 0) + 1
            for cat in entry.categories:
                by_category[cat] = by_category.get(cat, 0) + 1
        return {
            "total_skills": len(self._skills),
            "by_platform": by_platform,
            "by_category": by_category,
            "skill_roots": [r for _, r in _SKILL_ROOTS],
        }


# Module-level singleton
_registry: Optional[SkillsRegistry] = None


def get_registry() -> SkillsRegistry:
    global _registry
    if _registry is None:
        _registry = SkillsRegistry()
    return _registry
