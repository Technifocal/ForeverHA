import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import time
import sys
import sqlite3
from pathlib import Path

from .sources import discover_files, discover_roots, snapshot, tail
from .store import Store
from .transport import AuthenticationError, ProtocolError, Transport

LOG = logging.getLogger("forever_ha")


def default_state_directory():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "ForeverHA"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/ForeverHA"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "forever-ha"


def read_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    token_env = config.get("token_env", "FOREVER_HA_TOKEN")
    if not os.environ.get(token_env):
        raise ValueError(f"Set the {token_env} environment variable to your Home Assistant token")
    roots = config.get("installations")
    if roots is None:
        roots = [{"path": str(p)} for p in discover_roots()]
    if not roots:
        raise ValueError("No game installation found; set installations[].path to the client folder containing WTF and Logs")
    normalized = []
    for item in roots:
        root = Path(item["path"]).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Configured game directory does not exist")
        install_id = item.get("id") or hashlib.sha256(str(root).encode()).hexdigest()[:16]
        if not isinstance(install_id, str) or not install_id or len(install_id) > 100:
            raise ValueError("Installation ID must be a nonempty string of at most 100 characters")
        normalized.append(dict(item, path=root, id=install_id))
    if len({r["id"] for r in normalized}) != len(normalized):
        raise ValueError("Installation IDs must be unique")
    config["installations"] = normalized
    if not isinstance(config.get("home_assistant_url"), str):
        raise ValueError("Set home_assistant_url")
    config["token"] = os.environ[token_env]
    for name, default in (("max_queue_records", 20000), ("max_queue_bytes", 100 * 1024 * 1024)):
        if type(config.get(name, default)) is not int or config.get(name, default) <= 0:
            raise ValueError("Queue limits must be positive integers")
    return config


def poll(config, store):
    problems = []
    for install in config["installations"]:
        for path, kind, account in discover_files(install["path"], install.get("accounts")):
            if kind in ("combat", "chat") and not install.get(kind + "_log", True):
                continue
            try:
                stat = path.stat()
                identity = f"{stat.st_dev}:{stat.st_ino}" if stat.st_ino and kind != "snapshot" else str(path)
                key = "file:" + install["id"] + ":" + kind + ":" + identity
                checkpoint = store.get(key, {})
                if kind == "snapshot":
                    records, updated = snapshot(path, install["id"], account, checkpoint)
                else:
                    records, updated = tail(path, install["id"], account, kind, checkpoint,
                                            install.get("log_timezone"), install.get("import_history", True))
                if records or updated != checkpoint:
                    store.ingest(key, updated, records)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                # Report only the filename and exception class, not mail/chat/token contents.
                problems.append({"file": path.name, "error": type(exc).__name__})
    return problems


async def run(config, once=False):
    state_dir = Path(config.get("state_directory", default_state_directory())).expanduser().resolve()
    if any(state_dir == r["path"] or r["path"] in state_dir.parents for r in config["installations"]):
        raise ValueError("Keep state_directory outside the game installation")
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    store = Store(state_dir / "bridge.sqlite3", config.get("max_queue_records", 20000),
                  config.get("max_queue_bytes", 100 * 1024 * 1024))
    if os.name != "nt":
        os.chmod(state_dir / "bridge.sqlite3", 0o600)
    transport = Transport(config["home_assistant_url"], config["token"])
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except (NotImplementedError, RuntimeError):
            pass
    retry_at, delay, heartbeat_at = 0.0, 1.0, 0.0
    last_losses, last_problems = 0, None
    ids = [r["id"] for r in config["installations"]]
    try:
        while not stopping.is_set():
            started = time.monotonic()
            problems = poll(config, store)
            # sqlite connection and source checkpoints stay on a single thread.
            diagnostics = dict(store.diagnostics(), file_errors=problems)
            if problems != last_problems and problems:
                LOG.warning("%s file(s) could not be read; see Home Assistant bridge diagnostics", len(problems))
            last_problems = problems
            if diagnostics["dropped_records"] > last_losses:
                LOG.warning("Outbox limit exceeded: %s total records dropped", diagnostics["dropped_records"])
                last_losses = diagnostics["dropped_records"]
            if time.monotonic() >= retry_at:
                try:
                    if not transport.ws:
                        await transport.open()
                        heartbeat_at = 0
                    if time.monotonic() >= heartbeat_at:
                        await transport.heartbeat(ids, diagnostics)
                        heartbeat_at = time.monotonic() + 10
                    await transport.send(store)
                    delay = 1.0
                except AuthenticationError:
                    LOG.error("Home Assistant rejected the token. Update the environment variable and restart.")
                    return 2
                except Exception as exc:
                    LOG.warning("Connection unavailable (%s); records retained for retry", type(exc).__name__)
                    await transport.close()
                    retry_at = time.monotonic() + delay
                    delay = min(60.0, delay * 2)
            if once:
                return 0 if not store.batch() else 1
            remaining = max(0.01, 1 - (time.monotonic() - started))
            try:
                await asyncio.wait_for(stopping.wait(), remaining)
            except TimeoutError:
                pass
    finally:
        await transport.close()
        store.close()
    return 0


async def query(config, args):
    transport = Transport(config["home_assistant_url"], config["token"])
    try:
        await transport.open()
        result = await transport.command("forever_ha/get_dataset",
                    installation=args.installation or config["installations"][0]["id"],
                    character_guid=args.character_guid, account=args.account,
                    dataset=args.dataset, offset=args.offset, limit=args.limit)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    finally:
        await transport.close()


def main():
    parser = argparse.ArgumentParser(description="Sync normal WoW Forever files to Home Assistant")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--discover", action="store_true", help="List likely client directories and exit")
    parser.add_argument("--once", action="store_true", help="Poll files and send one batch for diagnostics")
    parser.add_argument("--dataset", help="Query an exported dataset and exit")
    parser.add_argument("--installation", help="Installation ID for dataset query")
    parser.add_argument("--character-guid", help="Character GUID; omit for installation-wide logs")
    parser.add_argument("--account", default="")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output", help="Write queried JSON to this file instead of stdout")
    args = parser.parse_args()
    if args.discover:
        print(json.dumps([str(p) for p in discover_roots()], indent=2))
        return
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        config = read_config(args.config)
        if args.dataset:
            asyncio.run(query(config, args))
            code = 0
        else:
            code = asyncio.run(run(config, args.once))
    except (OSError, ValueError, sqlite3.Error, AuthenticationError, ProtocolError) as exc:
        LOG.error("Configuration/setup error (%s): check paths, URL, token and JSON syntax", type(exc).__name__)
        code = 2
    except KeyboardInterrupt:
        code = 0
    raise SystemExit(code)


if __name__ == "__main__":
    main()
