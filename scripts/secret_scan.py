#!/usr/bin/env python3
"""
Secret scan for algochains-mcp-server.

Scans all tracked Python, YAML, JSON, TOML, and shell files for patterns
that look like hardcoded API keys, tokens, or passwords.

Usage:
    python3 scripts/secret_scan.py          # scan src/ + tests/ + scripts/
    python3 scripts/secret_scan.py --all    # scan every tracked file
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
# Each entry: (regex, description, is_allowlist_fn)
# Allowlist fn takes the matching line and returns True if it should be skipped.

def _is_env_lookup(line: str) -> bool:
    return any(kw in line for kw in (
        "os.environ", "os.getenv", "getenv(", ".env", "environ.get",
        "# noqa", "# secret-scan-skip",
    ))


PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'(?i)(alpaca|polygon|finnhub|tradovate|alpaca)[_\-]?(api[_\-]?key|secret)[_\s]*[=:]\s*["\'][A-Za-z0-9+/]{16,}["\']'), "Broker API key literal"),
    (re.compile(r'(?i)(api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token)\s*=\s*["\'][A-Za-z0-9+/._\-]{20,}["\']'), "API key assignment"),
    (re.compile(r'(?i)password\s*=\s*["\'][^"\']{8,}["\']'), "Hardcoded password"),
    (re.compile(r'["\'](?:sk|pk)-[A-Za-z0-9]{20,}["\']'), "sk- / pk- prefixed key"),
    (re.compile(r'(?i)bearer\s+[A-Za-z0-9._\-]{30,}'), "Bearer token"),
    (re.compile(r'os\.environ\[["\'][A-Z_]+["\']\]\s*=\s*["\'][A-Za-z0-9+/._\-]{16,}["\']'), "os.environ hard-set (not os.environ.get)"),
]

SCAN_EXTS = {".py", ".sh", ".yaml", ".yml", ".json", ".toml", ".env.example"}

SKIP_PATHS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", "dist", "build",
}

# False-positive allowlist for known-safe strings that match patterns
SAFE_LITERALS = {
    "<your-api-key>", "<api-key>", "YOUR_KEY_HERE", "PLACEHOLDER",
    "sk-placeholder", "pk-placeholder",
}


def scan_file(path: Path) -> list[tuple[int, str, str]]:
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
            m = pat.search(line)
            if m:
                # Check safe literals
                value = m.group(0)
                if any(safe.lower() in value.lower() for safe in SAFE_LITERALS):
                    continue
                hits.append((i, desc, stripped[:120]))
                break  # one hit per line is enough
    return hits


def collect_files(root: Path, all_files: bool) -> list[Path]:
    paths: list[Path] = []
    if all_files:
        for p in root.rglob("*"):
            if any(part in SKIP_PATHS for part in p.parts):
                continue
            if p.is_file() and p.suffix in SCAN_EXTS:
                paths.append(p)
    else:
        for sub in ("src", "tests", "scripts"):
            d = root / sub
            if d.is_dir():
                for p in d.rglob("*"):
                    if any(part in SKIP_PATHS for part in p.parts):
                        continue
                    if p.is_file() and p.suffix in SCAN_EXTS:
                        paths.append(p)
    return sorted(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Secret scan for algochains-mcp-server")
    parser.add_argument("--all", action="store_true", help="Scan all tracked files")
    parser.add_argument("--ci", action="store_true", help="CI mode: exit 1 on hits")
    args = parser.parse_args(argv)

    files = collect_files(ROOT, all_files=args.all)
    total_hits = 0
    for path in files:
        hits = scan_file(path)
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
