"""Fork Avibe Agent Sessions.

This module owns the Avibe-level fork contract: validate the source Session,
copy its row into a new pending Session, and carry enough metadata for backend
adapters to call their native fork API on the first turn.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from config import paths


class SessionForkError(ValueError):
    """Raised when a Session cannot be forked."""


@dataclass(frozen=True)
class SessionForkSpec:
    source_session_id: str
    source_native_session_id: str
    source_backend: str

    def to_metadata(self) -> dict[str, Any]:
        return {
            "source_session_id": self.source_session_id,
            "source_native_session_id": self.source_native_session_id,
            "source_backend": self.source_backend,
        }


@dataclass(frozen=True)
class SessionForkResult:
    session_id: str
    agent_name: Optional[str]
    agent_id: Optional[str]
    agent_backend: str
    model: Optional[str]
    reasoning_effort: Optional[str]
    fork: SessionForkSpec


def reserve_forked_session(
    *,
    source_session_id: str,
    agent_name: Optional[str] = None,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> SessionForkResult:
    """Copy an existing Agent Session row into a new pending fork target.

    ``agent_name`` may switch to another enabled Avibe Agent only when that
    Agent uses the same backend as the source. ``model`` and
    ``reasoning_effort`` are simple per-session overrides. The new row's native
    id stays empty until the backend adapter successfully forks the native
    session.
    """

    from sqlalchemy import select

    from core.vibe_agents import VibeAgentStore
    from storage.agent_session_rows import create_agent_session_row, utc_now_iso
    from storage.db import create_sqlite_engine
    from storage.importer import ensure_sqlite_state, resolve_primary_platform_from_config
    from storage.models import agent_sessions

    path = db_path or paths.get_sqlite_state_path()
    if db_path is None:
        ensure_sqlite_state(primary_platform=resolve_primary_platform_from_config(paths.get_state_dir()))
    engine = create_sqlite_engine(path)
    agent_store = VibeAgentStore(path)
    try:
        with engine.begin() as conn:
            row = conn.execute(
                select(agent_sessions).where(agent_sessions.c.id == str(source_session_id)).limit(1)
            ).mappings().first()
            if row is None:
                raise SessionForkError(f"agent session id not found: {source_session_id}")
            if str(row["status"] or "") == "archived":
                raise SessionForkError(f"agent session is archived: {source_session_id}")

            source_backend = str(row["agent_backend"] or "").strip()
            if source_backend not in {"codex", "claude", "opencode"}:
                raise SessionForkError(f"session backend cannot be forked: {source_backend or 'unknown'}")
            source_native = str(row["native_session_id"] or "").strip()
            if not source_native:
                raise SessionForkError(
                    f"agent session has no native session id to fork: {source_session_id}"
                )

            override_agent = agent_store.require_enabled(agent_name) if agent_name else None
            if override_agent is not None and override_agent.backend != source_backend:
                raise SessionForkError(
                    "agent backend does not match the source session backend"
                )

            target_agent_id = override_agent.id if override_agent else row["agent_id"]
            target_agent_name = override_agent.name if override_agent else row["agent_name"]
            target_backend = override_agent.backend if override_agent else source_backend
            target_variant = str(row["agent_variant"] or target_backend)
            target_model = _clean_optional(model) if model is not None else row["model"]
            target_effort = (
                _clean_optional(reasoning_effort)
                if reasoning_effort is not None
                else row["reasoning_effort"]
            )

            now = utc_now_iso()
            metadata = _load_metadata(row["metadata_json"])
            metadata.update(
                {
                    "created_via": "session_fork",
                    "fork_source_session_id": str(row["id"]),
                    "fork_source_native_session_id": source_native,
                    "fork_source_backend": source_backend,
                    "fork_created_at": now,
                }
            )
            session_id = create_agent_session_row(
                conn,
                scope_id=row["scope_id"],
                # A fork is an independent Avibe Session, so it needs a fresh
                # anchor even when it stays under the same scope.
                session_anchor=None,
                agent_id=target_agent_id,
                agent_name=target_agent_name,
                agent_backend=target_backend,
                agent_variant=target_variant,
                model=target_model,
                reasoning_effort=target_effort,
                workdir=row["workdir"],
                native_session_id="",
                title=row["title"],
                metadata=metadata,
                now=now,
                require_workdir=False,
            )

        fork = SessionForkSpec(
            source_session_id=str(source_session_id),
            source_native_session_id=source_native,
            source_backend=source_backend,
        )
        return SessionForkResult(
            session_id=session_id,
            agent_name=str(target_agent_name).strip() if target_agent_name else None,
            agent_id=str(target_agent_id).strip() if target_agent_id else None,
            agent_backend=target_backend,
            model=target_model,
            reasoning_effort=target_effort,
            fork=fork,
        )
    finally:
        agent_store.close()
        engine.dispose()


def fork_metadata_from_request(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return normalized pending fork metadata from an Agent Run record."""

    if not isinstance(metadata, dict):
        return None
    fork = metadata.get("session_fork")
    if not isinstance(fork, dict):
        return None
    source_backend = _clean_optional(fork.get("source_backend"))
    source_native = _clean_optional(fork.get("source_native_session_id"))
    source_session = _clean_optional(fork.get("source_session_id"))
    if not (source_backend and source_native and source_session):
        return None
    return {
        "source_session_id": source_session,
        "source_native_session_id": source_native,
        "source_backend": source_backend,
    }


def pending_native_fork_source(context: Any, backend: str) -> Optional[str]:
    """Native source id if this turn should fork instead of start fresh."""

    payload = getattr(context, "platform_specific", None) or {}
    target = payload.get("agent_session_target")
    if not isinstance(target, dict):
        return None
    target_backend = str(target.get("agent_backend") or "").strip()
    if target_backend and target_backend != backend:
        return None
    if str(target.get("native_session_id") or "").strip():
        return None
    fork = target.get("native_session_fork")
    if not isinstance(fork, dict):
        return None
    if str(fork.get("source_backend") or "").strip() != backend:
        return None
    source_native = str(fork.get("source_native_session_id") or "").strip()
    return source_native or None


def _load_metadata(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _clean_optional(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
