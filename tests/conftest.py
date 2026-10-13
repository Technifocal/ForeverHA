import importlib.util
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))
sys.path.insert(0, str(ROOT))


@pytest.fixture
def model_module():
    spec = importlib.util.spec_from_file_location("forever_model", ROOT / "custom_components/forever_ha/model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env():
    def make(seq=1, dataset="character", stamp=100, payload=None, guid="Player-1-ABC", installation="pc", account="ACCOUNT"):
        return {"version": 1, "installation": installation, "account": account,
                "character_guid": guid, "source": "saved_variables", "client_build": ["1.60.1", "70338"],
                "session": "bridge-session", "sequence": seq, "observed_at": stamp, "received_at": 200,
                "dataset": dataset, "payload": payload if payload is not None else
                {"status": "available", "observed_at": stamp, "payload": {"name": "Tester", "level": 60, "money_copper": 100000}}}
    return make


@pytest.fixture
def addon():
    from lupa.lua51 import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''
        F={}; SlashCmdList={}; _clock=1000
        function GetServerTime() return _clock end
        function time() return _clock end
        function GetTime() return 42 end
        function UnitGUID(u) return u == "player" and "Player-1-ABC" or nil end
        function UnitFullName() return "Tester", "Realm" end
        function UnitName() return "Tester" end
        function GetRealmName() return "Realm" end
        function GetBuildInfo() return "1.60.1", "70338", "Oct 2026", 16001 end
        function UnitLevel() return 60 end
        function GetMoney() return 120000 end
        function CreateFrame()
            local frame={events={}, scripts={}}
            function frame:RegisterEvent(event) self.events[event]=true end
            function frame:SetScript(event,fn) self.scripts[event]=fn end
            return frame
        end
        DEFAULT_CHAT_FRAME={AddMessage=function() end}
        SECRET=setmetatable({}, {__tostring=function() error("secret stringified") end,
                                __eq=function() error("secret compared") end})
        function issecretvalue(v) return rawequal(v,SECRET) end
        function canaccesstable(v) return not rawequal(v,SECRET) end
    ''')
    for filename in ("Core.lua", "Collectors.lua", "Pixel.lua", "Events.lua"):
        source = (ROOT / "addon/ForeverHA" / filename).read_text()
        lua.execute("assert(loadstring(...))('ForeverHA',F)", source)
    lua.execute("assert(F.initialize()); F.worldReady=true; F.queue={}; F.pending={}")
    return lua


def collect(lua, name):
    lua.execute("F.enqueue(...)", name)
    for _ in range(10000):
        lua.execute("F.tick()")
        if lua.eval("not F.running and #F.queue == 0"):
            return lua.globals().F.character.datasets[name]
    raise AssertionError("Collector did not finish")
