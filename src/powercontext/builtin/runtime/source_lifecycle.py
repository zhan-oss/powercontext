"""Durable dependency propagation for source-backed memories."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryCitation
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE as NODES
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.sources.models import SourceRef


class SourceLifecycleError(ValueError):
    """An invalid binding, event, or reference use."""


class SourceLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    node_id: str
    revision: int
    sequence: int
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must be nonblank")
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("value must be positive")
        return value


class ScopedSourceLifecycle:
    def __init__(self, runtime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = scope_id

    @property
    def _database(self):
        return self._runtime._records()._database

    async def _rows(self) -> dict[str, dict]:
        async with self._database.transaction() as connection:
            result = await connection.execute(select(NODES).where(NODES.c.scope_id == self.scope_id))
            return {row.node_id: dict(row._mapping) for row in result}

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("invalid source revision")
        await self._register(node_id, "source", source_ref, (), "all", revision)

    async def register_memory(
        self, node_id: str, citation: MemoryCitation, *, parents: tuple[str, ...], policy: Literal["all", "any"]
    ) -> None:
        await self._register(node_id, "memory", citation, parents, policy, 0)

    async def register_topic(
        self, node_id: str, artifact_ref: ArtifactRef, *, parents: tuple[str, ...], policy: Literal["all", "any"]
    ) -> None:
        await self._register(node_id, "topic", artifact_ref, parents, policy, 0)

    async def _register(self, node_id, kind, reference, parents, policy, revision) -> None:
        if not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 256:
            raise SourceLifecycleError("invalid node id")
        if policy not in ("all", "any"):
            raise SourceLifecycleError("invalid dependency policy")
        parents = tuple(parents)
        if kind != "source" and (not parents or len(set(parents)) != len(parents) or node_id in parents):
            raise SourceLifecycleError("invalid dependency parents")
        rows = await self._rows()
        if any(parent not in rows for parent in parents):
            raise SourceLifecycleError("unknown dependency parent")
        encoded = reference.model_dump_json()
        prior = rows.get(node_id)
        if prior is not None:
            if (prior["kind"], prior["reference"], prior["parents"], prior["policy"], prior["revision"]) == (
                kind, encoded, json.dumps(parents), policy, revision
            ):
                return
            raise SourceLifecycleError("node id is already bound")
        try:
            if kind == "source":
                await self._runtime.records.for_scope(self.scope_id).get_source(
                    reference.source_type, reference.source_id
                )
            elif kind == "memory":
                await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=reference))
            else:
                if reference.family != "topic-memory":
                    raise SourceLifecycleError("wrong topic family")
                await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                    "topic-memory", reference.artifact_id, reference.revision
                )
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError("registered reference is unavailable") from error
        available = all(rows[parent]["available"] for parent in parents) if policy == "all" else any(
            rows[parent]["available"] for parent in parents
        )
        if kind == "source":
            available = True
        if kind == "memory" and not available:
            await self._set_memory(reference, False)
        async with self._database.transaction() as connection:
            await connection.execute(insert(NODES).values(
                scope_id=self.scope_id, node_id=node_id, kind=kind, reference=encoded,
                parents=json.dumps(parents), policy=policy, revision=revision,
                available=available, sequence=0, last_event=None,
            ))

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        rows = await self._rows()
        root = rows.get(event.node_id)
        if root is None or root["kind"] != "source" or root["revision"] != event.revision:
            raise SourceLifecycleError("unknown source or revision")
        encoded = event.model_dump_json()
        if event.sequence < root["sequence"] or (
            event.sequence == root["sequence"] and encoded != root["last_event"]
        ):
            raise SourceLifecycleError("stale or conflicting event")
        if event.sequence == root["sequence"]:
            return sorted(node_id for node_id, row in rows.items() if row["available"])
        desired = {node_id: bool(row["available"]) for node_id, row in rows.items()}
        desired[event.node_id] = event.available
        pending = {node_id for node_id, row in rows.items() if row["kind"] != "source"}
        while pending:
            progress = False
            for node_id in tuple(pending):
                parents = json.loads(rows[node_id]["parents"])
                if any(parent in pending for parent in parents):
                    continue
                desired[node_id] = (all if rows[node_id]["policy"] == "all" else any)(
                    desired[parent] for parent in parents
                )
                pending.remove(node_id)
                progress = True
            if not progress:
                raise SourceLifecycleError("dependency cycle")
        # Reconcile the native Memory state before committing the event. If a
        # write fails, retrying the same event completes the remaining nodes.
        for node_id, row in rows.items():
            if row["kind"] == "memory":
                await self._set_memory(MemoryCitation.model_validate_json(row["reference"]), desired[node_id])
        async with self._database.transaction() as connection:
            for node_id, available in desired.items():
                values = {"available": available}
                if node_id == event.node_id:
                    values.update(sequence=event.sequence, last_event=encoded)
                await connection.execute(update(NODES).where(
                    NODES.c.scope_id == self.scope_id, NODES.c.node_id == node_id
                ).values(**values))
        return sorted(node_id for node_id, available in desired.items() if available)

    async def _set_memory(self, citation: MemoryCitation, available: bool) -> None:
        async with self._runtime._context(self.scope_id) as context:
            async with self._runtime._locked(self.scope_id):
                service = context.artifacts.memory
                current = await service.head(context.artifacts.memory_artifact_id)
                manifest = next((item for item in current.content.manifest.entries
                                 if item.entry_id == citation.entry_id and item.entry_version_id == citation.entry_version_id), None)
                if manifest is None:
                    raise SourceLifecycleError("memory entry changed after registration")
                if (manifest.state == "active") == available:
                    return
                entry = await service.validate_citation(citation)
                if available:
                    await service.reactivate(current, entries=(entry,), reason="source lifecycle")
                else:
                    await service.forget(current, entries=(entry,), reason="source lifecycle")

    async def resolve(self, node_id: str, *, purpose: Literal["generation", "audit"]):
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError("unknown purpose")
        row = (await self._rows()).get(node_id)
        if row is None or (purpose == "generation" and not row["available"]):
            raise SourceLifecycleError("node is unavailable")
        cls = {"source": SourceRef, "memory": MemoryCitation, "topic": ArtifactRef}[row["kind"]]
        return cls.model_validate_json(row["reference"])

    async def inactive_topics(self) -> set[tuple[str, int]]:
        rows = await self._rows()
        return {(ref.artifact_id, ref.revision)
                for row in rows.values() if row["kind"] == "topic" and not row["available"]
                for ref in (ArtifactRef.model_validate_json(row["reference"]),)}


class SourceLifecycleApplication:
    def __init__(self, runtime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, scope_id)
