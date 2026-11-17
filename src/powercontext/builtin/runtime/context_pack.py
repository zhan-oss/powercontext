"""Self-contained UTF-8 context packs and prefix deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass

_PACK = "powercontext.context-pack.v1"
_DELTA = "powercontext.context-pack-delta.v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ContextPackError(ValueError):
    """Invalid context pack or delta protocol data."""


def _wire(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContextPackError("Invalid Base64 item list")
    try:
        items = tuple(base64.b64decode(item, validate=True) for item in value)
        for item in items:
            item.decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError) as error:
        raise ContextPackError("Invalid UTF-8 Base64 item") from error
    if _encode(items) != value:
        raise ContextPackError("Noncanonical Base64 item")
    return items


def _check_digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ContextPackError("Invalid pack digest")
    return value


def _load(data: bytes, keys: set[str]) -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise ContextPackError("Invalid JSON wire data") from error
    if not isinstance(value, dict) or set(value) != keys:
        raise ContextPackError("Unexpected wire fields")
    return value


@dataclass(frozen=True, slots=True)
class ContextPack:
    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        if not isinstance(items, tuple) or any(not isinstance(item, bytes) for item in items):
            raise ContextPackError("Pack items must be bytes")
        for item in items:
            try:
                item.decode("utf-8")
            except UnicodeError as error:
                raise ContextPackError("Pack item is not valid UTF-8") from error
        unsigned = {"schema": _PACK, "items": _encode(items)}
        digest = "sha256:" + hashlib.sha256(_wire(unsigned)).hexdigest()
        count = 0
        while True:
            new_count = len(_wire({**unsigned, "digest": digest, "byte_count": count}))
            if new_count == count:
                break
            count = new_count
        return cls(items=items, digest=digest, byte_count=count)

    @classmethod
    def from_content(cls, content: str | None, *, chunk_bytes: int = 512) -> ContextPack:
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ContextPackError("Context content must be text")
        items: list[bytes] = []
        buffer = bytearray()
        for character in content:
            encoded = character.encode("utf-8")
            if len(encoded) > chunk_bytes:
                raise ContextPackError("Character exceeds chunk byte limit")
            if len(buffer) + len(encoded) > chunk_bytes:
                items.append(bytes(buffer))
                buffer.clear()
            buffer.extend(encoded)
        if buffer:
            items.append(bytes(buffer))
        return cls.build(tuple(items))

    def to_bytes(self) -> bytes:
        return _wire({"schema": _PACK, "items": _encode(self.items), "digest": self.digest,
                      "byte_count": self.byte_count})

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        value = _load(data, {"schema", "items", "digest", "byte_count"})
        if value["schema"] != _PACK or type(value["byte_count"]) is not int:
            raise ContextPackError("Invalid pack schema or size")
        items = _decode(value["items"])
        expected = cls.build(items)
        if value["digest"] != expected.digest or value["byte_count"] != expected.byte_count:
            raise ContextPackError("Pack digest or size mismatch")
        return expected


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        for left, right in zip(base.items, target.items):
            if left != right:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _wire({"schema": _DELTA, "base_digest": self.base_digest,
                      "target_digest": self.target_digest, "prefix_items": self.prefix_items,
                      "appended": _encode(self.appended)})

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        value = _load(data, {"schema", "base_digest", "target_digest", "prefix_items", "appended"})
        if value["schema"] != _DELTA or type(value["prefix_items"]) is not int or value["prefix_items"] < 0:
            raise ContextPackError("Invalid delta schema or prefix")
        return cls(_check_digest(value["base_digest"]), _check_digest(value["target_digest"]),
                   value["prefix_items"], _decode(value["appended"]))


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    if delta.base_digest != base.digest:
        raise ContextPackError("Delta base digest mismatch")
    if type(delta.prefix_items) is not int or not 0 <= delta.prefix_items <= len(base.items):
        raise ContextPackError("Delta prefix out of bounds")
    target = ContextPack.build(base.items[:delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("Delta target digest mismatch")
    return target
