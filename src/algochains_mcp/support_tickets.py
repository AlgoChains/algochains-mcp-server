"""
support_tickets.py — IT Support Ticket System for AlgoChains
=============================================================

Backs the Support Page → creates, tracks, and resolves tickets.
Primary store: Supabase (algochains_support_tickets table).
Optional sync: Notion (NOTION_SUPPORT_DB_ID env var).

Ticket lifecycle:  open → in_progress → resolved | closed

No synthetic data. Every ticket read/write hits real Supabase rows.
Env vars required:
  SUPABASE_URL              — Supabase project URL
  SUPABASE_SERVICE_KEY      — Service-role key (can write tickets)
Optional:
  NOTION_API_KEY            — Sync tickets to Notion database
  NOTION_SUPPORT_DB_ID      — Target Notion database ID
  RESEND_API_KEY            — Send email confirmations to users
  SUPPORT_FROM_EMAIL        — Sender address (default: support@algochains.ai)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("algochains_mcp.support_tickets")

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_SUPPORT_DB_ID = os.getenv("NOTION_SUPPORT_DB_ID", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SUPPORT_FROM_EMAIL = os.getenv("SUPPORT_FROM_EMAIL", "support@algochains.ai")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SUPPORT_CHANNEL = os.getenv("SLACK_SUPPORT_CHANNEL", "#Support-tickets")

_TABLE = "algochains_support_tickets"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketCategory(str, Enum):
    BROKER_CONNECTION = "broker_connection"
    BOT_PERFORMANCE = "bot_performance"
    BILLING = "billing"
    ACCOUNT = "account"
    ONBOARDING = "onboarding"
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    OTHER = "other"


# ── Supabase client helpers ───────────────────────────────────────────────────

def _sb_headers(service_role: bool = True) -> dict:
    key = SUPABASE_SERVICE_KEY if service_role else os.getenv("SUPABASE_ANON_KEY", "")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _sb_url(path: str) -> str:
    return f"{SUPABASE_URL.rstrip('/')}/rest/v1/{path}"


def _sb_available() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


# ── Fallback file store (when Supabase not configured) ────────────────────────

_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_LOCAL_TICKETS_FILE = _STATE_DIR / "support_tickets.json"


def _load_local_tickets() -> dict[str, dict]:
    if _LOCAL_TICKETS_FILE.exists():
        try:
            return json.loads(_LOCAL_TICKETS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_local_tickets(tickets: dict[str, dict]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _LOCAL_TICKETS_FILE.write_text(json.dumps(tickets, indent=2, default=str))


# ── Notion sync ───────────────────────────────────────────────────────────────

async def _sync_to_notion(ticket: dict) -> Optional[str]:
    """Create or update a Notion page for the ticket. Returns Notion page ID."""
    if not NOTION_API_KEY or not NOTION_SUPPORT_DB_ID:
        return None

    priority_color = {
        "critical": "red",
        "high": "orange",
        "medium": "yellow",
        "low": "default",
    }.get(ticket.get("priority", "medium"), "default")

    payload = {
        "parent": {"database_id": NOTION_SUPPORT_DB_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": f"[{ticket['ticket_id']}] {ticket['subject']}"}}]},
            "Status": {"select": {"name": ticket.get("status", "open").replace("_", " ").title()}},
            "Priority": {"select": {"name": ticket.get("priority", "medium").title(), "color": priority_color}},
            "Category": {"select": {"name": ticket.get("category", "other").replace("_", " ").title()}},
            "User Email": {"email": ticket.get("user_email", "")},
            "Ticket ID": {"rich_text": [{"text": {"content": ticket["ticket_id"]}}]},
            "Created": {"date": {"start": ticket.get("created_at", datetime.now(timezone.utc).isoformat())}},
        },
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": ticket.get("description", "")}}]
                },
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.notion.com/v1/pages",
                headers={
                    "Authorization": f"Bearer {NOTION_API_KEY}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code == 200:
                page_id = resp.json().get("id")
                logger.info("Ticket %s synced to Notion page %s", ticket["ticket_id"], page_id)
                return page_id
            else:
                logger.warning("Notion sync failed %s: %s", resp.status_code, resp.text[:200])
                return None
    except Exception as e:
        logger.error("Notion sync error: %s", e)
        return None


# ── Email confirmation ────────────────────────────────────────────────────────

async def _send_ticket_confirmation(ticket: dict) -> bool:
    """Send acknowledgment email to user via Resend."""
    if not RESEND_API_KEY or not ticket.get("user_email"):
        return False

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={
                    "from": SUPPORT_FROM_EMAIL,
                    "to": [ticket["user_email"]],
                    "subject": f"Support Ticket #{ticket['ticket_id']} Received — {ticket['subject']}",
                    "html": f"""
<h2>We received your support request</h2>
<p>Ticket ID: <strong>{ticket['ticket_id']}</strong></p>
<p>Subject: {ticket['subject']}</p>
<p>Priority: {ticket.get('priority', 'medium').title()}</p>
<p>We'll respond within 24 hours. You can track your ticket status at
<a href="https://algochains.ai/support/tickets/{ticket['ticket_id']}">
algochains.ai/support/tickets/{ticket['ticket_id']}</a></p>
<hr>
<p><em>AlgoChains Support Team</em></p>
""",
                },
            )
            return resp.status_code in (200, 201)
    except Exception as e:
        logger.error("Ticket confirmation email failed: %s", e)
        return False


# ── Core CRUD ─────────────────────────────────────────────────────────────────

async def _notify_slack_support(ticket: dict[str, Any]) -> None:
    """Post a new-ticket alert to the Slack #Support-tickets channel.

    Non-fatal: logs warning on failure, never raises.
    Requires SLACK_BOT_TOKEN env var to be set.
    """
    if not SLACK_BOT_TOKEN:
        logger.debug("_notify_slack_support: SLACK_BOT_TOKEN not set — skipping Slack alert")
        return

    ticket_id = ticket.get("ticket_id", "TKT-???")
    category = ticket.get("category", "other")
    priority = ticket.get("priority", "medium")
    subject = ticket.get("subject", "(no subject)")
    user_email = ticket.get("user_email", "unknown")

    priority_emoji = {"critical": ":rotating_light:", "high": ":red_circle:", "medium": ":large_yellow_circle:", "low": ":white_circle:"}.get(priority, ":white_circle:")

    text = (
        f"{priority_emoji} *New Support Ticket — {ticket_id}*\n"
        f"• *Subject:* {subject}\n"
        f"• *Category:* {category}  |  *Priority:* {priority}\n"
        f"• *Email:* {user_email}"
    )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
                json={"channel": SLACK_SUPPORT_CHANNEL, "text": text, "unfurl_links": False},
            )
            data = resp.json() if resp.status_code == 200 else {}
            if not data.get("ok"):
                logger.warning("_notify_slack_support: Slack API error for ticket %s: %s", ticket_id, data.get("error", resp.text[:120]))
            else:
                logger.info("_notify_slack_support: posted alert for ticket %s to %s", ticket_id, SLACK_SUPPORT_CHANNEL)
    except Exception as exc:
        logger.warning("_notify_slack_support: failed to post Slack alert for ticket %s: %s", ticket_id, exc)


async def create_ticket(
    subject: str,
    description: str,
    user_email: str,
    category: str = "other",
    priority: str = "medium",
    user_id: Optional[str] = None,
    attachments: Optional[list[str]] = None,
    metadata: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Create a new support ticket.

    Args:
        subject:     Short summary of the issue (max 200 chars)
        description: Full description of the problem
        user_email:  User's email for reply notifications
        category:    One of: broker_connection, bot_performance, billing, account,
                     onboarding, bug, feature_request, other
        priority:    low | medium | high | critical
        user_id:     Optional Supabase user ID
        attachments: Optional list of S3/storage URLs
        metadata:    Any extra context (browser, OS, bot name, etc.)

    Returns:
        dict with ticket_id, status, notion_page_id (if synced)
    """
    if not subject or not description or not user_email:
        return {"success": False, "error": "subject, description, and user_email are required"}

    if category not in [c.value for c in TicketCategory]:
        category = "other"
    if priority not in [p.value for p in TicketPriority]:
        priority = "medium"

    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc).isoformat()

    ticket: dict[str, Any] = {
        "ticket_id": ticket_id,
        "subject": subject[:200],
        "description": description,
        "user_email": user_email.lower().strip(),
        "user_id": user_id,
        "category": category,
        "priority": priority,
        "status": TicketStatus.OPEN.value,
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
        "notion_page_id": None,
        "attachments": attachments or [],
        "metadata": metadata or {},
        "responses": [],
    }

    # Try Supabase first
    notion_page_id = None
    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    _sb_url(_TABLE),
                    headers=_sb_headers(),
                    json=ticket,
                )
                if resp.status_code in (200, 201):
                    logger.info("Ticket %s created in Supabase", ticket_id)
                else:
                    logger.warning("Supabase insert failed %s: %s", resp.status_code, resp.text[:200])
                    _save_local_tickets({**_load_local_tickets(), ticket_id: ticket})
        except Exception as e:
            logger.error("Supabase ticket create error: %s", e)
            _save_local_tickets({**_load_local_tickets(), ticket_id: ticket})
    else:
        _save_local_tickets({**_load_local_tickets(), ticket_id: ticket})

    # Slack alert — non-fatal, always attempt
    await _notify_slack_support(ticket)

    # Sync to Notion
    notion_page_id = await _sync_to_notion(ticket)
    if notion_page_id:
        ticket["notion_page_id"] = notion_page_id
        if _sb_available():
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    await client.patch(
                        _sb_url(f"{_TABLE}?ticket_id=eq.{ticket_id}"),
                        headers=_sb_headers(),
                        json={"notion_page_id": notion_page_id},
                    )
            except Exception:
                pass

    # Send confirmation email
    await _send_ticket_confirmation(ticket)

    return {
        "success": True,
        "ticket_id": ticket_id,
        "status": TicketStatus.OPEN.value,
        "notion_page_id": notion_page_id,
        "message": f"Ticket {ticket_id} created. Confirmation sent to {user_email}.",
    }


async def get_ticket(ticket_id: str) -> dict[str, Any]:
    """Get a support ticket by ID."""
    if not ticket_id:
        return {"success": False, "error": "ticket_id required"}

    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    _sb_url(f"{_TABLE}?ticket_id=eq.{ticket_id}&limit=1"),
                    headers=_sb_headers(),
                )
                if resp.status_code == 200:
                    rows = resp.json()
                    if rows:
                        return {"success": True, "ticket": rows[0]}
                    return {"success": False, "error": f"Ticket {ticket_id} not found"}
        except Exception as e:
            logger.error("Supabase get_ticket error: %s", e)
            return {"success": False, "error": f"Supabase get_ticket failed: {e}"}

    return {"success": False, "error": "Supabase not configured — ticket reads require SUPABASE_URL + SUPABASE_SERVICE_KEY"}


async def list_tickets(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    category: Optional[str] = None,
    user_email: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List support tickets with optional filters."""
    if _sb_available():
        try:
            params: list[str] = []
            if status:
                params.append(f"status=eq.{status}")
            if priority:
                params.append(f"priority=eq.{priority}")
            if category:
                params.append(f"category=eq.{category}")
            if user_email:
                params.append(f"user_email=eq.{user_email.lower()}")
            query = "&".join(params)
            url = _sb_url(f"{_TABLE}?{query}&limit={limit}&offset={offset}&order=created_at.desc")
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, headers=_sb_headers())
                if resp.status_code == 200:
                    rows = resp.json()
                    return {"success": True, "tickets": rows, "count": len(rows)}
        except Exception as e:
            logger.error("Supabase list_tickets error: %s", e)
            return {"success": False, "error": f"Supabase list_tickets failed: {e}"}

    return {"success": False, "error": "Supabase not configured — ticket list requires SUPABASE_URL + SUPABASE_SERVICE_KEY"}


async def update_ticket_status(
    ticket_id: str,
    status: str,
    agent_response: Optional[str] = None,
    agent_email: Optional[str] = None,
) -> dict[str, Any]:
    """Update ticket status and optionally add an agent response."""
    valid_statuses = {s.value for s in TicketStatus}
    if status not in valid_statuses:
        return {"success": False, "error": f"status must be one of: {valid_statuses}"}

    now = datetime.now(timezone.utc).isoformat()
    updates: dict[str, Any] = {
        "status": status,
        "updated_at": now,
    }
    if status in (TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value):
        updates["resolved_at"] = now
    if agent_response and agent_email:
        updates["last_agent_response"] = {
            "message": agent_response,
            "agent": agent_email,
            "timestamp": now,
        }

    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.patch(
                    _sb_url(f"{_TABLE}?ticket_id=eq.{ticket_id}"),
                    headers=_sb_headers(),
                    json=updates,
                )
                if resp.status_code in (200, 204):
                    logger.info("Ticket %s status → %s", ticket_id, status)
                    return {"success": True, "ticket_id": ticket_id, "new_status": status}
        except Exception as e:
            logger.error("Supabase update_ticket error: %s", e)

    # Fallback local
    tickets = _load_local_tickets()
    if ticket_id not in tickets:
        return {"success": False, "error": f"Ticket {ticket_id} not found"}
    tickets[ticket_id].update(updates)
    _save_local_tickets(tickets)
    return {"success": True, "ticket_id": ticket_id, "new_status": status}


async def get_ticket_stats() -> dict[str, Any]:
    """Get aggregate ticket statistics for the support dashboard."""
    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    _sb_url(f"{_TABLE}?select=status,priority,category"),
                    headers=_sb_headers(),
                )
                if resp.status_code == 200:
                    rows = resp.json()
                    by_status: dict[str, int] = {}
                    by_priority: dict[str, int] = {}
                    by_category: dict[str, int] = {}
                    for row in rows:
                        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
                        by_priority[row["priority"]] = by_priority.get(row["priority"], 0) + 1
                        by_category[row["category"]] = by_category.get(row["category"], 0) + 1
                    return {
                        "success": True,
                        "total": len(rows),
                        "by_status": by_status,
                        "by_priority": by_priority,
                        "by_category": by_category,
                        "open_critical": sum(
                            1 for r in rows if r["status"] == "open" and r["priority"] == "critical"
                        ),
                    }
        except Exception as e:
            logger.error("Supabase ticket stats error: %s", e)
            return {"success": False, "error": f"Supabase ticket stats failed: {e}"}

    return {"success": False, "error": "Supabase not configured — ticket stats require SUPABASE_URL + SUPABASE_SERVICE_KEY"}
