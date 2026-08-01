"""Versioned Django marketplace API route contract.

Keep all MCP callers on the routes owned by ``Django_Algochains/marketplace/urls.py``.
"""

LISTINGS_COLLECTION_PATH = "/api/v1/listings/"
LISTING_CREATE_PATH = "/api/v1/listings/create/"


def listing_detail_path(slug: str) -> str:
    return f"/api/v1/listings/{slug}/"


def listing_update_path(slug: str) -> str:
    return f"/api/v1/listings/{slug}/update/"


def listing_subscribe_path(slug: str) -> str:
    return f"/api/v1/listings/{slug}/subscribe/"


def listing_unsubscribe_path(slug: str) -> str:
    return f"/api/v1/listings/{slug}/unsubscribe/"


def listing_metrics_path(slug: str) -> str:
    return f"/api/v1/listings/{slug}/metrics/"


SUBSCRIPTIONS_COLLECTION_PATH = "/api/v1/subscriptions/"
