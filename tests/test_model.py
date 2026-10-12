import copy
import pytest


def test_snapshots_staleness_duplicates_and_restart(model_module, env):
    model = model_module.Model()
    assert model.apply(env())[0] == "accepted"
    assert model.apply(env())[0] == "duplicate"
    assert model.apply(env(seq=2, stamp=90))[0] == "stale"
    model = model_module.Model(copy.deepcopy(model.data))
    assert model.apply(env(seq=2))[0] == "duplicate"
    assert model.get_dataset("pc", "Player-1-ABC", "character")["observed_at"] == 100


def test_logs_and_snapshots_unify_character_device_and_stats(model_module, env):
    model = model_module.Model()
    model.apply(env())
    raw = {"source_guid": "Player-1-ABC", "source_name": "Tester", "source_flags": 0x511,
           "destination_guid": "Creature-1-XYZ", "metric": "damage", "amount": 50, "effective": 50}
    message = env(seq=2, dataset="combat_log", payload=raw, account="")
    model.apply(message)
    assert len(model.data["devices"]) == 1
    assert model.get_dataset("pc", "Player-1-ABC", "combat_statistics")["payload"]["damage_done"] == 50
    assert model.apply(message)[0] == "duplicate"
    assert model.get_dataset("pc", "Player-1-ABC", "combat_statistics")["payload"]["damage_done"] == 50


def test_accounts_installations_and_switching(model_module, env):
    model = model_module.Model()
    model.apply(env())
    model.apply(env(seq=2, guid="Player-1-DEF", account="OTHER"))
    model.apply(env(guid="Player-1-ABC", installation="pc2"))
    assert len(model.data["devices"]) == 3
    assert model.get_dataset("pc", "Player-1-DEF", "character")["account"] == "OTHER"


def test_raw_pagination_retention_and_disable(model_module, env, monkeypatch):
    monkeypatch.setattr(model_module, "MAX_RAW_RECORDS", 2)
    model = model_module.Model()
    for seq in range(1, 4):
        model.apply(env(seq=seq, dataset="chat_log", payload={"text": str(seq)}))
    result = model.get_dataset("pc", "Player-1-ABC", "chat_log", offset=1, limit=1)
    assert result["total"] == 2
    assert result["retention_dropped"] == 1
    assert result["records"][0]["payload"]["text"] == "3"
    model.apply(env(seq=4, dataset="chat_log", payload={"status": "disabled"}))
    assert model.get_dataset("pc", "Player-1-ABC", "chat_log")["status"] == "disabled"


def test_silent_log_does_not_imply_character_online(model_module):
    model = model_module.Model()
    model.heartbeat(["pc"], {"queued_records": 0}, now=100)
    assert model.connected("pc", now=101)
    assert not model.connected("pc", now=140)
    assert "online" not in next(iter(model.data["devices"].values()))


def test_encounter_totals_duration_and_no_fabricated_hps(model_module, env):
    model = model_module.Model()
    model.apply(env(seq=1, dataset="combat_log", guid=None, stamp=100, payload={
        "format": "encounter", "event": "ENCOUNTER_START", "encounter_fields": ["1", "Boss", "1", "5"]}))
    model.apply(env(seq=2, dataset="combat_log", guid=None, stamp=110, payload={
        "source_guid": "Player-1-ABC", "source_flags": 0x511, "metric": "damage", "effective": 100}))
    model.apply(env(seq=3, dataset="combat_log", guid=None, stamp=120, payload={
        "format": "encounter", "event": "ENCOUNTER_END", "encounter_fields": ["1", "Boss", "1", "5", "1"]}))
    result = model.get_dataset("pc", "Player-1-ABC", "last_encounter")
    assert result["payload"]["dps"] == 5
    assert result["payload"]["hps"] is None
    stream = model.get_dataset("pc", None, "encounter_statistics", account="ACCOUNT")
    # Encounter stream is installation-wide, independent of account name.
    stream = model.get_dataset("pc", None, "encounter_statistics")
    assert stream["payload"]["encounters_won"] == 1


def test_batched_raw_events_apply_once(model_module, env):
    model = model_module.Model()
    rows = [{"source_guid": "Player-1-ABC", "source_flags": 0x511, "metric": "damage", "effective": 10} for _ in range(128)]
    message = env(dataset="combat_log", guid=None, payload={"records": rows})
    _, events = model.apply(message)
    assert len(events) == 128
    assert model.get_dataset("pc", "Player-1-ABC", "combat_statistics")["payload"]["damage_done"] == 1280
    assert model.apply(message)[1] == []


def test_future_log_clock_is_not_marked_live(model_module, env):
    _, events = model_module.Model().apply(env(dataset="chat_log", stamp=10000, payload={"text": "clock mismatch"}))
    assert events[0]["historical"]



@pytest.mark.parametrize("change", [{"version": 2}, {"sequence": -1}, {"observed_at": float("nan")},
                                     {"character_guid": "Creature-1-ABC"}, {"dataset": "../evil"}])
def test_protocol_validation(model_module, env, change):
    with pytest.raises(ValueError):
        model_module.validate(dict(env(), **change))
