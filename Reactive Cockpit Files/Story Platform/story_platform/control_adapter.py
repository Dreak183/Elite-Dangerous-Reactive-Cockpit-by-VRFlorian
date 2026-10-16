from __future__ import annotations

import ctypes
import os
import signal
import time
from pathlib import Path
from typing import Any

from .config import APP_DIR, load_config
from .engine import utc_now
from .signals import ensure_signal_id
from .storage import append_jsonl


VK = {"CTRL": 0x11, "SHIFT": 0x10, "ALT": 0x12, "SPACE": 0x20}


def binding_codes(binding: str) -> list[int]:
    codes: list[int] = []
    for token in binding.upper().split("+"):
        token = token.strip()
        if token in VK:
            codes.append(VK[token])
        elif len(token) == 1 and token.isalnum():
            codes.append(ord(token))
        else:
            raise ValueError(f"Unsupported control token: {token}")
    if not codes:
        raise ValueError("Empty control binding")
    return codes


def emit_control(action: str, binding: str = "INJECTED", path: Path | None = None) -> dict[str, Any]:
    signal_row = ensure_signal_id({
        "name": f"control.{action}", "source": "control", "at": utc_now(),
        "data": {"action": action, "binding": binding}
    })
    append_jsonl(path or (APP_DIR / "runtime" / "control_inbox.jsonl"), signal_row)
    return signal_row


def main() -> int:
    root_config = load_config()
    config = root_config["controls"]
    if not config.get("enabled", True):
        print("[CONTROL] Disabled by platform_config.json", flush=True)
        return 0
    bindings = {name: str(value) for name, value in config.get("bindings", {}).items()}
    developer_config = root_config.get("developer", {})
    developer_bindings = (
        {f"developer.{name}": str(value) for name, value in developer_config.get("bindings", {}).items()}
        if developer_config.get("enabled", False) else {}
    )
    all_bindings = {**{f"control.{name}": value for name, value in bindings.items()}, **developer_bindings}
    parsed = {name: binding_codes(value) for name, value in all_bindings.items()}
    print("[CONTROL] Generic controls active:", flush=True)
    for name, value in bindings.items():
        print(f"  {value:<10} -> control.{name}", flush=True)
    if os.name != "nt":
        print("[CONTROL] Global key polling is Windows-only; platform.py inject remains available.", flush=True)
        while True:
            time.sleep(1)
    user32 = ctypes.windll.user32
    running = True

    def stop(*_: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    previous = {name: False for name in parsed}
    last_emit = {name: 0.0 for name in parsed}
    poll = float(config.get("poll_seconds", 0.03))
    while running:
        now = time.monotonic()
        for signal_name, codes in parsed.items():
            down = all(bool(user32.GetAsyncKeyState(code) & 0x8000) for code in codes)
            if down and not previous[signal_name] and now - last_emit[signal_name] >= 0.25:
                binding = all_bindings[signal_name]
                if signal_name.startswith("control."):
                    action = signal_name.removeprefix("control.")
                    emit_control(action, binding)
                    print(f"[CONTROL] {binding} -> {signal_name}", flush=True)
                else:
                    signal_row = ensure_signal_id({
                        "name": signal_name, "source": "control", "at": utc_now(),
                        "data": {"action": signal_name.removeprefix("developer."), "binding": binding},
                    })
                    append_jsonl(APP_DIR / "runtime" / "control_inbox.jsonl", signal_row)
                last_emit[signal_name] = now
            previous[signal_name] = down
        time.sleep(poll)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
