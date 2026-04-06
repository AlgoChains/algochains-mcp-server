"""
Onyx Intelligence Layer — AlgoChains Knowledge Search
======================================================
Connects AlgoChains MCP tools to the self-hosted Onyx knowledge base
(Onyx on-prem at 100.89.114.31:8085 via Tailscale).

Capabilities:
  - Semantic search across 400+ strategy research JSONs
  - Bot performance history queries ("best CL Sharpe setup last 90 days")
  - Blueprint/skill discovery ("how do I set up Token Guardian?")
  - Real-time log ingestion for live bot state queries
  - Trade outcome context injection into agent sessions

Architecture:
  Onyx REST API → /api/query/stream-answer (streaming semantic search)
  Onyx REST API → /api/document-set (index management)
  Onyx REST API → /api/search (direct vector search)

All data flows from real Onyx instance. No synthetic responses.
Raises OnyxUnavailableError if Onyx cannot be reached.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

log = logging.getLogger(__name__)

ONYX_BASE = os.getenv("ONYX_API_URL", "http://100.89.114.31:8085")
ONYX_KEY = os.getenv("ONYX_API_KEY", "")
ONYX_ADMIN_EMAIL = os.getenv("ONYX_ADMIN_EMAIL", "admin@algochains.io")
ONYX_ADMIN_PASS = os.getenv("ONYX_ADMIN_PASS", "")

TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class OnyxUnavailableError(RuntimeError):
    """Raised when Onyx cannot be reached or authentication fails."""


@dataclass
class OnyxSearchResult:
    document_id: str
    content: str
    source: str
    score: float
    metadata: dict = field(default_factory=dict)


@dataclass
class OnyxAnswer:
    question: str
    answer: str
    citations: list[OnyxSearchResult] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


class OnyxClient:
    """
    Async Onyx API client for AlgoChains MCP server.

    Uses Onyx's REST API for semantic search and QA.
    Authenticates with basic auth (email/password) to get a session token.
    """

    def __init__(self):
        self._token: str | None = None
        self._client = httpx.AsyncClient(base_url=ONYX_BASE, timeout=TIMEOUT)

    async def _ensure_auth(self) -> None:
        if self._token:
            return
        try:
            resp = await self._client.post(
                "/api/auth/token/login",
                json={"username": ONYX_ADMIN_EMAIL, "password": ONYX_ADMIN_PASS},
            )
            if resp.status_code == 200:
                self._token = resp.json().get("access_token", "")
            elif resp.status_code == 401:
                raise OnyxUnavailableError("Onyx authentication failed — check ONYX_ADMIN_EMAIL / ONYX_ADMIN_PASS")
            else:
                raise OnyxUnavailableError(f"Onyx auth returned {resp.status_code}: {resp.text[:200]}")
        except httpx.ConnectError as exc:
            raise OnyxUnavailableError(
                f"Cannot reach Onyx at {ONYX_BASE} — is the desktop PC online and Tailscale connected? ({exc})"
            ) from exc

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if ONYX_KEY:
            h["Authorization"] = f"Bearer {ONYX_KEY}"
        elif self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def search(
        self,
        query: str,
        limit: int = 10,
        document_set: str | None = None,
    ) -> list[OnyxSearchResult]:
        """
        Direct vector search over Onyx knowledge base.
        Returns ranked results with content and source metadata.
        """
        await self._ensure_auth()
        payload: dict[str, Any] = {
            "query": query,
            "num_results": limit,
            "search_type": "SEMANTIC",
        }
        if document_set:
            payload["document_set_ids"] = [document_set]

        try:
            resp = await self._client.post(
                "/api/search", json=payload, headers=self._headers()
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get("results", data.get("documents", [])):
                results.append(OnyxSearchResult(
                    document_id=item.get("document_id", ""),
                    content=item.get("content", item.get("highlight", "")),
                    source=item.get("source", {}).get("url", item.get("source_id", "")),
                    score=item.get("score", 0.0),
                    metadata=item.get("metadata", {}),
                ))
            return results
        except httpx.ConnectError as exc:
            raise OnyxUnavailableError(f"Onyx search failed: {exc}") from exc

    async def ask(
        self,
        question: str,
        document_set: str | None = None,
        stream: bool = False,
    ) -> OnyxAnswer:
        """
        Ask Onyx a natural language question with RAG grounding.
        Returns an answer with cited sources from the knowledge base.
        """
        await self._ensure_auth()
        payload: dict[str, Any] = {
            "query": question,
            "search_settings": {"num_results": 8},
            "return_contexts": True,
        }
        if document_set:
            payload["document_set_ids"] = [document_set]

        try:
            resp = await self._client.post(
                "/api/query/stream-answer",
                json=payload,
                headers=self._headers(),
            )
            if resp.status_code == 404:
                # Fall back to direct search
                results = await self.search(question, limit=5)
                return OnyxAnswer(
                    question=question,
                    answer="\n\n".join(r.content[:500] for r in results[:3]),
                    citations=results,
                    sources=[r.source for r in results],
                )
            resp.raise_for_status()

            # Parse streaming SSE response
            full_answer = ""
            citations = []
            for line in resp.text.splitlines():
                if not line.strip():
                    continue
                if line.startswith("data:"):
                    try:
                        import json
                        chunk = json.loads(line[5:])
                        if "answer_piece" in chunk:
                            full_answer += chunk["answer_piece"]
                        if "documents" in chunk:
                            for doc in chunk["documents"]:
                                citations.append(OnyxSearchResult(
                                    document_id=doc.get("document_id", ""),
                                    content=doc.get("content", ""),
                                    source=doc.get("source", {}).get("url", ""),
                                    score=doc.get("score", 0),
                                    metadata=doc.get("metadata", {}),
                                ))
                    except Exception:
                        pass

            if not full_answer:
                # Extract from plain JSON response
                try:
                    import json
                    data = json.loads(resp.text)
                    full_answer = data.get("answer", data.get("response", ""))
                except Exception:
                    full_answer = resp.text[:1000]

            return OnyxAnswer(
                question=question,
                answer=full_answer,
                citations=citations,
                sources=list({c.source for c in citations}),
            )

        except httpx.ConnectError as exc:
            raise OnyxUnavailableError(f"Onyx Q&A failed: {exc}") from exc

    async def health(self) -> dict[str, Any]:
        """Check Onyx health status."""
        try:
            resp = await self._client.get("/api/health", timeout=5.0)
            if resp.status_code == 200:
                return {"status": "healthy", "url": ONYX_BASE, "response": resp.json()}
            return {"status": "degraded", "url": ONYX_BASE, "http_status": resp.status_code}
        except httpx.ConnectError:
            return {"status": "unreachable", "url": ONYX_BASE,
                    "hint": "Start Onyx on desktop or ensure Tailscale is connected"}

    async def ingest_text(
        self, name: str, content: str, metadata: dict | None = None
    ) -> bool:
        """
        Ingest a raw text document into Onyx via the file upload API.
        Returns True on success.
        """
        await self._ensure_auth()
        try:
            resp = await self._client.post(
                "/api/connector/file/ingest",
                json={
                    "name": name,
                    "content": content,
                    "metadata": metadata or {},
                },
                headers=self._headers(),
            )
            return resp.status_code in (200, 201, 202)
        except Exception as exc:
            log.warning("Onyx ingest failed for %s: %s", name, exc)
            return False

    async def close(self) -> None:
        await self._client.aclose()


# Module-level singleton
_onyx: OnyxClient | None = None


def get_onyx_client() -> OnyxClient:
    global _onyx
    if _onyx is None:
        _onyx = OnyxClient()
    return _onyx


async def onyx_search(query: str, limit: int = 10) -> list[OnyxSearchResult]:
    """Convenience function for one-shot searches."""
    return await get_onyx_client().search(query, limit)


async def onyx_ask(question: str) -> OnyxAnswer:
    """Convenience function for one-shot Q&A."""
    return await get_onyx_client().ask(question)
