from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any


def canonical_id(source: str, payload: Any) -> str:
    packed = json.dumps([source, payload], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()[:24]


def ensure_signal_id(signal: dict[str, Any]) -> dict[str, Any]:
    if not signal.get("id"):
        signal["id"] = canonical_id(str(signal.get("source", "unknown")), {
            "name": signal.get("name"), "at": signal.get("at"), "data": signal.get("data", {})
        })
    return signal


class ExactDeduplicator:
    """Bounded exact-identity filter for raw source records.

    Elite occasionally writes byte-equivalent events with the same timestamp.
    Those are retained in the audit as ignored duplicates but never reach graph
    counters twice.
    """

    def __init__(self, capacity: int = 4096) -> None:
        self.capacity = capacity
        self.order: deque[str] = deque()
        self.known: set[str] = set()

    def accept(self, source: str, payload: Any) -> tuple[bool, str]:
        identity = canonical_id(source, payload)
        if identity in self.known:
            return False, identity
        self.known.add(identity)
        self.order.append(identity)
        while len(self.order) > self.capacity:
            self.known.discard(self.order.popleft())
        return True, identity

