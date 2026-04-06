"""
MCP 2025-11-25 Tasks (durable requests) support.

Implements the experimental Tasks feature for tracking long-running operations
with polling and deferred result retrieval.

Use cases:
  - submit_to_marketplace → 7-gate MCPT validation (minutes)
  - optimize_strategy with n_trials > 500 → Optuna run (tens of minutes)
  - walk_forward_test → multi-fold WFE (minutes per fold)
  - paper trading period monitoring (days/weeks)

Task state machine:
  pending → running → completed | failed | cancelled

Persistence: SQLite at ~/.algochains/tasks.db
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskProgress:
    current: int
    total: int
    message: str
    pct: float = 0.0

    def __post_init__(self):
        if self.total > 0:
            self.pct = round(self.current / self.total * 100, 1)

    def to_dict(self) -> dict[str, Any]:
        return {"current": self.current, "total": self.total, "pct": self.pct, "message": self.message}


@dataclass
class Task:
    task_id: str
    tool_name: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    progress: TaskProgress | None = None
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    estimated_duration_seconds: int = 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "description": self.description,
            "status": self.status.value,
            "progress": self.progress.to_dict() if self.progress else None,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": round((self.completed_at or time.time()) - (self.started_at or self.created_at), 1),
            "estimated_duration_seconds": self.estimated_duration_seconds,
        }


class TaskManager:
    """
    Manages durable long-running tasks with SQLite persistence.

    Agents submit a task and immediately receive a task_id.
    They then poll get_task_status(task_id) for completion.
    Resource notifications are emitted when task state changes.
    """

    DB_PATH = Path.home() / ".algochains" / "tasks.db"

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._init_db()
        self._load_from_db()

    def _init_db(self) -> None:
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    tool_name TEXT,
                    description TEXT,
                    status TEXT,
                    result_json TEXT,
                    error TEXT,
                    created_at REAL,
                    started_at REAL,
                    completed_at REAL,
                    estimated_duration_seconds INTEGER
                )
            """)
            conn.commit()

    def _load_from_db(self) -> None:
        try:
            with sqlite3.connect(self.DB_PATH) as conn:
                rows = conn.execute("SELECT * FROM tasks WHERE status IN ('pending','running') LIMIT 100").fetchall()
                for row in rows:
                    task_id, tool_name, desc, status, result_json, error, created_at, started_at, completed_at, est = row
                    task = Task(
                        task_id=task_id,
                        tool_name=tool_name,
                        description=desc,
                        status=TaskStatus(status),
                        error=error or "",
                        created_at=created_at,
                        started_at=started_at,
                        completed_at=completed_at,
                        estimated_duration_seconds=est or 60,
                    )
                    if result_json:
                        task.result = json.loads(result_json)
                    # Mark previously-running tasks as failed (server restarted)
                    if task.status == TaskStatus.RUNNING:
                        task.status = TaskStatus.FAILED
                        task.error = "Task interrupted by server restart"
                    self._tasks[task_id] = task
        except Exception:
            pass

    def _persist(self, task: Task) -> None:
        try:
            with sqlite3.connect(self.DB_PATH) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks
                    (task_id, tool_name, description, status, result_json, error,
                     created_at, started_at, completed_at, estimated_duration_seconds)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    task.task_id, task.tool_name, task.description, task.status.value,
                    json.dumps(task.result) if task.result is not None else None,
                    task.error, task.created_at, task.started_at, task.completed_at,
                    task.estimated_duration_seconds,
                ))
                conn.commit()
        except Exception:
            pass

    # ── Public API ───────────────────────────────────────────────────

    def create_task(
        self,
        tool_name: str,
        description: str,
        estimated_duration_seconds: int = 60,
    ) -> Task:
        task = Task(
            task_id=str(uuid.uuid4()),
            tool_name=tool_name,
            description=description,
            estimated_duration_seconds=estimated_duration_seconds,
        )
        self._tasks[task.task_id] = task
        self._persist(task)
        return task

    def submit_async(
        self,
        task: Task,
        coro: Coroutine,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Run coro as a background asyncio task, updating task state on completion."""
        async def _wrapper():
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            self._persist(task)
            try:
                result = await coro
                task.status = TaskStatus.COMPLETED
                task.result = result
                task.completed_at = time.time()
            except asyncio.CancelledError:
                task.status = TaskStatus.CANCELLED
                task.completed_at = time.time()
            except Exception as exc:
                task.status = TaskStatus.FAILED
                task.error = str(exc)
                task.completed_at = time.time()
            finally:
                self._persist(task)
                self._running.pop(task.task_id, None)

        async_task = asyncio.ensure_future(_wrapper())
        self._running[task.task_id] = async_task

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def update_progress(self, task_id: str, current: int, total: int, message: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.progress = TaskProgress(current=current, total=total, message=message)

    def cancel_task(self, task_id: str) -> bool:
        async_task = self._running.get(task_id)
        if async_task and not async_task.done():
            async_task.cancel()
            return True
        task = self._tasks.get(task_id)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
            self._persist(task)
            return True
        return False

    def list_tasks(self, status_filter: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        tasks = list(self._tasks.values())
        if status_filter:
            tasks = [t for t in tasks if t.status.value == status_filter]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tasks[:limit]]

    def purge_old_tasks(self, older_than_hours: int = 24) -> int:
        cutoff = time.time() - older_than_hours * 3600
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        to_delete = [tid for tid, t in self._tasks.items() if t.status in terminal and t.created_at < cutoff]
        for tid in to_delete:
            del self._tasks[tid]
        return len(to_delete)

    # ── Convenience wrappers for common long-running tools ────────────

    def task_response(self, task: Task) -> dict[str, Any]:
        """Build the immediate response dict when a task is created."""
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "description": task.description,
            "estimated_duration_seconds": task.estimated_duration_seconds,
            "instruction": (
                f"Task submitted. Poll get_task_status(task_id='{task.task_id}') "
                "to check progress and retrieve results when complete."
            ),
        }


_task_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
