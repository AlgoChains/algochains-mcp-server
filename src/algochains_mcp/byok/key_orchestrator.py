"""
BYOK Key Orchestrator — Autonomous discovery, validation, gap analysis,
and provisioning of API keys for data providers.

Industry first: No platform (Composio, Nango, Arcade, Merge) offers
autonomous key discovery. They all require manual entry.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .provider_registry import (
    PROVIDER_REGISTRY,
    ProviderCategory,
    ProviderMeta,
    get_all_env_var_names,
)

logger = logging.getLogger("algochains.byok")


class KeyStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    EXPIRED = "expired"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"
    NOT_FOUND = "not_found"


class DiscoverySource(str, Enum):
    ENV_VAR = "environment_variable"
    DOTENV_FILE = "dotenv_file"
    IDE_CONFIG = "ide_config"
    SHELL_PROFILE = "shell_profile"
    CONFIG_DIR = "config_directory"


@dataclass
class DiscoveredKey:
    provider: str
    env_var: str
    masked_value: str
    source: DiscoverySource
    source_path: str
    status: KeyStatus = KeyStatus.UNKNOWN
    plan_tier: str = ""
    rate_limit: str = ""
    permissions: list[str] = field(default_factory=list)
    validated_at: Optional[str] = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "env_var": self.env_var,
            "masked_value": self.masked_value,
            "source": self.source.value,
            "source_path": self.source_path,
            "status": self.status.value,
            "plan_tier": self.plan_tier,
            "rate_limit": self.rate_limit,
            "permissions": self.permissions,
            "validated_at": self.validated_at,
            "error": self.error,
        }


@dataclass
class GapEntry:
    provider: str
    display_name: str
    signup_url: str
    free_tier: bool
    free_tier_limits: str
    unlocks: list[str]
    categories: list[str]
    priority: str  # "high", "medium", "low"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "signup_url": self.signup_url,
            "free_tier": self.free_tier,
            "free_tier_limits": self.free_tier_limits,
            "unlocks": self.unlocks,
            "categories": self.categories,
            "priority": self.priority,
            "notes": self.notes,
        }


def _mask_key(key: str) -> str:
    """Mask a key showing only first 4 and last 4 characters."""
    if len(key) <= 8:
        return key[:2] + "***" + key[-2:]
    return key[:4] + "***" + key[-4:]


def _parse_env_line(line: str) -> tuple[str, str] | None:
    """Parse a KEY=VALUE line from .env or shell profile."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    # Handle export KEY=VALUE
    if line.startswith("export "):
        line = line[7:]
    match = re.match(r'^([A-Z_][A-Z0-9_]*)=["\']?([^"\'#\s]+)["\']?', line)
    if match:
        return match.group(1), match.group(2)
    return None


class KeyOrchestrator:
    """Autonomous key discovery, validation, and management."""

    def __init__(self) -> None:
        self._discovered: dict[str, DiscoveredKey] = {}
        self._env_var_map = get_all_env_var_names()

    # ── Discovery ────────────────────────────────────────────────

    async def discover_keys(self) -> dict[str, Any]:
        """
        Scan all known locations for existing API keys.
        Returns found keys (masked), their locations, and provider info.
        """
        self._discovered.clear()
        found: list[DiscoveredKey] = []

        # 1. Environment variables (highest priority)
        found.extend(self._scan_env_vars())

        # 2. .env files in common locations
        found.extend(self._scan_dotenv_files())

        # 3. IDE MCP config files
        found.extend(self._scan_ide_configs())

        # 4. Shell profiles
        found.extend(self._scan_shell_profiles())

        # 5. Config directories
        found.extend(self._scan_config_dirs())

        # Deduplicate — keep highest priority source per provider
        for key in found:
            if key.provider not in self._discovered:
                self._discovered[key.provider] = key

        # Add no-key-needed providers
        no_key_providers = [
            p for p in PROVIDER_REGISTRY.values() if not p.requires_key
        ]
        for p in no_key_providers:
            if p.name not in self._discovered:
                self._discovered[p.name] = DiscoveredKey(
                    provider=p.name,
                    env_var="N/A",
                    masked_value="(no key needed)",
                    source=DiscoverySource.ENV_VAR,
                    source_path="built-in",
                    status=KeyStatus.VALID,
                    plan_tier="free",
                    permissions=p.data_types,
                )

        # Calculate coverage score
        total_providers = len(PROVIDER_REGISTRY)
        found_count = len(self._discovered)
        coverage_score = int((found_count / total_providers) * 100)

        return {
            "found_keys": [k.to_dict() for k in self._discovered.values()],
            "found_count": found_count,
            "total_providers": total_providers,
            "coverage_score": coverage_score,
            "scan_locations_checked": self._get_scan_locations_count(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _scan_env_vars(self) -> list[DiscoveredKey]:
        """Scan current environment variables."""
        found = []
        for env_var, provider_name in self._env_var_map.items():
            value = os.environ.get(env_var)
            if value and len(value) > 4:
                found.append(DiscoveredKey(
                    provider=provider_name,
                    env_var=env_var,
                    masked_value=_mask_key(value),
                    source=DiscoverySource.ENV_VAR,
                    source_path="os.environ",
                ))
        return found

    def _scan_dotenv_files(self) -> list[DiscoveredKey]:
        """Scan .env files in project and home directories."""
        found = []
        dotenv_paths = [
            Path(".env"),
            Path(".env.local"),
            Path(".env.production"),
            Path.home() / ".env",
            Path.home() / ".config" / "algochains" / ".env",
        ]
        for path in dotenv_paths:
            if path.exists() and path.is_file():
                found.extend(self._parse_file_for_keys(
                    path, DiscoverySource.DOTENV_FILE
                ))
        return found

    def _scan_ide_configs(self) -> list[DiscoveredKey]:
        """Scan IDE MCP configuration files for embedded keys."""
        found = []
        ide_configs = [
            Path.home() / ".windsurf" / "mcp-config.json",
            Path.home() / ".cursor" / "mcp.json",
            Path.home() / ".vscode" / "settings.json",
            Path.home() / ".continue" / "config.json",
        ]
        for path in ide_configs:
            if path.exists() and path.is_file():
                try:
                    data = json.loads(path.read_text())
                    found.extend(self._extract_keys_from_json(
                        data, str(path), DiscoverySource.IDE_CONFIG
                    ))
                except (json.JSONDecodeError, OSError):
                    continue
        return found

    def _scan_shell_profiles(self) -> list[DiscoveredKey]:
        """Scan shell profiles for export KEY=VALUE statements."""
        found = []
        profiles = [
            Path.home() / ".zshrc",
            Path.home() / ".bashrc",
            Path.home() / ".bash_profile",
            Path.home() / ".zprofile",
        ]
        for path in profiles:
            if path.exists() and path.is_file():
                found.extend(self._parse_file_for_keys(
                    path, DiscoverySource.SHELL_PROFILE
                ))
        return found

    def _scan_config_dirs(self) -> list[DiscoveredKey]:
        """Scan known config directories for provider-specific configs."""
        found = []
        config_dirs = [
            (Path.home() / ".config" / "polygon", "polygon"),
            (Path.home() / ".config" / "databento", "databento"),
        ]
        for dir_path, _provider in config_dirs:
            if dir_path.exists() and dir_path.is_dir():
                for f in dir_path.iterdir():
                    if f.suffix in (".json", ".env", ".cfg", ".ini"):
                        try:
                            if f.suffix == ".json":
                                data = json.loads(f.read_text())
                                found.extend(self._extract_keys_from_json(
                                    data, str(f), DiscoverySource.CONFIG_DIR
                                ))
                            else:
                                found.extend(self._parse_file_for_keys(
                                    f, DiscoverySource.CONFIG_DIR
                                ))
                        except (json.JSONDecodeError, OSError):
                            continue
        return found

    def _parse_file_for_keys(
        self, path: Path, source: DiscoverySource
    ) -> list[DiscoveredKey]:
        """Parse a text file for KEY=VALUE lines matching known env vars."""
        found = []
        try:
            for line in path.read_text().splitlines():
                parsed = _parse_env_line(line)
                if parsed:
                    var_name, value = parsed
                    if var_name in self._env_var_map and len(value) > 4:
                        found.append(DiscoveredKey(
                            provider=self._env_var_map[var_name],
                            env_var=var_name,
                            masked_value=_mask_key(value),
                            source=source,
                            source_path=str(path),
                        ))
        except OSError:
            pass
        return found

    def _extract_keys_from_json(
        self, data: Any, source_path: str, source: DiscoverySource
    ) -> list[DiscoveredKey]:
        """Recursively extract known env var keys from a JSON structure."""
        found = []
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, str) and key in self._env_var_map and len(value) > 4:
                    found.append(DiscoveredKey(
                        provider=self._env_var_map[key],
                        env_var=key,
                        masked_value=_mask_key(value),
                        source=source,
                        source_path=source_path,
                    ))
                elif isinstance(value, (dict, list)):
                    found.extend(self._extract_keys_from_json(
                        value, source_path, source
                    ))
        elif isinstance(data, list):
            for item in data:
                found.extend(self._extract_keys_from_json(
                    item, source_path, source
                ))
        return found

    def _get_scan_locations_count(self) -> int:
        """Count how many locations were scanned."""
        count = 1  # env vars
        for path_list in [
            [".env", ".env.local", ".env.production"],
            [str(Path.home() / ".zshrc"), str(Path.home() / ".bashrc")],
        ]:
            count += len(path_list)
        return count + 8  # approximate total scan locations

    # ── Validation ───────────────────────────────────────────────

    async def validate_keys(self, providers: list[str] | None = None) -> dict[str, Any]:
        """
        Deep-validate discovered keys with live API calls.
        Returns permissions, rate limits, plan tier for each.
        """
        import httpx

        targets = providers or list(self._discovered.keys())
        results = []

        async with httpx.AsyncClient(timeout=10.0) as client:
            for provider_name in targets:
                dk = self._discovered.get(provider_name)
                if not dk:
                    continue

                meta = PROVIDER_REGISTRY.get(provider_name)
                if not meta or not meta.requires_key or not meta.validation_url:
                    dk.status = KeyStatus.VALID
                    dk.validated_at = datetime.now(timezone.utc).isoformat()
                    results.append(dk.to_dict())
                    continue

                # Get the actual key value from env
                actual_key = None
                for env_var in meta.env_vars:
                    actual_key = os.environ.get(env_var)
                    if actual_key:
                        break

                if not actual_key:
                    dk.status = KeyStatus.NOT_FOUND
                    dk.error = "Key found in config but not in current environment"
                    results.append(dk.to_dict())
                    continue

                # Format validation check
                if meta.key_pattern and not meta.matches_key_format(actual_key):
                    dk.status = KeyStatus.INVALID
                    dk.error = f"Key format doesn't match expected pattern for {meta.display_name}"
                    results.append(dk.to_dict())
                    continue

                # Live API call
                try:
                    url = meta.validation_url.format(key=actual_key)
                    headers = {}
                    if meta.validation_method == "bearer":
                        url = meta.validation_url
                        headers["Authorization"] = f"Bearer {actual_key}"

                    resp = await client.get(url, headers=headers)

                    if resp.status_code == 200:
                        dk.status = KeyStatus.VALID
                        dk.plan_tier = self._detect_plan_tier(provider_name, resp)
                        dk.rate_limit = self._detect_rate_limit(resp)
                        dk.permissions = meta.data_types
                    elif resp.status_code == 401:
                        dk.status = KeyStatus.INVALID
                        dk.error = "Authentication failed (401)"
                    elif resp.status_code == 403:
                        dk.status = KeyStatus.INVALID
                        dk.error = "Access denied (403) — key may lack permissions"
                    elif resp.status_code == 429:
                        dk.status = KeyStatus.RATE_LIMITED
                        dk.error = "Rate limit exceeded — key is valid but throttled"
                    else:
                        dk.status = KeyStatus.UNKNOWN
                        dk.error = f"Unexpected response: {resp.status_code}"

                except Exception as e:
                    dk.status = KeyStatus.UNKNOWN
                    dk.error = f"Validation failed: {str(e)}"

                dk.validated_at = datetime.now(timezone.utc).isoformat()
                results.append(dk.to_dict())

        valid_count = sum(1 for r in results if r["status"] == "valid")
        return {
            "validated_keys": results,
            "valid_count": valid_count,
            "total_checked": len(results),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _detect_plan_tier(self, provider: str, resp: Any) -> str:
        """Detect plan tier from response headers or body."""
        # Check common rate limit headers
        remaining = resp.headers.get("x-ratelimit-remaining", "")
        limit = resp.headers.get("x-ratelimit-limit", "")
        if limit:
            limit_val = int(limit) if limit.isdigit() else 0
            if limit_val > 1000:
                return "premium"
            elif limit_val > 100:
                return "standard"
            else:
                return "free"
        return "detected"

    def _detect_rate_limit(self, resp: Any) -> str:
        """Extract rate limit info from response headers."""
        remaining = resp.headers.get("x-ratelimit-remaining", "")
        limit = resp.headers.get("x-ratelimit-limit", "")
        if limit and remaining:
            return f"{remaining}/{limit} remaining"
        return ""

    # ── Gap Analysis ─────────────────────────────────────────────

    async def gap_analysis(self) -> dict[str, Any]:
        """
        Show what providers are missing, what each unlocks,
        signup URLs, and free tier availability.
        """
        found_providers = set(self._discovered.keys())
        gaps: list[GapEntry] = []

        # Determine what categories are already covered
        covered_categories: set[str] = set()
        for dk in self._discovered.values():
            meta = PROVIDER_REGISTRY.get(dk.provider)
            if meta:
                for cat in meta.categories:
                    covered_categories.add(cat.value)

        for name, meta in PROVIDER_REGISTRY.items():
            if name in found_providers:
                continue

            # Calculate priority based on what's missing
            uncovered_cats = [
                c for c in meta.categories if c.value not in covered_categories
            ]
            if uncovered_cats:
                priority = "high"
            elif meta.free_tier:
                priority = "medium"
            else:
                priority = "low"

            gaps.append(GapEntry(
                provider=name,
                display_name=meta.display_name,
                signup_url=meta.signup_url,
                free_tier=meta.free_tier,
                free_tier_limits=meta.free_tier_limits,
                unlocks=meta.data_types,
                categories=[c.value for c in meta.categories],
                priority=priority,
                notes=meta.notes,
            ))

        # Sort: high priority first, then free tier, then alphabetical
        priority_order = {"high": 0, "medium": 1, "low": 2}
        gaps.sort(key=lambda g: (priority_order.get(g.priority, 3), not g.free_tier, g.display_name))

        # Coverage by category
        all_categories = set()
        for meta in PROVIDER_REGISTRY.values():
            for cat in meta.categories:
                all_categories.add(cat.value)

        category_coverage = {}
        for cat in all_categories:
            providers_for_cat = [
                p.name for p in PROVIDER_REGISTRY.values()
                if any(c.value == cat for c in p.categories)
            ]
            found_for_cat = [p for p in providers_for_cat if p in found_providers]
            pct = int((len(found_for_cat) / max(len(providers_for_cat), 1)) * 100)
            category_coverage[cat] = {
                "percentage": pct,
                "found": len(found_for_cat),
                "total": len(providers_for_cat),
            }

        # Quick win recommendation
        quick_win = None
        free_gaps = [g for g in gaps if g.free_tier and g.priority in ("high", "medium")]
        if free_gaps:
            qw = free_gaps[0]
            quick_win = {
                "provider": qw.display_name,
                "signup_url": qw.signup_url,
                "unlocks": qw.unlocks,
                "reason": f"Free tier available ({qw.free_tier_limits}). Fills gap in: {', '.join(qw.categories)}",
            }

        return {
            "missing_providers": [g.to_dict() for g in gaps],
            "missing_count": len(gaps),
            "found_count": len(found_providers),
            "total_providers": len(PROVIDER_REGISTRY),
            "coverage_score": int((len(found_providers) / len(PROVIDER_REGISTRY)) * 100),
            "category_coverage": category_coverage,
            "quick_win": quick_win,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Provisioning ─────────────────────────────────────────────

    async def provision_key(
        self, provider: str, key_value: str, write_to_env: bool = True
    ) -> dict[str, Any]:
        """
        Add a new API key for a provider. Validates it, then optionally
        writes to the project .env file.
        """
        meta = PROVIDER_REGISTRY.get(provider)
        if not meta:
            return {"error": f"Unknown provider: {provider}", "success": False}

        if not meta.requires_key:
            return {"error": f"{meta.display_name} doesn't require an API key", "success": False}

        # Format validation
        if meta.key_pattern and not meta.matches_key_format(key_value):
            return {
                "error": f"Key format doesn't match expected pattern for {meta.display_name}",
                "expected_pattern": meta.key_pattern,
                "success": False,
            }

        env_var = meta.env_vars[0]

        # Set in current environment
        os.environ[env_var] = key_value

        # Add to discovered keys
        dk = DiscoveredKey(
            provider=provider,
            env_var=env_var,
            masked_value=_mask_key(key_value),
            source=DiscoverySource.DOTENV_FILE if write_to_env else DiscoverySource.ENV_VAR,
            source_path=".env" if write_to_env else "os.environ",
        )
        self._discovered[provider] = dk

        # Write to .env if requested
        env_written = False
        if write_to_env:
            env_written = self._append_to_dotenv(env_var, key_value)

        # Validate the new key
        validation = await self.validate_keys(providers=[provider])

        return {
            "success": True,
            "provider": meta.display_name,
            "env_var": env_var,
            "masked_value": _mask_key(key_value),
            "env_written": env_written,
            "validation": validation,
        }

    def _append_to_dotenv(self, env_var: str, value: str) -> bool:
        """Append a key to the project's .env file."""
        env_path = Path(".env")
        try:
            existing = ""
            if env_path.exists():
                existing = env_path.read_text()

            # Check if var already exists
            pattern = re.compile(rf"^{re.escape(env_var)}=", re.MULTILINE)
            if pattern.search(existing):
                # Update existing
                updated = pattern.sub(f'{env_var}="{value}"', existing)
                env_path.write_text(updated)
            else:
                # Append
                with env_path.open("a") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write(f'{env_var}="{value}"\n')
            return True
        except OSError as e:
            logger.warning(f"Failed to write to .env: {e}")
            return False

    # ── Health Check ─────────────────────────────────────────────

    async def key_health(self) -> dict[str, Any]:
        """Real-time health check of all configured keys."""
        if not self._discovered:
            await self.discover_keys()

        validation = await self.validate_keys()

        healthy = sum(
            1 for k in validation["validated_keys"] if k["status"] == "valid"
        )
        unhealthy = sum(
            1 for k in validation["validated_keys"] if k["status"] in ("invalid", "expired")
        )

        return {
            "healthy": healthy,
            "unhealthy": unhealthy,
            "total": validation["total_checked"],
            "keys": validation["validated_keys"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Export Config ────────────────────────────────────────────

    async def export_config(self, format: str = "env") -> dict[str, Any]:
        """
        Export validated key configuration in various formats.
        Formats: env, json, mcp_windsurf, mcp_cursor, mcp_vscode
        """
        if not self._discovered:
            await self.discover_keys()

        keys_for_export: dict[str, str] = {}
        for dk in self._discovered.values():
            if dk.status == KeyStatus.VALID and dk.env_var != "N/A":
                # Get actual value from env
                actual = os.environ.get(dk.env_var, "")
                if actual:
                    keys_for_export[dk.env_var] = actual

        if format == "env":
            lines = [f'{k}="{v}"' for k, v in keys_for_export.items()]
            return {"format": "env", "content": "\n".join(lines), "key_count": len(keys_for_export)}

        elif format == "json":
            return {"format": "json", "content": keys_for_export, "key_count": len(keys_for_export)}

        elif format in ("mcp_windsurf", "mcp_cursor", "mcp_vscode"):
            mcp_config = {
                "mcpServers": {
                    "algochains": {
                        "command": "algochains-mcp",
                        "env": keys_for_export,
                    }
                }
            }
            return {
                "format": format,
                "content": json.dumps(mcp_config, indent=2),
                "key_count": len(keys_for_export),
                "target_file": {
                    "mcp_windsurf": "~/.windsurf/mcp-config.json",
                    "mcp_cursor": "~/.cursor/mcp.json",
                    "mcp_vscode": "~/.vscode/settings.json",
                }.get(format, ""),
            }

        return {"error": f"Unknown format: {format}. Use: env, json, mcp_windsurf, mcp_cursor, mcp_vscode"}
