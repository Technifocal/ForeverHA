"""Bounded Windows-only addon strip feed. Never saves images or sends input."""
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import json
import math
import sys
import time

from . import doink_pixel as codec
from .sources import envelope

MAX_MESSAGE = 2048
FIELDS = ("health", "health_max", "power", "power_max", "dead", "combat", "mounted", "level",
          "money_copper", "xp", "xp_max", "rested_xp", "zone", "subzone", "dropped_pixel_packets", "secret_fields")


class Assembler:
    def __init__(self):
        self.partial = OrderedDict()
        self.done = OrderedDict()
        self.expired = 0

    def add(self, chunk, now=None):
        now = time.monotonic() if now is None else now
        for key in list(self.partial):
            if now - self.partial[key]["at"] > 20:
                self.partial.pop(key)
                self.expired += 1
        if not 1 <= chunk.count <= 21 or not 0 <= chunk.index < chunk.count:
            return None
        key = (chunk.msg_id, chunk.count)
        part = self.partial.setdefault(key, {"at": now, "parts": {}})
        if chunk.index in part["parts"] and part["parts"][chunk.index] != chunk.payload:
            part["parts"] = {}  # message ID reused across sessions/windows
        part["parts"][chunk.index] = chunk.payload
        if sum(map(len, part["parts"].values())) > MAX_MESSAGE:
            self.partial.pop(key)
            self.expired += 1
            return None
        if len(self.partial) > 32:
            self.partial.popitem(last=False)
            self.expired += 1
        if len(part["parts"]) < chunk.count:
            return None
        self.partial.pop(key, None)
        message = b"".join(part["parts"][i] for i in range(chunk.count))
        digest = hashlib.sha256(message).digest()
        if digest in self.done:
            return None
        self.done[digest] = None
        if len(self.done) > 128:
            self.done.popitem(last=False)
        return message


def packet_envelope(message, installation):
    if len(message) > MAX_MESSAGE:
        raise ValueError("Pixel message exceeds limit")
    packet = json.loads(message.decode("utf-8"))
    if not isinstance(packet, list) or len(packet) not in (7, 10) or packet[0] != "FHA2":
        return None  # DOINK and other strips must never be ingested
    _, guid, session, sequence, observed, kind, payload, *extra = packet
    if not isinstance(guid, str) or not guid.startswith("Player-") or len(guid) > 100:
        raise ValueError("Invalid pixel character")
    if not isinstance(session, str) or not 0 < len(session) <= 100 or type(sequence) is not int or sequence < 1:
        raise ValueError("Invalid pixel event identity")
    if type(observed) not in (int, float) or not math.isfinite(observed) or observed < 0:
        raise ValueError("Invalid pixel timestamp")
    if kind == "event" and len(packet) == 10:
        args, secrets, status = extra
        if not isinstance(payload, str) or not isinstance(args, (dict, list)) or type(secrets) is not int or status not in ("available", "restricted"):
            raise ValueError("Invalid pixel event")
        record = {"event": payload, "args": args, "session": session, "sequence": sequence,
                  "observed_at": observed, "secret_fields": secrets, "status": status}
        return envelope(installation, "", "addon_events", record, "pixel", observed, guid)
    if kind == "state" and len(packet) == 7 and isinstance(payload, list) and len(payload) == len(FIELDS):
        data = dict(zip(FIELDS, payload))
        for name, value in data.items():
            if value is not None and (type(value) not in (str, int, float, bool) or (isinstance(value, float) and not math.isfinite(value))):
                raise ValueError("Invalid live state value")
        record = {"status": "restricted" if data["secret_fields"] else "available", "observed_at": observed,
                  "checked_at": observed, "payload": data}
        return envelope(installation, "", "live_state", record, "pixel", observed, guid)
    raise ValueError("Unsupported pixel packet")


class Reader:
    def __init__(self, capture=None, meter=None):
        self.capture = capture or codec.Capture()
        self.assembler = Assembler()
        self.meter = meter or codec.ResourceMeter(max_avg_ms=5, max_growth_mb=32, max_gdi_growth=12)
        self.last_seen = None
        self.invalid = 0
        self.stopped = None

    def poll(self):
        if self.stopped:
            return []
        old_dpi = None
        change_dpi = None
        if sys.platform == "win32":
            change_dpi = getattr(codec._user32, "SetThreadDpiAwarenessContext", None)
            if change_dpi:
                change_dpi.restype = ctypes.c_void_p
                change_dpi.argtypes = [ctypes.c_void_p]
                old_dpi = change_dpi(ctypes.c_void_p(-4))  # per-monitor v2, only this worker
        wall, cpu = time.perf_counter(), time.process_time()
        messages = []
        try:
            grab = self.capture.grab()
            if grab is not None:
                for top, bottom in ((0, False), (grab.band_rows, True)):
                    chunk = codec.decode(grab.buf, grab.width, grab.stride, top, grab.band_rows, bottom)
                    if chunk is None:
                        continue
                    message = self.assembler.add(chunk)
                    if message is not None:
                        messages.append(message)
        finally:
            self.meter.record(time.perf_counter() - wall, time.process_time() - cpu)
            if change_dpi and old_dpi:
                change_dpi(old_dpi)
        self.stopped = self.meter.should_stop()
        return messages

    def close(self):
        self.capture.close()


class PixelFeed:
    def __init__(self, installation, hz=8, reader_factory=Reader):
        self.installation, self.hz, self.reader_factory = installation, hz, reader_factory
        self.status = {"enabled": True, "window": "starting", "last_seen": None, "invalid_messages": 0}

    async def run(self, store, stopping):
        # All GDI allocation, use, DPI changes and cleanup run on one worker.
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="foreverha-pixels")
        loop = asyncio.get_running_loop()
        reader = None
        try:
            reader = await loop.run_in_executor(executor, self.reader_factory)
            while not stopping.is_set():
                started = time.monotonic()
                messages = await loop.run_in_executor(executor, reader.poll)
                records = []
                for message in messages:
                    try:
                        record = packet_envelope(message, self.installation)
                        if record:
                            records.append(record)
                            reader.last_seen = time.time()
                            self.status["character_guid"] = record["character_guid"]
                    except (ValueError, TypeError, KeyError, RecursionError):
                        reader.invalid += 1
                if records:
                    # SQLite stays on the asyncio thread with file checkpoints.
                    store.ingest("pixel:" + self.installation, {"last_seen": reader.last_seen}, records)
                self.status.update(window=reader.capture.window, last_seen=reader.last_seen,
                                   invalid_messages=reader.invalid, incomplete_messages=reader.assembler.expired,
                                   resource_stop=reader.stopped, **reader.meter.snapshot())
                if reader.stopped:
                    self.status["window"] = "paused"
                    break
                interval = 1 / self.hz if reader.capture.window == "foreground" else 1.0
                try:
                    await asyncio.wait_for(stopping.wait(), max(0.01, interval - (time.monotonic() - started)))
                except TimeoutError:
                    pass
        except Exception as exc:
            self.status.update(window="error", error=type(exc).__name__)
        finally:
            if reader:
                await loop.run_in_executor(executor, reader.close)
            executor.shutdown(wait=True)
