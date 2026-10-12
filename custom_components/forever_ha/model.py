"""Pure data model shared by runtime behavior and offline tests."""
import hashlib
import json
import math
import re
import time

RAW_DATASETS = {"combat_log", "chat_log", "addon_events"}
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_DATASET_BYTES = 4 * 1024 * 1024
MAX_RAW_BYTES = 2 * 1024 * 1024
MAX_RAW_RECORDS = 2000


def validate_json(value, depth=0):
    if depth > 32:
        raise ValueError("Payload nesting exceeds limit")
    if value is None or type(value) in (str, bool, int):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for v in value:
            validate_json(v, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for v in value.values():
            validate_json(v, depth + 1)
        return
    raise ValueError("Payload is not finite JSON data")


def validate(envelope):
    if not isinstance(envelope, dict) or envelope.get("version") != 1:
        raise ValueError("Unsupported envelope version")
    for field in ("installation", "session", "source", "dataset"):
        if not isinstance(envelope.get(field), str) or not 0 < len(envelope[field]) <= 100:
            raise ValueError("Missing or oversized identifier")
    if not NAME.fullmatch(envelope["dataset"]):
        raise ValueError("Invalid dataset name")
    if not isinstance(envelope.get("account"), str) or len(envelope["account"]) > 200:
        raise ValueError("Invalid account")
    guid = envelope.get("character_guid")
    if guid is not None and (not isinstance(guid, str) or not guid.startswith("Player-") or len(guid) > 100):
        raise ValueError("Invalid character GUID")
    seq = envelope.get("sequence")
    if type(seq) is not int or seq < 1:
        raise ValueError("Invalid sequence")
    for name in ("observed_at", "received_at"):
        stamp = envelope.get(name)
        if name == "received_at" and stamp is None:
            raise ValueError("Missing receipt timestamp")
        if stamp is not None and (type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0):
            raise ValueError("Invalid timestamp")
    validate_json(envelope)
    if len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_DATASET_BYTES + 8192:
        raise ValueError("Envelope exceeds limit")
    if not isinstance(envelope.get("payload"), dict) and envelope["dataset"] in RAW_DATASETS:
        raise ValueError("Raw record must be an object")
    if "payload" not in envelope:
        raise ValueError("Missing payload")
    if envelope["dataset"] in RAW_DATASETS and "records" in envelope["payload"]:
        records = envelope["payload"]["records"]
        if not isinstance(records, list) or not 1 <= len(records) <= 128 or not all(isinstance(r, dict) for r in records):
            raise ValueError("Invalid raw record batch")
    elif envelope["dataset"] not in RAW_DATASETS and not isinstance(envelope["payload"], dict):
        raise ValueError("Snapshot record must be an object")


def device_key(installation, guid=None, account=""):
    # GUIDs unify log records (no account in file) with account-specific snapshots.
    return json.dumps([installation, guid or "account:" + account], separators=(",", ":"))


def entity_prefix(key):
    return hashlib.sha256(key.encode()).hexdigest()[:20]


class Model:
    def __init__(self, data=None):
        if data and (data.get("version") != 1 or any(k not in data for k in ("devices", "watermarks", "bridges"))):
            raise ValueError("Unsupported integration storage schema; preserve storage and update the integration")
        self.data = data or {"version": 1, "devices": {}, "watermarks": {}, "bridges": {}}

    def device(self, installation, guid, account=""):
        key = device_key(installation, guid, account)
        if key not in self.data["devices"]:
            self.data["devices"][key] = {"installation": installation, "account": account,
                                          "guid": guid, "name": guid or "Game files", "datasets": {}}
        device = self.data["devices"][key]
        if account:
            device["account"] = account
        return key, device

    def apply(self, envelope):
        validate(envelope)
        installation, guid = envelope["installation"], envelope.get("character_guid")
        stream = json.dumps([installation, envelope["session"]], separators=(",", ":"))
        sequence = envelope["sequence"]
        # Companion is an ordered outbox. Retries never skip an unacknowledged batch.
        if sequence <= self.data["watermarks"].get(stream, 0):
            return "duplicate", []
        key, device = self.device(installation, guid, envelope["account"])
        dataset, payload = envelope["dataset"], envelope["payload"]
        events = []
        if dataset in RAW_DATASETS:
            if payload.get("status") != "disabled":
                records = payload.get("records", [payload])
                if not isinstance(records, list) or not 1 <= len(records) <= 128 or not all(isinstance(r, dict) for r in records):
                    raise ValueError("Invalid raw record batch")
                for record in records:
                    individual = dict(envelope, payload=record, observed_at=record.get("observed_at", envelope.get("observed_at")))
                    self._append(device, dataset, individual)
                    observed = individual.get("observed_at")
                    delay = individual["received_at"] - observed if observed is not None else None
                    events.append({"device_key": key, "dataset": dataset, "record": individual,
                                   "historical": delay is None or abs(delay) > 30,
                                   "observation_delay_seconds": delay})
                    if dataset == "combat_log":
                        self._combat(installation, record, individual)
            else:
                self._append(device, dataset, envelope)
        else:
            old = device["datasets"].get(dataset)
            stamp = envelope.get("observed_at")
            # checked_at orders unavailable/disabled states without pretending they were observed.
            order = payload.get("checked_at") if isinstance(payload, dict) else None
            order = order if type(order) in (int, float) else stamp
            order = order if order is not None else envelope["received_at"]
            if old and order < old.get("order_at", 0):
                result = "stale"
            else:
                device["datasets"][dataset] = dict(envelope, order_at=order)
                result = "accepted"
                if dataset == "character" and isinstance(payload, dict):
                    values = payload.get("payload", {}) or {}
                    if values.get("name"):
                        device["name"] = values["name"]
                    if values.get("realm"):
                        device["realm"] = values["realm"]
                if dataset == "export_status" and isinstance(payload, dict):
                    values = payload.get("payload", {}) or {}
                    device["name"] = values.get("name") or device["name"]
                    device["realm"] = values.get("realm") or device.get("realm")
                device["client_build"] = envelope.get("client_build") or device.get("client_build")
        self.data["watermarks"][stream] = sequence
        return ("accepted" if dataset in RAW_DATASETS else result), events

    def _append(self, device, dataset, envelope):
        old = device["datasets"].get(dataset, {"records": [], "retention_dropped": 0, "bytes": 0})
        if envelope["payload"].get("status") == "disabled":
            device["datasets"][dataset] = {"records": [], "retention_dropped": old.get("retention_dropped", 0),
                                             "bytes": 0, "status": "disabled"}
            return
        length = len(json.dumps(envelope, ensure_ascii=False).encode())
        old.setdefault("records", []).append(envelope)
        old["bytes"] = old.get("bytes", 0) + length
        old["status"] = "available"
        while len(old["records"]) > MAX_RAW_RECORDS or old["bytes"] > MAX_RAW_BYTES:
            first = old["records"].pop(0)
            old["bytes"] -= len(json.dumps(first, ensure_ascii=False).encode())
            old["retention_dropped"] = old.get("retention_dropped", 0) + 1
        old["observed_at"] = envelope.get("observed_at")
        old["received_at"] = envelope["received_at"]
        device["datasets"][dataset] = old

    def _combat(self, installation, record, envelope):
        if record.get("format") == "encounter":
            self._encounter(installation, record, envelope)
            return
        metric = record.get("metric")
        for prefix in ("source", "destination"):
            guid = record.get(prefix + "_guid", "")
            if not guid.startswith("Player-"):
                continue
            key = device_key(installation, guid)
            flags = record.get(prefix + "_flags")
            if key not in self.data["devices"] and not (type(flags) is int and flags & 1):
                continue
            _, device = self.device(installation, guid)
            device["name"] = record.get(prefix + "_name") or device["name"]
            stats = device["datasets"].setdefault("combat_statistics", {"payload": {}, "source": "combat_log"})
            # Counters describe only events seen in these files, never complete lifetime totals.
            values = stats["payload"]
            field = None
            if metric in ("damage", "healing"):
                field = metric + ("_done" if prefix == "source" else "_taken")
                values[field] = values.get(field, 0) + record.get("effective", record.get("amount", 0))
            elif metric in ("casts", "interrupts") and prefix == "source":
                field = metric
                values[field] = values.get(field, 0) + 1
            elif metric == "deaths" and prefix == "destination":
                field = metric
                values[field] = values.get(field, 0) + 1
            if record.get("statistics_unparsed"):
                values["unparsed_statistic_records"] = values.get("unparsed_statistic_records", 0) + 1
            if field or record.get("statistics_unparsed"):
                stats["observed_at"] = envelope.get("observed_at")
                stats["received_at"] = envelope["received_at"]
                stats["status"] = "partial" if values.get("unparsed_statistic_records") else "available"
                encounter = self.data.get("encounters", {}).get(installation)
                if encounter and (field or record.get("statistics_unparsed")):
                    totals = encounter.setdefault("characters", {}).setdefault(guid, {})
                    if field:
                        increment = record.get("effective", record.get("amount", 0)) if metric in ("damage", "healing") else 1
                        totals[field] = totals.get(field, 0) + increment
                    else:
                        totals["unparsed_statistic_records"] = totals.get("unparsed_statistic_records", 0) + 1

    def _encounter(self, installation, record, envelope):
        fields = record.get("encounter_fields", [])
        if len(fields) < 4:
            return
        _, device = self.device(installation, None)
        stats = device["datasets"].setdefault("encounter_statistics", {"payload": {}, "source": "combat_log"})
        values = stats["payload"]
        encounters = self.data.setdefault("encounters", {})
        stamp = envelope.get("observed_at")
        if record.get("event") == "ENCOUNTER_START":
            encounters[installation] = {"id": fields[0], "name": fields[1], "started_at": stamp, "characters": {}}
            values["encounters_started"] = values.get("encounters_started", 0) + 1
            values["active_encounter"] = fields[1]
        elif record.get("event") == "ENCOUNTER_END":
            values["encounters_completed"] = values.get("encounters_completed", 0) + 1
            values["last_encounter"] = fields[1]
            if len(fields) > 4 and fields[4] in ("1", "0"):
                values["last_success"] = fields[4] == "1"
                field = "encounters_won" if fields[4] == "1" else "encounters_failed"
                values[field] = values.get(field, 0) + 1
            active = encounters.pop(installation, {})
            values["active_encounter"] = None
            start = active.get("started_at")
            if active.get("id") == fields[0] and stamp is not None and start is not None and stamp > start:
                duration = stamp - start
                values["last_duration"] = duration
                for guid, totals in active.get("characters", {}).items():
                    _, character = self.device(installation, guid)
                    character["datasets"]["last_encounter"] = {
                        "payload": dict(totals, name=fields[1], duration=duration,
                                        dps=totals["damage_done"] / duration if "damage_done" in totals and not totals.get("unparsed_statistic_records") else None,
                                        hps=totals["healing_done"] / duration if "healing_done" in totals and not totals.get("unparsed_statistic_records") else None),
                        "source": "combat_log", "observed_at": stamp, "received_at": envelope["received_at"],
                        "status": "partial" if totals.get("unparsed_statistic_records") else "available"}
        stats.update(observed_at=stamp, received_at=envelope["received_at"], status="available")

    def heartbeat(self, installations, diagnostics, now=None):
        now = time.time() if now is None else now
        for installation in installations:
            self.data["bridges"][installation] = dict(diagnostics, last_heartbeat=now)
            self.device(installation, None)

    def connected(self, installation, now=None):
        return (time.time() if now is None else now) - self.data["bridges"].get(installation, {}).get("last_heartbeat", 0) < 35

    def get_dataset(self, installation, guid, dataset, account="", offset=0, limit=200):
        device = self.data["devices"].get(device_key(installation, guid, account))
        if not device or dataset not in device["datasets"]:
            return {"status": "unobserved"}
        record = device["datasets"][dataset]
        if dataset in RAW_DATASETS:
            return dict(record, records=record.get("records", [])[offset:offset + limit],
                        total=len(record.get("records", [])), offset=offset)
        return record
