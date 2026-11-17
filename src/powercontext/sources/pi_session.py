"""Capture complete visible events from a selected Pi session branch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionInput(BaseModel):
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _visible_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for part in value:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text" and isinstance(part.get("text"), str):
            parts.append(part["text"])
        elif kind == "toolCall":
            name, call_id = part.get("name"), part.get("id")
            arguments = json.dumps(part.get("arguments", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            parts.append(f"[tool_call {name} {call_id}] {arguments}")
    return "\n".join(parts)


def _event_body(record: dict) -> tuple[str, str] | None:
    kind = record.get("type")
    if kind in {"compaction", "branch_summary"}:
        role = kind
        body = record.get("summary")
    elif kind == "custom_message":
        role = "custom"
        body = _visible_content(record.get("content"))
    elif kind == "message" and isinstance(record.get("message"), dict):
        message = record["message"]
        role = message.get("role")
        if role == "toolResult":
            payload = _visible_content(message.get("content"))
            body = f"[tool_result {message.get('toolName')} {message.get('toolCallId')}] {payload}"
        elif role == "bashExecution":
            body = f"$ {message.get('command', '')}\n{message.get('output', '')}"
        else:
            body = _visible_content(message.get("content"))
    else:
        return None
    if not isinstance(role, str) or not isinstance(body, str) or not body:
        return None
    return role, body


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Validate the complete JSONL tree, then return one visible ancestor path."""
    try:
        lines = [json.loads(line) for line in value.content.decode("utf-8-sig").splitlines() if line.strip()]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid Pi session JSONL") from error
    if not lines or not isinstance(lines[0], dict):
        raise ValueError("Missing Pi session header")
    header = lines[0]
    session_id = header.get("id")
    if header.get("type") != "session" or header.get("version") != 3 or not isinstance(session_id, str) or not session_id:
        raise ValueError("Invalid Pi session header")
    nodes: dict[str, dict] = {}
    for record in lines[1:]:
        if not isinstance(record, dict):
            raise ValueError("Invalid Pi session node")
        event_id, parent_id = record.get("id"), record.get("parentId")
        if not isinstance(event_id, str) or not event_id or event_id in nodes:
            raise ValueError("Missing or duplicate Pi event id")
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in nodes):
            raise ValueError("Pi event parent must precede its child")
        nodes[event_id] = record
    leaf = value.leaf_id if value.leaf_id is not None else next(reversed(nodes), None)
    if leaf not in nodes:
        raise ValueError("Pi session leaf does not exist")
    path: list[dict] = []
    visited: set[str] = set()
    while leaf is not None:
        if leaf in visited:
            raise ValueError("Pi session contains a cycle")
        visited.add(leaf)
        record = nodes[leaf]
        path.append(record)
        leaf = record["parentId"]
    events: list[PiEventInput] = []
    for record in reversed(path):
        result = _event_body(record)
        if result is None:
            continue
        role, body = result
        events.append(PiEventInput(
            session_id=session_id,
            event_id=record["id"],
            parent_id=record["parentId"],
            event_type=record.get("type", ""),
            role=role,
            content=f"{role}: {body}",
            record=record,
        ))
    return tuple(events)


def _identity(session_id: str, event_id: str) -> str:
    encoded = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(encoded) <= 256:
        return encoded
    digest = hashlib.sha256(json.dumps([session_id, event_id], ensure_ascii=False).encode()).hexdigest()
    return f"pi:{digest}"


class PiEventAdapter:
    input_class = PiEventInput
    name = "pi-event"
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = _identity(value.session_id, value.event_id)
        return PiEventSource(name=identity, source_id=identity, materialization=SourceMaterialization.CAPTURED,
                             **value.model_dump())

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput.model_validate(source.model_dump(include=set(PiEventInput.model_fields)))


class PiEventProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(source_type="pi-event", source_id=source.source_id, content=source.content,
                            metadata={key: getattr(source, key) for key in
                                      ("session_id", "event_id", "parent_id", "event_type", "role")})


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(PiEventAdapter(), projections=(PiEventProjection(),))


class PiSessionConnector:
    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self.path.read_bytes(), leaf_id=self.leaf_id))
        previous = session.checkpoint or {}
        if not isinstance(previous, dict):
            raise ValueError("Invalid Pi checkpoint")
        imported = previous.get("events", {})
        if not isinstance(imported, dict):
            raise ValueError("Invalid Pi checkpoint events")
        updated = dict(imported)
        for event in events:
            identity = _identity(event.session_id, event.event_id)
            digest = hashlib.sha256(json.dumps(event.record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if identity in imported:
                if imported[identity] != digest:
                    raise ValueError("Previously imported Pi event was changed")
                continue
            outcome = await session.submit(identity, "pi-event", event)
            if outcome.status is ConnectorSubmissionStatus.ACCEPTED:
                updated[identity] = digest
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint={"events": updated})
