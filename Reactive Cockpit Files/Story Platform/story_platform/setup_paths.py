from __future__ import annotations

import argparse
from pathlib import Path

from .config import default_journal_dir, load_config, save_config, validate_external_paths


def choose_folder() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        selected = filedialog.askdirectory(title="Select your existing Reactive Cockpit installation folder")
        root.destroy()
        return Path(selected) if selected else None
    except Exception:
        return None


def configure(source: Path | None = None) -> int:
    config = load_config()
    source = source or choose_folder()
    if not source:
        print("No folder selected. Run again with the folder path, for example:")
        print('  python platform.py configure "C:\\Users\\Commander\\Documents\\YOUR REACTIVE COCKPIT FOLDER"')
        return 2
    source = source.expanduser().resolve()
    core = source / "reactive_cockpit.py"
    cache = source / "theme_cache"
    if not core.is_file():
        print(f"Not a Reactive Cockpit installation: missing {core}")
        return 2
    if not (cache / "index.json").is_file():
        print(f"Theme cache is incomplete: missing {cache / 'index.json'}")
        return 2
    rc = config["reactive_cockpit"]
    rc["install_dir"] = str(source)
    rc["core_script"] = "reactive_cockpit.py"
    rc["theme_cache_dir"] = str(cache)
    bundled = source / "bundled_theme_cache"
    rc["bundled_theme_cache_dir"] = str(bundled) if bundled.is_dir() else ""
    edhm_library = Path.home() / "EDHM_UI" / "ODYSS" / "Themes"
    if edhm_library.is_dir():
        rc["edhm_theme_library_dir"] = str(edhm_library)
    journal = default_journal_dir()
    if journal.is_dir():
        config["journal_dir"] = str(journal)
    save_config(config)
    print("External files linked by path. Nothing was copied or modified.")
    report = validate_external_paths(config)
    for line in report.lines:
        print("  " + line)
    return 0 if report.ok else 1


def configure_media(source: Path | None = None) -> int:
    if source is None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk(); root.withdraw()
            selected = filedialog.askdirectory(title="Select the folder containing Mission One audio files")
            root.destroy()
            source = Path(selected) if selected else None
        except Exception:
            source = None
    if source is None:
        print("No story-media folder selected.")
        return 2
    source = source.expanduser().resolve()
    if not source.is_dir():
        print(f"Story-media folder does not exist: {source}")
        return 2
    config = load_config(); config["external_story_media_dir"] = str(source); save_config(config)
    print(f"Story media linked: {source}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?")
    args = parser.parse_args(argv)
    return configure(Path(args.folder) if args.folder else None)


if __name__ == "__main__":
    raise SystemExit(main())
