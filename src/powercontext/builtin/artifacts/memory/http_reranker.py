"""HTTP score based Memory reranker with stable global candidate ranks."""

from __future__ import annotations

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.endpoint = endpoint
        self.client = client
        self.batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        if not candidates or limit < 1 or limit > len(candidates):
            raise ValueError("Invalid reranking candidate count or limit")
        scores: dict[int, float] = {}
        requests = 0
        for offset in range(0, len(candidates), self.batch_size):
            batch = tuple(enumerate(candidates[offset : offset + self.batch_size], start=offset + 1))
            response = await self.client.post(
                self.endpoint,
                json={"query": query, "candidates": [{"rank": rank, "text": hit.text} for rank, hit in batch]},
            )
            requests += 1
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as error:
                raise ValueError("Invalid reranking JSON") from error
            if not isinstance(payload, dict) or set(payload) != {"scores"} or not isinstance(payload["scores"], list):
                raise ValueError("Invalid reranking response schema")
            expected = {rank for rank, _ in batch}
            observed: set[int] = set()
            for item in payload["scores"]:
                if not isinstance(item, dict) or set(item) != {"rank", "score"}:
                    raise ValueError("Invalid reranking score record")
                rank, score = item["rank"], item["score"]
                if type(rank) is not int or rank not in expected or rank in observed:
                    raise ValueError("Invalid or duplicate reranking rank")
                if isinstance(score, bool) or not isinstance(score, int | float) or not math.isfinite(score):
                    raise ValueError("Reranking score must be finite")
                observed.add(rank)
                scores[rank] = float(score)
            if observed != expected:
                raise ValueError("Reranking response does not cover its batch")
        ordered = sorted(scores, key=lambda rank: (-scores[rank], rank))
        return MemoryRerankDecision(selected_ranks=tuple(ordered[:limit]), usage=InferenceUsage(requests=requests))
