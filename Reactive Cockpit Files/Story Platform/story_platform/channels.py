from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


class JsonlTail:
    def __init__(self, path: Path, from_end: bool = True) -> None:
        self.path = path
        self.position = path.stat().st_size if from_end and path.exists() else 0
        self.remainder = ""

    def poll(self) -> Iterator[dict[str, Any]]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if size < self.position:
            self.position = 0
            self.remainder = ""
        with self.path.open("r", encoding="utf-8", errors="replace") as stream:
            stream.seek(self.position)
            chunk = stream.read()
            self.position = stream.tell()
        if not chunk:
            return
        text = self.remainder + chunk
        lines = text.splitlines(keepends=True)
        self.remainder = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self.remainder = lines.pop()
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value

