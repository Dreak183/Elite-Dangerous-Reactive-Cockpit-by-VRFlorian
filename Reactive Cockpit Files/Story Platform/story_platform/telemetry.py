from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .signals import ensure_signal_id


FLAG_NAMES = {
    0: "docked", 1: "landed", 2: "landing_gear_down", 3: "shields_up",
    4: "supercruise", 6: "hardpoints_deployed", 9: "cargo_scoop_deployed",
    10: "silent_running", 11: "fuel_scooping", 19: "low_fuel", 20: "overheating",
    21: "has_lat_long", 22: "in_danger", 23: "being_interdicted", 24: "in_main_ship",
    25: "in_fighter", 26: "in_srv", 27: "analysis_mode", 28: "night_vision",
}

FLAG2_NAMES = {
    0: "on_foot", 1: "in_taxi", 2: "in_multicrew", 3: "on_foot_in_station",
    4: "on_foot_on_planet", 5: "aim_down_sight", 6: "low_oxygen", 7: "low_health",
    8: "cold", 9: "hot", 10: "very_cold", 11: "very_hot", 12: "glide_mode",
    13: "on_foot_in_hangar", 14: "on_foot_social_space", 15: "on_foot_exterior",
    16: "breathable_atmosphere", 17: "telepresence_multicrew", 18: "physical_multicrew",
    19: "fsd_hyperdrive_charging",
}


LIVE_LOCATION_EVENTS = frozenset({"Location", "FSDJump", "CarrierJump"})

# These records can be written while Elite is opening, sitting at its menu, or
# shutting down.  They are fresh journal records, but they do not prove that a
# commander is in a live gameplay session yet.
NON_GAMEPLAY_JOURNAL_EVENTS = frozenset({
    "Fileheader", "Commander", "LoadGame", "Shutdown",
})


def is_live_location_event(event: dict[str, Any]) -> bool:
    """Return true only for a fresh absolute location reported by Elite.

    Cached journal hydration deliberately reuses these records without signals.
    The live runtime gate calls this helper only for newly tailed records, so a
    remembered location or a menu-time Status.json refresh cannot start audio.
    """
    name = str(event.get("event") or "")
    system = str(event.get("StarSystem") or "").strip()
    return name in LIVE_LOCATION_EVENTS and bool(system)


def is_live_journal_telemetry(event: dict[str, Any]) -> bool:
    """Return true for a newly tailed journal record that proves live play.

    ``JournalTail`` supplies only records written after its startup boundary,
    so this function does not need to detect cached history.  It only excludes
    the known session/menu lifecycle records that can arrive before a commander
    is actually in the game.
    """
    name = str(event.get("event") or "").strip()
    if not name or name in NON_GAMEPLAY_JOURNAL_EVENTS:
        return False
    if name == "Music":
        track = "".join(character for character in str(event.get("MusicTrack") or "").casefold() if character.isalnum())
        if track in {"", "mainmenu"}:
            return False
    return True


def is_live_status_update(current: dict[str, Any], previous: dict[str, Any] | None) -> bool:
    """Return true when Status.json changed meaningfully after its baseline.

    The first Status.json read is hydration only: it can be an old gameplay
    snapshot or a menu-time rewrite.  A later content change is fresh Elite
    telemetry and safely recovers a Story Platform restart in the middle of a
    mission.  Timestamp-only rewrites remain inert so the menu cannot open the
    story gate by merely touching Status.json.
    """
    if previous is None:
        return False
    current_live = {key: value for key, value in current.items() if key != "timestamp"}
    previous_live = {key: value for key, value in previous.items() if key != "timestamp"}
    return current_live != previous_live


def utc_from_payload(payload: dict[str, Any]) -> str:
    return str(payload.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(flatten(item, child))
    else:
        result[prefix] = value
    return result


class WorldModel:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {
            "location": {}, "vehicle": "unknown", "flags": {}, "flags2": {},
            "status": {}, "last_journal": {}, "inventory": {}, "location_updated_at": None,
        }

    def journal_signal(self, event: dict[str, Any]) -> dict[str, Any]:
        name = str(event.get("event") or "Unknown")
        self.data["last_journal"] = event
        location = self.data["location"]
        if name in {"Location", "FSDJump", "CarrierJump"}:
            location.clear()
            self.data["location_updated_at"] = None
        elif name == "Undocked":
            location.pop("station", None)
            location.pop("market_id", None)
        elif name in {"LeaveBody", "SupercruiseEntry"}:
            for key in ("settlement", "latitude", "longitude", "altitude", "heading", "body_radius_m"):
                location.pop(key, None)
            self.data["location_updated_at"] = None
        incoming_body = event.get("Body") or event.get("BodyName")
        if incoming_body and location.get("body") and incoming_body != location.get("body"):
            for key in ("latitude", "longitude", "altitude", "heading", "body_radius_m", "settlement"):
                location.pop(key, None)
            self.data["location_updated_at"] = None
        for source, target in (("StarSystem", "system"), ("Body", "body"), ("BodyName", "body"),
                               ("StationName", "station"), ("Name", "name"), ("MarketID", "market_id")):
            if source in event and name in {"Location", "FSDJump", "CarrierJump", "ApproachBody", "ApproachSettlement", "Docked", "SupercruiseExit", "SupercruiseDestinationDrop"}:
                location[target] = event[source]
        # ApproachSettlement publishes the settlement target's coordinates,
        # not the player's live position. Status.json remains authoritative for
        # the craft/on-foot location and prevents premature geofence entry.
        if name != "ApproachSettlement":
            for source, target in (("Latitude", "latitude"), ("Longitude", "longitude"), ("Altitude", "altitude"), ("Heading", "heading")):
                if isinstance(event.get(source), (int, float)):
                    location[target] = event[source]
            if isinstance(event.get("Latitude"), (int, float)) and isinstance(event.get("Longitude"), (int, float)):
                self.data["location_updated_at"] = utc_from_payload(event)
        if name == "ApproachSettlement":
            location["settlement"] = event.get("Name") or event.get("Name_Localised")
        elif name in {"LeaveBody", "SupercruiseEntry"}:
            location.pop("settlement", None)
        if name == "Disembark":
            self.data["vehicle"] = "on_foot"
            # Elite commonly omits Altitude from on-foot Status records. A
            # cached ship altitude must not reject a valid surface geofence.
            location.pop("altitude", None)
        elif name == "Embark":
            self.data["vehicle"] = str(event.get("SRV") and "srv" or "ship")
        elif name == "LaunchSRV":
            self.data["vehicle"] = "srv"
        elif name == "DockSRV":
            self.data["vehicle"] = "ship"
        elif name == "LaunchFighter":
            self.data["vehicle"] = "fighter"
        elif name == "DockFighter":
            self.data["vehicle"] = "ship"
        radius = event.get("Radius") or event.get("BodyRadius") or event.get("PlanetRadius")
        event_body = event.get("BodyName") or event.get("Body")
        if isinstance(radius, (int, float)) and (not event_body or event_body == location.get("body")):
            location["body_radius_m"] = float(radius)
        if name in {"Backpack", "BackpackChange", "ShipLocker", "CollectItems"}:
            self.data["inventory"]["last_change"] = event
        return ensure_signal_id({"name": f"journal.{name}", "source": "journal", "at": utc_from_payload(event), "data": event})

    def status_signals(self, status: dict[str, Any], previous: dict[str, Any] | None) -> list[dict[str, Any]]:
        flags = int(status.get("Flags", 0) or 0)
        flags2 = int(status.get("Flags2", 0) or 0)
        self.data["flags"] = {name: bool(flags & (1 << bit)) for bit, name in FLAG_NAMES.items()}
        self.data["flags2"] = {name: bool(flags2 & (1 << bit)) for bit, name in FLAG2_NAMES.items()}
        if self.data["flags2"].get("on_foot"):
            self.data["vehicle"] = "on_foot"
        elif self.data["flags"].get("in_srv"):
            self.data["vehicle"] = "srv"
        elif self.data["flags"].get("in_fighter"):
            self.data["vehicle"] = "fighter"
        elif self.data["flags"].get("in_main_ship"):
            self.data["vehicle"] = "ship"
        self.data["status"] = status
        location = self.data["location"]
        if status.get("BodyName"):
            location["body"] = status["BodyName"]
        for key, target in (("Latitude", "latitude"), ("Longitude", "longitude"), ("Altitude", "altitude"), ("Heading", "heading")):
            if key in status:
                location[target] = status[key]
        if (
            "has_lat_long" in self.data["flags"]
            and not self.data["flags"]["has_lat_long"]
            and self.data["vehicle"] != "on_foot"
        ):
            # Elite removes the surface-position fields once a ship leaves
            # planetary navigation. Do not retain the last low altitude: an
            # altitude-bounded environment would otherwise remain active in
            # space until a separate LeaveBody/SupercruiseEntry event arrives.
            for key in ("latitude", "longitude", "altitude", "heading"):
                location.pop(key, None)
            self.data["location_updated_at"] = None
        if self.data["vehicle"] == "on_foot" and "Altitude" not in status:
            location.pop("altitude", None)
        now = utc_from_payload(status)
        if isinstance(status.get("Latitude"), (int, float)) and isinstance(status.get("Longitude"), (int, float)):
            self.data["location_updated_at"] = now
        if previous is None:
            return [ensure_signal_id({"name": "status.snapshot", "source": "status", "at": now, "data": {"status": status, "changed": {}}})]
        old_flat, new_flat = flatten(previous), flatten(status)
        changes = {key: {"old": old_flat.get(key), "new": value} for key, value in new_flat.items() if old_flat.get(key) != value}
        if not changes:
            return []
        signals = [ensure_signal_id({"name": "status.changed", "source": "status", "at": now, "data": {"status": status, "changed": changes}})]
        meaningful = {"Flags", "Flags2", "GuiFocus", "SelectedWeapon", "LegalState", "Health", "Oxygen", "BodyName"}
        for path, change in changes.items():
            if path in meaningful:
                signals.append(ensure_signal_id({"name": f"status.{path}.changed", "source": "status", "at": now, "data": {"path": path, **change}}))
        previous_flags2 = int(previous.get("Flags2", 0) or 0)
        previous_glide = bool(previous_flags2 & (1 << 12))
        current_glide = bool(flags2 & (1 << 12))
        if current_glide != previous_glide:
            transition = "entered" if current_glide else "exited"
            signals.append(ensure_signal_id({
                "name": f"status.glide.{transition}",
                "source": "status",
                "at": now,
                "data": {
                    "active": current_glide,
                    "old": previous_glide,
                    "new": current_glide,
                    "path": "Flags2.glide_mode",
                },
            }))
        return signals


def status_age_seconds(status: dict[str, Any]) -> float | None:
    value = status.get("timestamp")
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds())


def journal_session_order(path: Path) -> tuple[datetime, int, str] | None:
    """Compare Elite's legacy YYMMDD and current YYYY-MM-DD session names.

    File modification time is deliberately not used: copying/restoring an old
    journal must not make it take over the live session. Unknown names are
    ignored rather than guessed to be current.
    """
    name = path.name
    current = re.fullmatch(r"Journal\.(\d{4})-(\d{2})-(\d{2})T(\d{2})(\d{2})(\d{2})\.(\d+)\.log", name, re.I)
    legacy = re.fullmatch(r"Journal\.(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\.(\d+)\.log", name, re.I)
    match = current or legacy
    if match is None:
        return None
    parts = list(map(int, match.groups()))
    if legacy:
        parts[0] += 2000
    try:
        session = datetime(*parts[:6])
    except ValueError:
        return None
    return session, parts[6], name.casefold()


def latest_journal(journal_dir: Path) -> Path | None:
    candidates = []
    for path in journal_dir.glob("Journal.*.log"):
        order = journal_session_order(path)
        if order is not None and path.is_file():
            candidates.append((order, path))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


class JournalTail:
    def __init__(self, journal_dir: Path, from_end: bool = True) -> None:
        self.journal_dir = journal_dir
        self.from_end = from_end
        self.path: Path | None = None
        self.stream = None

    def _latest(self) -> Path | None:
        return latest_journal(self.journal_dir)

    def _open(self, path: Path) -> None:
        if self.stream:
            self.stream.close()
        self.path = path
        self.stream = path.open("r", encoding="utf-8", errors="replace")
        if self.from_end:
            self.stream.seek(0, 2)
            self.from_end = False

    def arm(self) -> bool:
        """Establish the no-replay boundary before runtime hydration begins.

        An existing journal is opened at EOF.  If Elite has not created a
        journal yet, the first future journal is read from its beginning because
        every record in that new file was generated after Story Platform began.
        """
        if self.stream is not None:
            return True
        latest = self._latest()
        if latest is not None:
            self._open(latest)
            return True
        self.from_end = False
        return False

    def poll(self) -> Iterator[dict[str, Any]]:
        latest = self._latest()
        if latest and latest != self.path:
            self._open(latest)
        if not self.stream:
            return
        while True:
            line = self.stream.readline()
            if not line:
                break
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value

    def close(self) -> None:
        if self.stream:
            self.stream.close()


def seed_world_from_latest_journal(journal_dir: Path, world: WorldModel) -> int:
    """Rebuild current location/vehicle context before tailing new events.

    The latest session journal is replayed from its most recent absolute
    location anchor. Signals are deliberately discarded: this hydrates state
    without replaying story triggers.
    """
    journal = latest_journal(journal_dir)
    if journal is None:
        return 0
    events: list[dict[str, Any]] = []
    try:
        for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
    except (OSError, PermissionError):
        return 0
    anchors = {"Location", "FSDJump", "CarrierJump"}
    start = max((index for index, row in enumerate(events) if row.get("event") in anchors), default=max(0, len(events) - 500))
    replayed = 0
    for event in events[start:]:
        world.journal_signal(event)
        replayed += 1
    return replayed


class StatusWatcher:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.previous: dict[str, Any] | None = None
        self.last_text = ""

    def poll(self) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, PermissionError):
            return None
        if text == self.last_text:
            return None
        try:
            current = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(current, dict):
            return None
        previous = self.previous
        self.previous = current
        self.last_text = text
        return current, previous

    def discard_current_as_stale(self) -> None:
        self.previous = None
