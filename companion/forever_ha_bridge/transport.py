import asyncio
import json
import time
import base64
import hashlib
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect


class AuthenticationError(RuntimeError):
    pass


class ProtocolError(RuntimeError):
    pass


def websocket_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Home Assistant URL must be http(s) with no embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Home Assistant URL must not contain a query or fragment")
    return urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc,
                       parsed.path.rstrip("/") + "/api/websocket", "", ""))


class Transport:
    def __init__(self, url, token):
        self.url, self.token = websocket_url(url), token
        self.ws, self.counter = None, 0

    async def open(self):
        # wss uses the platform certificate store; certificate verification stays enabled.
        self.ws = await connect(self.url, max_size=8 * 1024 * 1024, open_timeout=15,
                                ping_interval=20, ping_timeout=20)
        try:
            greeting = json.loads(await asyncio.wait_for(self.ws.recv(), 15))
            if greeting.get("type") != "auth_required":
                raise ProtocolError("Unexpected authentication greeting")
            await self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
            reply = json.loads(await asyncio.wait_for(self.ws.recv(), 15))
            if reply.get("type") == "auth_invalid":
                raise AuthenticationError("Home Assistant rejected the access token")
            if reply.get("type") != "auth_ok":
                raise ProtocolError("Authentication did not complete")
        except Exception:
            await self.close()
            raise

    async def command(self, kind, **data):
        self.counter += 1
        command_id = self.counter
        await self.ws.send(json.dumps(dict(data, id=command_id, type=kind), ensure_ascii=False, separators=(",", ":")))
        async with asyncio.timeout(20):
            while True:
                reply = json.loads(await self.ws.recv())
                if reply.get("id") != command_id:
                    continue
                if reply.get("type") != "result" or not reply.get("success"):
                    # Don't log a server error which may echo private request content.
                    code = reply.get("error", {}).get("code", "invalid_response")
                    raise ProtocolError(f"Home Assistant command failed ({code})")
                return reply.get("result")

    async def send(self, store):
        batch = store.batch()
        if batch:
            if len(batch) == 1 and len(json.dumps(batch[0], ensure_ascii=False, separators=(",", ":")).encode()) > 3 * 1024 * 1024:
                # HA's inbound WebSocket frames are limited to 4 MiB.
                blob = json.dumps(batch[0], ensure_ascii=False, separators=(",", ":")).encode()
                upload = hashlib.sha256(blob).hexdigest()
                pieces = [blob[i:i + 256 * 1024] for i in range(0, len(blob), 256 * 1024)]
                for index, piece in enumerate(pieces):
                    reply = await self.command("forever_ha/ingest_chunk", upload_id=upload, index=index,
                                               count=len(pieces), data=base64.b64encode(piece).decode("ascii"))
            else:
                reply = await self.command("forever_ha/ingest", envelopes=batch)
            expected = [e["sequence"] for e in batch]
            if not isinstance(reply, dict) or reply.get("acknowledged") != expected:
                raise ProtocolError("Incomplete acknowledgement; retaining batch")
            store.ack(batch)
        return bool(batch)

    async def heartbeat(self, installations, diagnostics):
        await self.command("forever_ha/heartbeat", installations=installations, diagnostics=diagnostics)

    async def close(self):
        if self.ws:
            await self.ws.close()
            self.ws = None
