"""Shared SSRF guard for outbound webhook / HTTP notification targets."""
from __future__ import annotations

_PRIVATE_NETWORK_PREFIXES = (
    "http://localhost",
    "http://127.",
    "http://10.",
    "http://192.168.",
    "http://172.16.",
    "http://172.17.",
    "http://172.18.",
    "http://172.19.",
    "http://172.2",
    "http://172.3",
    "https://localhost",
    "https://127.",
    "https://10.",
    "https://192.168.",
    "https://172.16.",
    "https://172.17.",
    "https://172.18.",
    "https://172.19.",
    "https://172.2",
    "https://172.3",
    "file://",
    "ftp://",
    "http://0.",
    "http://169.254.",  # link-local (AWS metadata)
)


def is_ssrf_target(url: str) -> bool:
    """Return True if the URL targets a private/link-local/loopback address."""
    lower = (url or "").lower()
    return any(lower.startswith(prefix) for prefix in _PRIVATE_NETWORK_PREFIXES)


def validate_webhook_url(url: str) -> str | None:
    """Return an error message when *url* is blocked, else None."""
    if not url:
        return None
    if is_ssrf_target(url):
        return (
            "Blocked: webhook_url targets a private or link-local address. "
            "Provide an externally reachable HTTPS endpoint."
        )
    return None
