import os
from pathlib import Path
from forever_ha_bridge.sources import tail, snapshot, discover_files, MAX_LINE
from forever_ha_bridge.store import Store
from forever_ha_bridge.cli import poll
from forever_ha_bridge.logs import parse_line, local_player


COMBAT = '10/11/2026 19:00:00.123  SPELL_DAMAGE,false,Player-1-ABC,"Tester",0x511,0x0,Creature-1-XYZ,"Target",0x10a48,0x0,133,"Fireball",4,100,-1,4,0,0,0,false,false,false,false\n'


def test_standard_damage_and_unknown_advanced_layout():
    record = parse_line(COMBAT.strip(), "combat", 1791763200, "America/Los_Angeles")
    assert record["amount"] == 100
    assert local_player(record) == "Player-1-ABC"
    advanced = COMBAT.replace('4,100,-1', '4,Player-1-ABC,100,-1')
    parsed = parse_line(advanced.strip(), "combat", 1791763200)
    assert "amount" not in parsed
    assert parsed["statistics_unparsed"]
    assert parsed["raw"] == advanced.strip()


def test_healing_and_encounters():
    line = COMBAT.split(',133,')[0] + ',133,"Heal",2,100,20,0,false\n'
    record = parse_line(line.replace("SPELL_DAMAGE", "SPELL_HEAL").strip(), "combat", 1791763200)
    assert record["effective"] == 80
    record = parse_line('10/11/2026 19:00:00.000  ENCOUNTER_END,1,"Boss",1,5,1', "combat", 1791763200)
    assert record["format"] == "encounter"
    death = COMBAT.split(',133,')[0].replace("SPELL_DAMAGE", "UNIT_DIED")
    assert parse_line(death, "combat", 1791763200)["metric"] == "deaths"


def test_renaming_a_log_does_not_reimport_read_records(tmp_path):
    logs = tmp_path / "Logs"
    logs.mkdir()
    path = logs / "WoWCombatLog.txt"
    path.write_text(COMBAT)
    config = {"installations": [{"id": "pc", "path": tmp_path}]}
    store = Store(tmp_path / "queue.db")
    assert not poll(config, store)
    count = len(store.batch())
    path.rename(logs / "WoWCombatLog-old.txt")
    assert not poll(config, store)
    assert len(store.batch()) == count
    store.close()


def test_raid_log_is_batched(tmp_path):
    path = tmp_path / "WoWCombatLog.txt"
    path.write_text(COMBAT * 256)
    records, cp = tail(path, "pc", "", "combat", {})
    assert len(records) == 2
    assert len(records[0]["payload"]["records"]) == 128
    assert cp["offset"] == path.stat().st_size


def test_partial_unicode_line_and_restart_checkpoint(tmp_path):
    path = tmp_path / "WoWChatLog.txt"
    data = '10/11/2026 19:00:00.000  [Guild] Éowyn: hello\n'.encode()
    path.write_bytes(data[:-2])
    records, cp = tail(path, "pc", "", "chat", {})
    assert not records and cp["offset"] == 0
    with path.open("ab") as f:
        f.write(data[-2:])
    records, cp = tail(path, "pc", "", "chat", cp)
    assert len(records) == 1 and "Éowyn" in records[0]["payload"]["text"]
    store = Store(tmp_path / "state.db")
    store.ingest("cursor", cp, records)
    store.close()
    store = Store(tmp_path / "state.db")
    records, new = tail(path, "pc", "", "chat", store.get("cursor"))
    assert not records and new["offset"] == len(data)
    assert len(store.batch()) == 1
    store.close()


def test_truncation_replacement_and_copytruncate_growth(tmp_path):
    path = tmp_path / "WoWCombatLog.txt"
    path.write_text(COMBAT)
    _, cp = tail(path, "pc", "", "combat", {})
    path.write_text(COMBAT.replace('"Tester"', '"Other"') * 2)
    records, cp2 = tail(path, "pc", "", "combat", cp)
    assert cp2["generation"] == 1
    assert records[-1]["payload"]["payload"]["rotation"]
    replacement = tmp_path / "replacement.txt"
    replacement.write_text(COMBAT)
    os.replace(replacement, path)
    records, cp3 = tail(path, "pc", "", "combat", cp2)
    assert cp3["generation"] == 2


def test_oversized_line_does_not_block_following_lines(tmp_path):
    path = tmp_path / "WoWChatLog.txt"
    path.write_bytes(b"x" * (MAX_LINE + 10))
    records, cp = tail(path, "pc", "", "chat", {})
    assert cp["skipping"]
    with path.open("ab") as f:
        f.write(b"tail\n10/11/2026 19:00:00.000  hello\n")
    records, cp = tail(path, "pc", "", "chat", cp)
    assert len(records) == 1 and records[0]["payload"]["text"] == "hello"
    assert not cp["skipping"]


def test_snapshot_events_deduplicate_across_reloads_and_accounts(tmp_path):
    path = tmp_path / "ForeverHA.lua"
    prefix = 'ForeverHADB={schema=1,characters={["Player-1-ABC"]={name="Tester", datasets={character={status="available",observed_at=100,payload={level=60}}}, events={{sequence=1,event="PLAYER_DEAD",observed_at=100}}}}}'
    path.write_text(prefix)
    records, cp = snapshot(path, "pc", "ACCOUNT1", {})
    assert len([r for r in records if r["dataset"] == "addon_events"]) == 1
    path.write_text(prefix + " -- another save")
    records, _ = snapshot(path, "pc", "ACCOUNT1", cp)
    assert not [r for r in records if r["dataset"] == "addon_events"]
    account = tmp_path / "WTF/Account/ACCOUNT2/SavedVariables"
    account.mkdir(parents=True)
    (account / "ForeverHA.lua").write_text(prefix)
    assert list(discover_files(tmp_path))[0][2] == "ACCOUNT2"


def test_queue_overflow_ack_and_atomic_failure(tmp_path, env):
    store = Store(tmp_path / "state.db", max_rows=2)
    store.ingest("cursor", {"offset": 100}, [env(), env(), env()])
    assert store.get("dropped_records") == 1
    assert len(store.batch()) == 2
    assert store.get("cursor")["offset"] == 100
    store.ack(store.batch())
    assert not store.batch()
    try:
        store.ingest("cursor", {"offset": 200}, [dict(env(), payload=object())])
    except TypeError:
        pass
    assert store.get("cursor")["offset"] == 100
    assert not store.batch()
    store.close()


def test_malformed_structure_is_reported_without_checkpointing(tmp_path):
    account = tmp_path / "WTF/Account/ACCOUNT/SavedVariables"
    account.mkdir(parents=True)
    path = account / "ForeverHA.lua"
    path.write_text('ForeverHADB={schema=1,characters="bad"}')
    store = Store(tmp_path / "queue.db")
    problems = poll({"installations": [{"id": "pc", "path": tmp_path}]}, store)
    assert problems[0]["error"] == "LuaDataError"
    assert not store.batch()
    assert path.read_text() == 'ForeverHADB={schema=1,characters="bad"}'
    store.close()
