"""Coalesce concurrent embedding texts and cache successful vectors."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage


@dataclass(slots=True)
class _Work:
    future: asyncio.Future[tuple[tuple[float, ...], object, InferenceUsage]]


class CoalescingEmbeddingModel:
    def __init__(
        self,
        model,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size <= 0:
            raise ValueError("max_batch_size must be positive")
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, int | float) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be nonnegative")
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be nonnegative")
        self.model = model
        self.profile = model.profile
        self.max_batch_size = max_batch_size
        self.coalesce_ms = coalesce_ms
        self.cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._work: dict[str, _Work] = {}
        self._pending: OrderedDict[str, _Work] = OrderedDict()
        self._drain_task: asyncio.Task[None] | None = None

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        positions: list[tuple[float, ...] | _Work] = []
        for text in texts:
            if text in self._cache:
                self._cache.move_to_end(text)
                positions.append(self._cache[text])
                continue
            work = self._work.get(text)
            if work is None:
                work = _Work(asyncio.get_running_loop().create_future())
                self._work[text] = work
                self._pending[text] = work
            positions.append(work)
        if self._pending and (self._drain_task is None or self._drain_task.done()):
            self._drain_task = asyncio.create_task(self._drain())
        pending = [item for item in positions if isinstance(item, _Work)]
        resolved = await asyncio.gather(*(asyncio.shield(item.future) for item in pending))
        resolved_iter = iter(resolved)
        vectors: list[tuple[float, ...]] = []
        batches: dict[object, InferenceUsage] = {}
        for item in positions:
            if isinstance(item, _Work):
                vector, batch_id, usage = next(resolved_iter)
                vectors.append(vector)
                batches[batch_id] = usage
            else:
                vectors.append(item)
        def token_sum(name: str) -> int | None:
            values = [getattr(usage, name) for usage in batches.values()]
            return sum(value for value in values if value is not None) if any(value is not None for value in values) else None
        usage = InferenceUsage(
            requests=sum(item.requests for item in batches.values()),
            input_tokens=token_sum("input_tokens"),
            output_tokens=token_sum("output_tokens"),
        )
        return EmbeddingResult(vectors=tuple(vectors), usage=usage)

    async def _drain(self) -> None:
        await asyncio.sleep(self.coalesce_ms / 1000)
        while self._pending:
            batch: list[tuple[str, _Work]] = []
            for _ in range(min(self.max_batch_size, len(self._pending))):
                batch.append(self._pending.popitem(last=False))
            asyncio.create_task(self._run_batch(batch))

    async def _run_batch(self, batch: list[tuple[str, _Work]]) -> None:
        identity = object()
        try:
            result = await self.model.embed(tuple(text for text, _ in batch))
            if len(result.vectors) != len(batch):
                raise ValueError("Embedding vector count does not match batch")
            for (_, work), vector in zip(batch, result.vectors, strict=True):
                work.future.set_result((tuple(vector), identity, result.usage))
            if self.cache_size:
                for (text, _), vector in zip(batch, result.vectors, strict=True):
                    self._cache[text] = tuple(vector)
                    self._cache.move_to_end(text)
                    if len(self._cache) > self.cache_size:
                        self._cache.popitem(last=False)
        except Exception as error:
            for _, work in batch:
                if not work.future.done():
                    work.future.set_exception(error)
        finally:
            for text, work in batch:
                if self._work.get(text) is work:
                    self._work.pop(text)
