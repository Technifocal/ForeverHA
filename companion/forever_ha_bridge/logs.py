"""Conservative combat parser. Unknown/advanced layouts remain raw, not guessed."""
import csv
import datetime as dt
import re
from zoneinfo import ZoneInfo

STAMP = re.compile(r"^(?P<stamp>\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\s+(?P<body>.*)$")


def number(s):
    try:
        return int(s, 0) if s.lower().startswith("0x") else int(s)
    except (ValueError, AttributeError):
        return None


def parse_time(stamp, mtime, timezone=None):
    date, clock = stamp.split()
    parts = date.split("/")
    guessed = len(parts) == 2
    zone = ZoneInfo(timezone) if timezone else dt.datetime.now().astimezone().tzinfo
    if guessed:
        year = dt.datetime.fromtimestamp(mtime, zone).year
        date += f"/{year}"
    elif len(parts[2]) == 2:
        date = "/".join(parts[:2]) + f"/{2000 + int(parts[2])}"
    parsed = dt.datetime.strptime(date + " " + clock, "%m/%d/%Y %H:%M:%S.%f" if "." in clock else "%m/%d/%Y %H:%M:%S")
    parsed = parsed.replace(tzinfo=zone)
    if guessed and parsed.timestamp() - mtime > 86400:
        parsed = parsed.replace(year=parsed.year - 1)
    return parsed.timestamp(), guessed


def parse_line(line, kind, mtime, timezone=None):
    record = {"raw": line, "format": "unknown"}
    match = STAMP.match(line)
    if not match:
        return record
    record["timestamp_text"] = match["stamp"]
    try:
        record["observed_at"], record["year_inferred"] = parse_time(match["stamp"], mtime, timezone)
    except (ValueError, OverflowError):
        record["timestamp_error"] = True
    if kind == "chat":
        record.update(format="chat", text=match["body"])
        return record
    try:
        fields = next(csv.reader([match["body"]], skipinitialspace=True, strict=True))
    except csv.Error:
        return record
    record["fields"] = fields
    if not fields:
        return record
    event = fields[0]
    record["event"] = event
    if event in ("ENCOUNTER_START", "ENCOUNTER_END"):
        record.update(format="encounter", encounter_fields=fields[1:])
        return record
    # Standard common prefix; restricted/different shapes are never coerced.
    if len(fields) < 10 or fields[1] not in ("true", "false", "0", "1"):
        return record
    record.update(format="combat", source_guid=fields[2], source_name=fields[3],
                  source_flags=number(fields[4]), destination_guid=fields[6],
                  destination_name=fields[7], destination_flags=number(fields[8]))
    payload = fields[10:]
    metric = None
    if event == "SWING_DAMAGE":
        metric = ("damage", 0, 1, 12)
    elif event in ("SPELL_DAMAGE", "SPELL_PERIODIC_DAMAGE", "RANGE_DAMAGE", "DAMAGE_SHIELD", "DAMAGE_SPLIT"):
        metric = ("damage", 3, 4, 15)
    elif event in ("SPELL_HEAL", "SPELL_PERIODIC_HEAL"):
        metric = ("healing", 3, 4, 7)
    if metric:
        name, amount_index, excess_index, minimum = metric
        # Advanced records may insert GUID/position metadata before amounts.
        # Only accept a standard-shaped prefix and numeric amounts/excess.
        expected_sizes = {"damage": (10, 11, 12, 13, 14, 15, 16), "healing": (7, 8)}
        amount = number(payload[amount_index]) if len(payload) > amount_index else None
        excess = number(payload[excess_index]) if len(payload) > excess_index else None
        correct_size = len(payload) in ((10, 11, 12, 13) if event == "SWING_DAMAGE" else
                                       ((13, 14, 15, 16) if name == "damage" else expected_sizes[name]))
        if correct_size and amount is not None and excess is not None and amount >= 0:
            record["metric"] = name
            record["amount"] = amount
            record["effective"] = max(0, amount - max(0, excess)) if name == "healing" else amount
        else:
            record["statistics_unparsed"] = True
    elif event in ("UNIT_DIED", "UNIT_DESTROYED", "SPELL_CAST_SUCCESS", "SPELL_INTERRUPT"):
        record["metric"] = {"UNIT_DIED": "deaths", "UNIT_DESTROYED": "deaths",
                            "SPELL_CAST_SUCCESS": "casts", "SPELL_INTERRUPT": "interrupts"}[event]
    return record


def local_player(record):
    for prefix in ("source", "destination"):
        flags = record.get(prefix + "_flags")
        guid = record.get(prefix + "_guid", "")
        if flags is not None and flags & 1 and guid.startswith("Player-"):
            return guid
    return None
