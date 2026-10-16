from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


_REPLACE_TIMEOUT_SECONDS = 2.0
_REPLACE_INITIAL_DELAY_SECONDS = 0.01
_REPLACE_MAX_DELAY_SECONDS = 0.10


def _replace_with_retry(source: str, destination: Path) -> None:
    """Replace atomically while tolerating brief Windows reader locks."""
    deadline = time.monotonic() + _REPLACE_TIMEOUT_SECONDS
    delay = _REPLACE_INITIAL_DELAY_SECONDS
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, _REPLACE_MAX_DELAY_SECONDS)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_retry(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
