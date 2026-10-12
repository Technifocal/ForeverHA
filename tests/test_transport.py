import asyncio
import json
import pytest
from websockets.asyncio.server import serve
from forever_ha_bridge.store import Store
from forever_ha_bridge.transport import AuthenticationError, ProtocolError, Transport, websocket_url


def test_url_and_tls_scheme():
    assert websocket_url("https://ha.example/") == "wss://ha.example/api/websocket"
    assert websocket_url("http://localhost:8123") == "ws://localhost:8123/api/websocket"
    with pytest.raises(ValueError):
        websocket_url("http://user:secret@ha.example")


def test_ack_loss_reconnect_and_duplicate_application(tmp_path, model_module, env):
    async def scenario():
        model = model_module.Model()
        attempts = 0

        async def server(ws):
            nonlocal attempts
            await ws.send(json.dumps({"type": "auth_required"}))
            auth = json.loads(await ws.recv())
            assert auth["access_token"] == "test-token"
            await ws.send(json.dumps({"type": "auth_ok"}))
            async for raw in ws:
                command = json.loads(raw)
                for message in command["envelopes"]:
                    model.apply(message)
                attempts += 1
                if attempts == 1:
                    await ws.close()
                    return
                await ws.send(json.dumps({"id": command["id"], "type": "result", "success": True,
                                          "result": {"acknowledged": [e["sequence"] for e in command["envelopes"]]}}))

        async with serve(server, "127.0.0.1", 0) as service:
            port = service.sockets[0].getsockname()[1]
            bridge = Transport(f"http://127.0.0.1:{port}", "test-token")
            store = Store(tmp_path / "outbox.db")
            record = env(dataset="combat_log", payload={"source_guid": "Player-1-ABC", "source_flags": 0x511,
                          "metric": "damage", "amount": 100, "effective": 100})
            store.ingest("cursor", {"offset": 10}, [record])
            await bridge.open()
            with pytest.raises(Exception):
                await bridge.send(store)
            assert len(store.batch()) == 1
            await bridge.close()
            await bridge.open()
            await bridge.send(store)
            assert not store.batch()
            assert model.get_dataset("pc", "Player-1-ABC", "combat_statistics")["payload"]["damage_done"] == 100
            await bridge.close()
            store.close()
    asyncio.run(scenario())


def test_rejected_token_is_explicit():
    async def scenario():
        async def server(ws):
            await ws.send(json.dumps({"type": "auth_required"}))
            await ws.recv()
            await ws.send(json.dumps({"type": "auth_invalid"}))
        async with serve(server, "127.0.0.1", 0) as service:
            port = service.sockets[0].getsockname()[1]
            transport = Transport(f"http://127.0.0.1:{port}", "expired-token")
            with pytest.raises(AuthenticationError):
                await transport.open()
            assert transport.ws is None
    asyncio.run(scenario())


def test_partial_ack_keeps_outbox(tmp_path, env):
    async def scenario():
        class Partial(Transport):
            async def command(self, *args, **kwargs):
                return {"acknowledged": []}
        store = Store(tmp_path / "queue.db")
        store.ingest("cursor", {}, [env()])
        with pytest.raises(ProtocolError):
            await Partial("http://localhost", "token").send(store)
        assert store.batch()
        store.close()
    asyncio.run(scenario())
