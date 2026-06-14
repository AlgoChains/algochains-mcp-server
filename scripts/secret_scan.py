#!/usr/bin/env python3
"""
Secret scan for algochains-mcp-server.

Scans tracked source files for hardcoded API keys, tokens, passwords, and PII
patterns (Tailscale IPs, tunnel UUIDs, Slack IDs, internal hostnames).

Usage:
    python3 scripts/secret_scan.py          # scan src/ + tests/ + scripts/
    python3 scripts/secret_scan.py --all    # scan every tracked file (incl. docs)
    python3 scripts/secret_scan.py --ci     # exit 1 if any hits (for CI)

Exit codes:
    0  No hits
    1  Hits found (or error)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── patterns ──────────────────────────────────────────────────────────────────
# Each entry: (regex, description)
# Lines are pre-filtered by _is_env_lookup and comment stripping.

def _is_env_lookup(line: str) -> bool:
    return any(kw in line for kw in (
        "os.environ", "os.getenv", "getenv(", ".env", "environ.get",
        "process.env", "Deno.env",
        "# noqa", "# secret-scan-skip",
    ))


PATTERNS: list[tuple[re.Pattern, str]] = [
    # Credentials
    (re.compile(r'(?i)(alpaca|polygon|finnhub|tradovate)[_\-]?(api[_\-]?key|secret)[_\s]*[=:]\s*["\'][A-Za-z0-9+/]{16,}["\']'), "Broker API key literal"),
    (re.compile(r'(?i)(api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token)\s*=\s*["\'][A-Za-z0-9+/._\-]{20,}["\']'), "API key assignment"),
    (re.compile(r'(?i)password\s*=\s*["\'][^"\']{8,}["\']'), "Hardcoded password"),
    (re.compile(r'["\'](?:sk|pk)-[A-Za-z0-9]{20,}["\']'), "sk- / pk- prefixed key"),
    (re.compile(r'(?i)bearer\s+[A-Za-z0-9._\-]{30,}'), "Bearer token"),
    (re.compile(r'os\.environ\[["\'][A-Z_]+["\']\]\s*=\s*["\'][A-Za-z0-9+/._\-]{16,}["\']'), "os.environ hard-set (not os.environ.get)"),
    # Infrastructure PII
    (re.compile(r'\b100\.(89|99)\.\d{1,3}\.\d{1,3}\b'), "Tailscale internal IP"),
    (re.compile(r'\b172\.(232|238)\.\d{1,3}\.\d{1,3}\b'), "Linode/internal server IP"),
    (re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'), "Potential UUID (tunnel ID / secret)"),
    (re.compile(r'(?i)\bU[A-Z0-9]{8,}\b'), "Slack user ID format"),
    (re.compile(r'(?i)\bC[A-Z0-9]{8,}\b'), "Slack channel ID format"),
    (re.compile(r'teespc-\d+'), "Internal hostname"),
]

# PII patterns that should only be scanned in --all (docs/markdown) mode
PII_ONLY_PATTERNS: set[str] = {
    "Tailscale internal IP",
    "Linode/internal server IP",
    "Potential UUID (tunnel ID / secret)",
    "Slack user ID format",
    "Slack channel ID format",
    "Internal hostname",
}

SCAN_EXTS = {".py", ".sh", ".yaml", ".yml", ".json", ".toml", ".env.example"}
SCAN_EXTS_ALL = SCAN_EXTS | {".md", ".ts", ".tsx", ".sql"}

SKIP_PATHS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", "dist", "build",
}

# False-positive allowlist for known-safe strings that match patterns
SAFE_LITERALS = {
    "<your-api-key>", "<api-key>", "YOUR_KEY_HERE", "PLACEHOLDER",
    "sk-placeholder", "pk-placeholder",
    # Redacted infra placeholders (post-audit)
    "<TAILSCALE_GPU_HOST>", "<ALGOCHAINS_DJANGO_HOST>", "<your-tunnel-id>",
    "ALGOCHAINS_TOWER_HOST", "SUPABASE_PROJECT_REF",
    # Known test/example UUIDs
    "123e4567-e89b-12d3-a456-426614174000",
    # Known test fixture strings (intentionally descriptive names)
    "super-secret-massive-key", "should-not-reach-attacker",
}


def scan_file(path: Path, include_pii: bool = False) -> list[tuple[int, str, str]]:
    """Return list of (line_no, description, line_text) hits."""
    hits = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except Exception:
        return hits
    for i, line in enumerate(lines, 1):
        if _is_env_lookup(line):
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pat, desc in PATTERNS:
            if desc in PII_ONLY_PATTERNS and not include_pii:
                continue
            m = pat.search(line)
            if m:
                value = m.group(0)
                if any(safe.lower() in value.lower() for safe in SAFE_LITERALS):
                    continue
                hits.append((i, desc, stripped[:120]))
                break  # one hit per line is enough
    return hits


def collect_files(root: Path, all_files: bool) -> list[Path]:
    paths: list[Path] = []
    exts = SCAN_EXTS_ALL if all_files else SCAN_EXTS
    if all_files:
        for p in root.rglob("*"):
            if any(part in SKIP_PATHS for part in p.parts):
                continue
            if p.is_file() and p.suffix in exts:
                paths.append(p)
    else:
        for sub in ("src", "tests", "scripts"):
            d = root / sub
            if d.is_dir():
                for p in d.rglob("*"):
                    if any(part in SKIP_PATHS for part in p.parts):
                        continue
                    if p.is_file() and p.suffix in exts:
                        paths.append(p)
    return sorted(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Secret scan for algochains-mcp-server")
    parser.add_argument("--all", action="store_true", help="Scan all tracked files (incl. docs/markdown)")
    parser.add_argument("--ci", action="store_true", help="CI mode: exit 1 on hits")
    args = parser.parse_args(argv)

    include_pii = args.all  # PII patterns (IPs, Slack IDs) only relevant in docs scan
    files = collect_files(ROOT, all_files=args.all)
    total_hits = 0
    for path in files:
        hits = scan_file(path, include_pii=include_pii)
        if hits:
            rel = path.relative_to(ROOT)
            print(f"\n{rel}:")
            for lineno, desc, text in hits:
                print(f"  Line {lineno:4d}  [{desc}]  {text}")
                total_hits += 1

    print(f"\nSecret scan: {len(files)} files scanned, {total_hits} hits.")
    if total_hits > 0:
        print("Fix: replace hardcoded values with os.environ.get('VAR_NAME') and add to .env")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
