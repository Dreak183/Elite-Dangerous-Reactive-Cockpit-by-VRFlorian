from __future__ import annotations

import tkinter as tk

from .config import load_config
from .player_reader import PlayerReaderData


BG = "#080d13"
PANEL = "#0d1721"
BORDER = "#17384a"
CYAN = "#16b7ef"
WHITE = "#eef8fc"
MUTED = "#8396a3"
GREEN = "#48d68b"
AMBER = "#f0bd58"


class ReaderWindow:
    def __init__(self) -> None:
        config = load_config()["presentation"]
        self.source = PlayerReaderData()
        self.root = tk.Tk()
        self.root.title(str(config.get("window_title", "VRFlorian Reactive Cockpit — Story Mode ON")))
        self.root.geometry("820x300")
        self.root.minsize(680, 270)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", bool(config.get("always_on_top", True)))

        shell = tk.Frame(self.root, bg=BG, padx=22, pady=18)
        shell.pack(fill="both", expand=True)

        heading = tk.Frame(shell, bg=BG)
        heading.pack(fill="x")
        self.title = tk.Label(
            heading, text="LIVING WORLD", bg=BG, fg=CYAN,
            font=("Segoe UI Semibold", 11), anchor="w",
        )
        self.title.pack(side="left", fill="x", expand=True)
        tk.Label(
            heading, text="●  LIVE READER", bg=BG, fg=GREEN,
            font=("Segoe UI Semibold", 9), anchor="e",
        ).pack(side="right")

        self.clue = tk.Label(
            shell, text="Waiting for story graph…", bg=BG, fg=WHITE,
            justify="left", anchor="nw", wraplength=760,
            font=("Segoe UI Semibold", 15), pady=12,
        )
        self.clue.pack(fill="x")

        self.survey_panel = tk.Frame(
            shell, bg=PANEL, padx=14, pady=10,
            highlightbackground=BORDER, highlightthickness=1,
        )
        self.survey_panel.columnconfigure(0, weight=2)
        self.survey_panel.columnconfigure(1, weight=2)
        self.survey_panel.columnconfigure(2, weight=1)
        self.survey_rows: list[tuple[tk.Label, tk.Label, tk.Label]] = []
        for row_index in range(2):
            location = tk.Label(
                self.survey_panel, bg=PANEL, fg=WHITE, anchor="w",
                font=("Segoe UI Semibold", 10), pady=5,
            )
            window = tk.Label(
                self.survey_panel, bg=PANEL, fg=AMBER, anchor="w",
                font=("Segoe UI", 10), pady=5,
            )
            scans = tk.Label(
                self.survey_panel, bg=PANEL, fg=MUTED, anchor="e",
                font=("Segoe UI Semibold", 9), pady=5,
            )
            location.grid(row=row_index, column=0, sticky="ew", padx=(0, 12))
            window.grid(row=row_index, column=1, sticky="ew", padx=(0, 12))
            scans.grid(row=row_index, column=2, sticky="ew")
            self.survey_rows.append((location, window, scans))

        footer = tk.Frame(shell, bg=BG, pady=10)
        footer.pack(side="bottom", fill="x")
        self.cockpit = tk.Label(
            footer, text="COCKPIT  WAITING", bg=BG, fg=MUTED,
            font=("Segoe UI Semibold", 9), anchor="w",
        )
        self.cockpit.pack(side="left", fill="x", expand=True)
        self.scanner = tk.Label(
            footer, text="", bg=BG, fg=CYAN,
            font=("Segoe UI Semibold", 9), anchor="e",
        )
        self.scanner.pack(side="right")
        self.last_signature = ""
        self.last_survey_visible: bool | None = None

    @staticmethod
    def _scan_text(row: dict[str, object]) -> str:
        if row.get("done"):
            return "COMPLETE"
        total = int(row.get("total", 0))
        complete = int(row.get("complete", 0))
        return f"{complete} OF {total} SCANNED" if total else "AWAITING DATA"

    def render(self, view: dict[str, object]) -> None:
        signature = repr(view)
        if signature == self.last_signature:
            return
        self.last_signature = signature
        self.title.configure(text=str(view.get("title", "LIVING WORLD")).upper())
        self.clue.configure(text=str(view.get("clue", "Follow the current mission audio instructions.")))

        rows = view.get("survey", [])
        rows = rows if isinstance(rows, list) else []
        if rows:
            if not self.survey_panel.winfo_manager():
                self.survey_panel.pack(fill="x", pady=(0, 2), before=self.cockpit.master)
            for index, widgets in enumerate(self.survey_rows):
                if index >= len(rows) or not isinstance(rows[index], dict):
                    for widget in widgets:
                        widget.grid_remove()
                    continue
                row = rows[index]
                for widget in widgets:
                    widget.grid()
                widgets[0].configure(text=str(row.get("label", "LOCATION")))
                window = str(row.get("window", "CLOSED"))
                detail = f"{window}  ·  {row.get('change_label', '')} {row.get('change_in', 'unknown')}"
                widgets[1].configure(text=detail, fg=GREEN if window == "OPEN" else AMBER)
                widgets[2].configure(text=self._scan_text(row), fg=GREEN if row.get("done") else MUTED)
        else:
            self.survey_panel.pack_forget()
        survey_visible = bool(rows)
        if survey_visible != self.last_survey_visible:
            self.root.geometry("820x365" if survey_visible else "820x275")
            self.last_survey_visible = survey_visible

        state = str(view.get("cockpit_state", "WAITING"))
        theme = str(view.get("cockpit_theme", "Reactive Cockpit"))
        self.cockpit.configure(text=f"COCKPIT  {state}   ·   {theme}")
        if view.get("show_scanner"):
            scanner = str(view.get("scanner", "OFF"))
            self.scanner.configure(text=f"SCANNER  {scanner}", fg=GREEN if scanner == "ON" else MUTED)
        else:
            self.scanner.configure(text="")

    def refresh(self) -> None:
        try:
            self.render(self.source.read())
        except Exception:
            # Briefly locked runtime files must never interfere with the cockpit
            # or story engine. The next refresh retries automatically.
            pass
        self.root.after(300, self.refresh)

    def run(self) -> int:
        self.refresh()
        self.root.mainloop()
        return 0


def main() -> int:
    return ReaderWindow().run()


if __name__ == "__main__":
    raise SystemExit(main())
