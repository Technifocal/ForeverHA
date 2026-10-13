# Protocol and integration

## Connection

The companion uses Home Assistant's [authenticated WebSocket API](https://developers.home-assistant.io/docs/api/websocket/) at `/api/websocket`: `auth_required` → `auth` with access token → `auth_ok`. HTTPS URLs become WSS with normal certificate verification. All custom commands require an administrator user. No token is stored in WoW files, datasets, or the outbox.

Add the integration through its configuration flow before starting ingestion. The integration is a single config entry with multiple installation/account/character devices underneath it.

## Envelope version 1

```json
{
  "version": 1,
  "installation": "gaming-pc",
  "account": "ACCOUNT_FOLDER",
  "character_guid": "Player-REALM-ID",
  "source": "saved_variables",
  "client_build": ["1.60.1", "70338", "build date", 16001],
  "session": "persistent-companion-outbox-uuid",
  "sequence": 1,
  "observed_at": 1791763200,
  "received_at": 1791763210,
  "dataset": "character",
  "payload": {
    "status": "available",
    "observed_at": 1791763200,
    "checked_at": 1791763200,
    "payload": {"name": "Tester", "level": 60, "money_copper": 100000}
  }
}
```

Times are Unix seconds. `observed_at` can be null; receipt time is not a substitute for observing a value. Account names are empty for installation-wide logs. A character GUID can be null for shared game files or diagnostics. Client build may be unknown for live log-only records.

Snapshot payloads contain collector status, payload, API evidence, optional scope, and error/missing/secret counters. Previously collected data can remain alongside an `error`, `unsupported`, or `unobserved` status with its old observation time. Sensors do not present those states as available values. Disabled snapshot categories clear their exported payload on the next file save.

Raw log/event payloads are either an individual record or `{"records":[...]}` with at most 128 records. The integration expands batches into individual retained records and automation events. Log records preserve their raw line, optional parsed fields, timestamp text, inferred-year flag, filename, byte offset and file generation. Addon events preserve their own original session and sequence inside the record.

## Commands

`forever_ha/ingest` accepts `envelopes` containing 1–50 ordered envelopes. The result is:

```json
{"acknowledged":[1,2],"results":["accepted","duplicate"]}
```

The companion deletes only the acknowledged batch after the whole batch is confirmed. Source checkpoints and queued observations commit together in SQLite. Integration data and deduplication watermarks save before acknowledgement. The persistent companion UUID and monotonic outbox sequence permit replay after either side restarts. A companion must preserve its ordered outbox: lower sequences on the same installation/session are treated as duplicates. Independent companions need separate installation IDs.

`forever_ha/ingest_chunk` carries a single large envelope split into base64 chunks: `upload_id` (64-character identifier), `index` (zero-based), `count`, and `data`. Chunks are at most 256 KiB before encoding; total assembled size is at most 4 MiB. Intermediate results contain `stored`; the final result has the normal ingestion acknowledgement. Incomplete uploads expire after 60 seconds, with at most four simultaneous uploads. Retrying starts at index zero. This avoids Home Assistant's default 4 MiB inbound frame limit without increasing that limit.

`forever_ha/heartbeat` accepts `installations` and `diagnostics`. The companion sends it every ten seconds with queue sizes, cumulative dropped records, and file-error summaries. Connection availability expires after 35 seconds. Persisted heartbeat timestamps are never treated as connected on Home Assistant restart until a new heartbeat arrives.

`forever_ha/get_dataset` accepts:

```json
{
  "id": 10,
  "type": "forever_ha/get_dataset",
  "installation": "gaming-pc",
  "character_guid": "Player-REALM-ID",
  "dataset": "bags",
  "account": "",
  "offset": 0,
  "limit": 200
}
```

Snapshot results return the complete stored envelope. Raw results contain `records`, `total`, `offset`, collection status, timestamps, and `retention_dropped`; pagination limits are 1–2,000. Unknown datasets return `{"status":"unobserved"}`. Installation-wide logs use null/omitted `character_guid` and empty `account`. Character keys use installation plus GUID so logs and snapshots do not create duplicate character devices.

## Automation events

`forever_ha_event` contains `device_key`, `dataset`, `record`, `historical`, and `observation_delay_seconds`. Inspect the nested record for combat event type, actor/destination GUID, raw text, or addon event. An event can arrive long after it occurred, especially after reconnect or a snapshot save. The sample notification explicitly ignores historical events. This integration does not send commands back to WoW.

## Retention and diagnostics

Latest structured snapshots and aggregate counters persist in Home Assistant. Raw records are bounded per dataset to 2,000 records/2 MiB, with explicit retention-loss counts. Original log files are not edited or deleted. Counters describe successfully observed/parsed records, not a guaranteed full character lifetime.

Malformed/partially written SavedVariables are retried without advancing their checkpoint. Executable Lua is rejected. Binary Lua strings are encoded as `__lua_bytes_base64` objects. Unknown logs remain raw. A log line larger than 256 KiB is skipped with a file-status record so it cannot block later lines. Outbox overflow and a single envelope larger than 4 MiB increment the bridge's visible loss counter.

Authentication errors require a new token. Transport and storage failures retain unacknowledged observations. The integration caps its stored state at 128 MiB and reports a storage-limit command error if exceeded. Back up `.storage/forever_ha` and your game logs before any manual cleanup. Updating an incompatible schema should preserve existing data; no migration to a future schema is silently attempted.

## Screen feed and semantic event deduplication (0.2.0)

Pixel events use `source: pixel`, `dataset: addon_events` and the original addon event payload, including its `session` and `sequence`. These differ from the outer companion session/sequence used for transport acknowledgements. Persisted `addon_event_ranges` are keyed by installation, character GUID and addon session, and contain merged inclusive event-sequence ranges. Only observed IDs are suppressed. Pixel and SavedVariables copies append/fire once, regardless of arrival order or HA restart. On upgrade, retained addon events seed this ledger. Entries are not expired silently; the integration's existing 128 MiB total storage cap still applies.

Compact pixel state uses `dataset: live_state`; it does not overwrite the full snapshot datasets. Binary framing matches DOINK's version-1, three-row, CRC-8/CRC-16 protocol. The JSON payload has a distinct `FHA2` namespace and is at most 2 KiB. The wire array is `[FHA2, GUID, addon_session, sequence, observed_at, kind, ...]`. Event suffix: `[event_name, args, secret_fields, status]`; state suffix: `[16 scalar values]` in the order defined by `pixel.FIELDS`. Unrecognized strip namespaces are ignored. Up to 32 incomplete messages and 128 completed-message hashes are retained locally, with a 20-second partial expiry. Packet hashes remove visual repeats; the durable HA event ledger is the authority for cross-source deduplication.
