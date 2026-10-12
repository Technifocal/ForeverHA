import pytest
from forever_ha_bridge.lua_data import loads, LuaDataError


def test_literal_types_unicode_comments_and_sparse_tables():
    db = loads(r'''-- Saved by WoW
    ForeverHADB = { ["schema"]=1, values={true,false,nil,1.25,-2,0xFF},
       name="Éowyn", escaped="\195\137owyn", text=[=[line\n literal]=], sparse={[3]="three"}, }
    ''')
    assert db["values"] == [True, False, None, 1.25, -2, 255]
    assert db["name"] == db["escaped"] == "Éowyn"
    assert db["sparse"] == {"3": "three"}
    assert db["text"] == r"line\n literal"


@pytest.mark.parametrize("text", [
    'ForeverHADB = os.execute("touch hacked")',
    'ForeverHADB = {schema=1}; print("hacked")',
    'ForeverHADB = {schema=1, x=function() return 1 end}',
    'ForeverHADB = {schema=1, x=1+2}',
    'ForeverHADB = {schema=1, x="unfinished}',
    'ForeverHADB = {schema=1, x=1e999}',
    'OtherDB = {schema=1}',
    'ForeverHADB={schema=1}; ForeverHADB={schema=1}',
])
def test_reject_executable_malformed_and_partial_data(text):
    with pytest.raises(LuaDataError):
        loads(text)


def test_nesting_bound():
    with pytest.raises(LuaDataError):
        loads("ForeverHADB={schema=1,x=" + "{" * 62 + "}" * 62 + "}")


def test_binary_lua_string_preserves_bytes():
    import base64
    db = loads(r'ForeverHADB={schema=1,binary="\000\255\001"}')
    assert base64.b64decode(db["binary"]["__lua_bytes_base64"]) == bytes([0, 255, 1])
