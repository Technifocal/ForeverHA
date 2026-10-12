import hashlib
import os
import time
from pathlib import Path

from .logs import local_player, parse_line
from .lua_data import loads, LuaDataError

MAX_LINE = 256 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def rows(value):
    return value if isinstance(value, list) else list(value.values()) if isinstance(value, dict) else []


def envelope(installation, account, dataset, payload, source, observed=None, guid=None, build=None):
    return {"installation": installation, "account": account, "character_guid": guid,
            "dataset": dataset, "payload": payload, "source": source,
            "observed_at": observed, "client_build": build}


def batch_raw(envelopes):
    """Up to 128 records per envelope, so raid logs don't bottleneck at 50 lines/s."""
    result, group = [], []
    def flush():
        if not group:
            return
        if len(group) == 1:
            result.append(group[0])
        else:
            result.append(dict(group[-1], payload={"records": [e["payload"] for e in group]}))
        group.clear()
    for item in envelopes:
        if group and (len(group) == 128 or any(item[k] != group[0][k] for k in
                                              ("dataset", "character_guid", "account", "installation"))):
            flush()
        group.append(item)
    flush()
    return result


def snapshot(path, installation, account, checkpoint):
    stat = path.stat()
    if stat.st_size > 64 * 1024 * 1024:
        raise LuaDataError("SavedVariables exceeds 64 MiB")
    data = path.read_bytes()
    after = path.stat()
    if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return [], checkpoint  # Retry a write still in progress.
    fingerprint = digest(data)
    if checkpoint.get("hash") == fingerprint:
        return [], checkpoint
    db = loads(data.decode("utf-8-sig"))
    if not isinstance(db.get("characters", {}), dict) or not isinstance(db.get("settings", {}), dict):
        raise LuaDataError("Invalid SavedVariables structure")
    disabled = db.get("settings", {}).get("disabled", {})
    if not isinstance(disabled, dict):
        raise LuaDataError("Invalid disabled categories")
    out, event_marks = [], dict(checkpoint.get("event_marks", {}))
    for guid, character in db.get("characters", {}).items():
        if not isinstance(guid, str) or not guid.startswith("Player-") or not isinstance(character, dict):
            raise LuaDataError("Invalid character record")
        if not isinstance(character.get("datasets", {}), dict):
            raise LuaDataError("Invalid datasets")
        build = character.get("build")
        for name, record in character.get("datasets", {}).items():
            if not isinstance(record, dict):
                raise LuaDataError("Invalid dataset record")
            if disabled.get(name):
                record = {"status": "disabled", "checked_at": time.time()}
            out.append(envelope(installation, account, name, record, "saved_variables",
                                record.get("observed_at"), guid, build))
        if disabled.get("events"):
            out.append(envelope(installation, account, "addon_events", {"status": "disabled"},
                                "saved_variables", time.time(), guid, build))
            continue
        last = event_marks.get(guid, 0)
        for event in rows(character.get("events", [])):
            if not isinstance(event, dict) or type(event.get("sequence")) is not int:
                raise LuaDataError("Invalid event record")
            if event.get("sequence", 0) <= last:
                continue
            out.append(envelope(installation, account, "addon_events", event, "saved_variables",
                                event.get("observed_at"), guid, build))
            event_marks[guid] = max(event_marks.get(guid, 0), event.get("sequence", 0))
        out.append(envelope(installation, account, "export_status",
                            {"status": "available", "payload": {"dropped_addon_events": character.get("dropped_events", 0),
                              "saved_at": character.get("saved_at"), "name": character.get("name"), "realm": character.get("realm")}},
                            "saved_variables", character.get("saved_at"), guid, build))
    snapshots = [e for e in out if e["dataset"] != "addon_events"]
    snapshots.extend(batch_raw([e for e in out if e["dataset"] == "addon_events"]))
    return snapshots, {"hash": fingerprint, "event_marks": event_marks}


def tail(path, installation, account, kind, checkpoint, timezone=None, import_history=True):
    stat = path.stat()
    identity = [stat.st_dev, stat.st_ino]
    offset = checkpoint.get("offset", 0 if import_history else stat.st_size)
    generation = checkpoint.get("generation", 0)
    reset = checkpoint and (checkpoint.get("identity") != identity or stat.st_size < offset)
    with path.open("rb") as f:
        # An anchor detects copy/truncate followed by growth beyond the old offset.
        anchor_size = min(offset, 128)
        f.seek(offset - anchor_size if offset >= anchor_size else 0)
        anchor = digest(f.read(anchor_size))
        if checkpoint.get("anchor") and anchor != checkpoint["anchor"]:
            reset = True
        if reset:
            offset, generation = 0, generation + 1
        f.seek(offset)
        chunk = f.read(1024 * 1024)
        end = chunk.rfind(b"\n")
        skipping = checkpoint.get("skipping", False) and not reset
        out, consumed, oversized = [], 0, 0
        if end >= 0:
            complete = chunk[:end + 1]
            for raw in complete.splitlines(keepends=True):
                start = offset + consumed
                consumed += len(raw)
                if skipping:
                    skipping = False
                    continue
                if len(raw) > MAX_LINE:
                    oversized += 1
                    continue
                decoded = raw.rstrip(b"\r\n").decode("utf-8", errors="replace")
                record = parse_line(decoded, kind, stat.st_mtime, timezone)
                record.update(file=path.name, offset=start, generation=generation,
                              encoding_replaced="\ufffd" in decoded)
                out.append(envelope(installation, account, kind + "_log", record,
                                    kind + "_log", record.get("observed_at")))
        elif len(chunk) > MAX_LINE:
            consumed, skipping = len(chunk), True
            if not checkpoint.get("skipping"):
                oversized += 1
        new_offset = offset + consumed
        f.seek(max(0, new_offset - 128))
        new_anchor = digest(f.read(min(new_offset, 128)))
    out = batch_raw(out)
    if reset or oversized:
        out.append(envelope(installation, account, "file_status",
                            {"status": "partial", "payload": {"file": path.name, "rotation": bool(reset),
                              "oversized_lines_dropped": oversized}}, "diagnostics", time.time()))
    return out, {"identity": identity, "offset": new_offset, "anchor": new_anchor,
                 "generation": generation, "skipping": skipping}


def discover_roots():
    candidates = [Path("C:/Program Files (x86)/World of Warcraft"), Path("C:/Program Files/World of Warcraft"),
                  Path("/Applications/World of Warcraft"), Path.home() / "Games/World of Warcraft",
                  Path.home() / ".wine/drive_c/Program Files (x86)/World of Warcraft"]
    lutris = Path.home() / "Games/battlenet/drive_c/Program Files (x86)/World of Warcraft"
    candidates.append(lutris)
    found = []
    for root in candidates:
        if not root.is_dir():
            continue
        if (root / "WTF").is_dir():
            found.append(root)
        # Inspect local client metadata rather than treating every Classic install as Forever.
        info = root / ".build.info"
        text = info.read_text(errors="replace") if info.exists() else ""
        for folder in ("_forever_", "_camelot_", "_classic_beta_"):
            p = root / folder
            if p.is_dir() and (folder != "_classic_beta_" or "wow_classic_beta" in text):
                found.append(p)
    return found


def discover_files(root, accounts=None):
    account_root = root / "WTF/Account"
    if account_root.is_dir():
        for account in account_root.iterdir():
            if not account.is_dir() or (accounts and account.name not in accounts):
                continue
            p = account / "SavedVariables/ForeverHA.lua"
            if p.exists():
                yield p, "snapshot", account.name
    logs = root / "Logs"
    if logs.is_dir():
        for p in sorted(logs.iterdir()):
            if p.is_file() and p.suffix.lower() == ".txt":
                if p.name.startswith("WoWCombatLog"):
                    yield p, "combat", ""
                elif p.name.startswith("WoWChatLog"):
                    yield p, "chat", ""
