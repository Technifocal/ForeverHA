"""Lua encoder -> captured pixels -> Python -> durable HA deduplication.

Rendering helper adapted from DOINK's MIT test suite; licenses/DOINK.txt.
"""
import asyncio
import copy
import json
import sys
import time
from types import SimpleNamespace

import pytest
from forever_ha_bridge import doink_pixel as codec
from forever_ha_bridge.pixel import Assembler, PixelFeed, Reader, packet_envelope
from forever_ha_bridge.cli import ConfigurationError, pixel_settings
from forever_ha_bridge.store import Store


def render(rows, block=3, width=1300, x0=12, bottom=True, white=255, black=0):
    buf = bytearray([90, 90, 90, 255] * width * codec.BAND_ROWS)
    top = codec.BAND_ROWS - 3 * block if bottom else 0
    for y in range(codec.BAND_ROWS):
        if not 0 <= y - top < 3 * block:
            continue
        row = int((y - top) // block)
        for x in range(x0, width):
            column = int((x - x0) // block)
            if column < len(rows[row]):
                shade = white if rows[row][column] else black
                offset = (y * width + x) * 4
                buf[offset:offset + 3] = bytes([shade] * 3)
    return buf


def lua_rows(lua, message, ident=27):
    chunks = lua.eval("F.pixel.encode")(ident, message, 400)
    return [[[chunk[r][c] for c in range(1, 401)] for r in range(1, 4)] for chunk in chunks.values()]


def wire(event=True, sequence=1, session="login-1"):
    if event:
        return ["FHA2", "Player-1-PIXEL", session, sequence, time.time(), "event", "PLAYER_DEAD", [], 0, "available"]
    return ["FHA2", "Player-1-PIXEL", session, sequence, time.time(), "state",
            [100, 200, None, None, False, True, False, 60, 100000, 5, 100, None, "Zone", "Subzone", 0, 1]]


@pytest.mark.parametrize("block,bottom,white,black", [(3, True, 255, 0), (2, False, 180, 30), (3.03, True, 160, 40)])
def test_lua_strip_roundtrip_unicode_multichunk_and_repeat(addon, block, bottom, white, black):
    packet = wire()
    packet[6:8] = ["CHAT_MSG_SAY", ["Hello 世界 " + "x" * 150]]
    message = json.dumps(packet, ensure_ascii=False).encode()
    assembler = Assembler()
    got = None
    rows = lua_rows(addon, message)
    for chunk_rows in reversed(rows):
        buf = render(chunk_rows, block=block, bottom=bottom, white=white, black=black)
        chunk = codec.decode(buf, 1300, 5200, 0, codec.BAND_ROWS, bottom)
        assert chunk is not None
        got = assembler.add(chunk) or got
        assert assembler.add(chunk) is None
    assert got == message
    record = packet_envelope(got, "pc")
    assert record["payload"]["args"][0].startswith("Hello 世界")
    assert record["source"] == "pixel" and record["dataset"] == "addon_events"


def test_crc_and_corrupted_strip_are_rejected(addon):
    assert addon.eval("F.pixel.crc('123456789',16,0x1021,0xFFFF)") == codec.crc16(b"123456789") == 0x29B1
    assert addon.eval("F.pixel.crc('123456789',8,7,0)") == codec.crc8(b"123456789") == 0xF4
    rows = lua_rows(addon, b"test")[0]
    rows[1][10] = 1 - rows[1][10]
    assert codec.decode(render(rows), 1300, 5200, 0, 24, True) is None


def test_lua_json_nulls_and_secret_fields(addon):
    addon.execute("wire={n=4, 'FHA2', false, F.clean(SECRET), 'quote\\\"\\n'}")
    message = addon.eval("F.pixel.json(wire)")
    assert json.loads(message) == ["FHA2", False, None, 'quote"\n']
    assert addon.eval("F.pixel.dropped") == 0
    with pytest.raises(Exception):
        addon.eval("F.pixel.json")("x" * 3000)


def test_packet_validation_and_doink_isolation():
    assert packet_envelope(b'{"type":"death","seq":1}', "pc") is None
    packet = wire(False)
    value = packet_envelope(json.dumps(packet).encode(), "pc")
    assert value["payload"]["status"] == "restricted"
    assert value["payload"]["payload"]["health"] == 100
    assert value["payload"]["payload"]["power"] is None
    packet[3] = -1
    with pytest.raises(ValueError):
        packet_envelope(json.dumps(packet).encode(), "pc")


def test_assembler_bounds_expiry_and_id_reuse():
    a = Assembler()
    for index in range(40):
        assert a.add(codec.Chunk(index, 0, 2, b"a", 400, 3), now=0) is None
    assert len(a.partial) == 32 and a.expired == 8
    assert a.add(codec.Chunk(100, 0, 1, b"new", 400, 3), now=21) == b"new"
    assert not a.partial
    assert a.add(codec.Chunk(100, 0, 1, b"other session", 400, 3), now=22) == b"other session"
    assert a.add(codec.Chunk(100, 0, 1, b"new", 400, 3), now=23) is None


def test_pixel_then_saved_variables_dedup_across_ha_restart_and_missed_gaps(model_module, env):
    model = model_module.Model()
    live = packet_envelope(json.dumps(wire(sequence=5)).encode(), "pc")
    received = env(seq=1, guid=live["character_guid"], payload=live["payload"], dataset="addon_events", installation="pc")
    received["source"] = "pixel"
    assert len(model.apply(received)[1]) == 1
    restored = model_module.Model(copy.deepcopy(model.data))
    saved = dict(received, sequence=2, source="saved_variables")
    assert restored.apply(saved)[1] == []
    missed = dict(saved, sequence=3, payload=dict(saved["payload"], sequence=3))
    assert len(restored.apply(missed)[1]) == 1  # no high watermark dropping the gap
    assert restored.apply(dict(missed, sequence=4))[1] == []
    new_session = dict(saved, sequence=5, payload=dict(saved["payload"], session="next-login"))
    assert len(restored.apply(new_session)[1]) == 1
    ranges = list(restored.data["addon_event_ranges"].values())
    assert [[3, 3], [5, 5]] in ranges


def test_saved_variables_first_then_pixel_and_other_characters(model_module, env):
    model = model_module.Model()
    record = {"event": "PLAYER_DEAD", "session": "same", "sequence": 1}
    assert len(model.apply(env(seq=1, dataset="addon_events", payload=record))[1]) == 1
    assert model.apply(env(seq=2, dataset="addon_events", payload=record))[1] == []
    assert len(model.apply(env(seq=3, guid="Player-1-DEF", dataset="addon_events", payload=record))[1]) == 1


def test_resource_cpu_and_handle_limits():
    meter = codec.ResourceMeter(max_avg_ms=5, max_gdi_growth=12)
    for _ in range(21):
        meter.record(0.020, 0.001)
    assert not meter.should_stop()  # GPU waiting is not CPU usage
    for _ in range(64):
        meter.record(0.020, 0.020)
    assert meter.should_stop()
    meter = codec.ResourceMeter(max_gdi_growth=12)
    for _ in range(3): meter.inject(40, 4)
    meter.inject(40, 17)
    assert "GDI" in meter.should_stop()


def test_feed_thread_cleanup_and_ingestion(tmp_path):
    async def scenario():
        stopped = asyncio.Event()
        message = json.dumps(wire()).encode()
        class FakeReader:
            last_seen = None
            invalid = 0
            stopped = "resource test"
            capture = SimpleNamespace(window="foreground")
            assembler = SimpleNamespace(expired=0)
            meter = SimpleNamespace(snapshot=lambda: {})
            closed = False
            def poll(self): return [message]
            def close(self): self.closed = True
        reader = FakeReader()
        feed = PixelFeed("pc", reader_factory=lambda: reader)
        store = Store(tmp_path / "outbox.db")
        await feed.run(store, stopped)
        assert reader.closed
        assert store.batch()[0]["source"] == "pixel"
        assert feed.status["window"] == "paused"
        store.close()
    asyncio.run(scenario())


def test_configuration_requires_explicit_multi_installation(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    config = {"installations": [{"id": "one"}, {"id": "two"}]}
    with pytest.raises(ConfigurationError, match="multiple"):
        pixel_settings(config, True)
    config["pixel_transport"] = {"enabled": True, "installation": "two", "poll_hz": 4}
    assert pixel_settings(config) == {"installation": "two", "hz": 4}
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(ConfigurationError, match="Windows-only"):
        pixel_settings(config)


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Win32 GDI cleanup requires Windows")
def test_windows_gdi_buffers_resize_without_handle_leak():
    baseline = codec.gdi_handles()
    capture = codec.Capture(find_window=lambda: None)
    try:
        for width in range(640, 690):
            capture._allocate(width)
            assert codec.gdi_handles() <= baseline + 3
    finally:
        capture.close()
    assert codec.gdi_handles() <= baseline + 1


def prepare_lua_ui(lua):
    lua.execute('''
      texture_count=0
      local methods={}
      for _,name in ipairs({'SetFrameStrata','SetFrameLevel','EnableMouse','SetIgnoreParentScale',
        'SetScale','SetSize','SetPoint','SetColorTexture'}) do methods[name]=function() end end
      methods.Hide=function(self) self.shown=false end
      methods.Show=function(self) self.shown=true end
      methods.IsShown=function(self) return self.shown end
      methods.CreateTexture=function(self) texture_count=texture_count+1; return setmetatable({}, {__index=methods}) end
      CreateFrame=function() return setmetatable({}, {__index=methods}) end
      GetPhysicalScreenSize=function() return 1920,1080 end
      UIParent=setmetatable({}, {__index=methods})
    ''')


def test_addon_queue_bounded_and_death_preempts_state(addon):
    prepare_lua_ui(addon)
    addon.execute("F.pixel.setEnabled(true)")
    assert addon.eval("texture_count") == 1200
    addon.execute("for i=1,30 do F.record('CHAT_MSG_SAY','hi') end; F.record('PLAYER_DEAD')")
    assert addon.eval("#F.pixel.queue") <= 16
    assert addon.eval("F.pixel.dropped") > 0
    message = addon.eval("F.pixel.queue[1].chunks[1]")
    rows = [[message[r][c] for c in range(1, 401)] for r in range(1, 4)]
    chunk = codec.decode(render(rows), 1300, 5200, 0, 24, True)
    assert chunk is not None
    assert addon.eval("F.character.events[#F.character.events].event") == 'PLAYER_DEAD'
    addon.execute("for i=1,20 do F.pixel.state() end")
    assert addon.eval("texture_count") == 1200  # no textures allocated per update
    addon.execute("F.pixel.setEnabled(false)")
    assert addon.eval("#F.pixel.queue") == 0


def test_same_source_event_id_is_preserved_in_lua_strip(addon):
    prepare_lua_ui(addon)
    addon.execute("F.pixel.setEnabled(true); F.record('PLAYER_DEAD')")
    assembler = Assembler()
    chunks = addon.eval("F.pixel.queue[1].chunks")
    result = None
    for rows in chunks.values():
        bits = [[rows[r][c] for c in range(1, 401)] for r in range(1, 4)]
        chunk = codec.decode(render(bits), 1300, 5200, 0, 24, True)
        result = assembler.add(chunk) or result
    record = packet_envelope(result, "pc")['payload']
    saved = addon.eval("F.character.events[#F.character.events]")
    assert record['session'] == saved['session']
    assert record['sequence'] == saved['sequence']
    assert record['observed_at'] == saved['observed_at']
    assert record['event'] == saved['event'] == 'PLAYER_DEAD'


def test_unchanged_state_sends_only_heartbeat(addon):
    prepare_lua_ui(addon)
    addon.execute("F.pixel.setEnabled(true)")
    count = addon.eval('F.pixel.sequence')
    addon.execute('for i=1,10 do F.pixel.state() end')
    assert addon.eval('F.pixel.sequence') == count
    addon.execute('_clock=_clock+5; F.pixel.state()')
    assert addon.eval('F.pixel.sequence') == count + 1


def test_upgrade_seeds_dedup_from_preexisting_retained_events(model_module, env):
    model = model_module.Model()
    record = {"event": "PLAYER_DEAD", "session": "old-login", "sequence": 8}
    model.apply(env(seq=1, dataset="addon_events", payload=record))
    old_data = copy.deepcopy(model.data)
    old_data.pop("addon_event_ranges")
    upgraded = model_module.Model(old_data)
    assert upgraded.apply(env(seq=2, dataset="addon_events", payload=record))[1] == []
