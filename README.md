# ForeverHA — WoW Forever → Home Assistant

ForeverHA collects broad character data in WoW, reads the files WoW normally writes, and pushes observations to Home Assistant without MQTT. The Lua addon is portable. Its Python companion runs on Windows, macOS, and Linux.

**Addon/integration/companion version 0.1.1:** implemented and tested offline against Lua 5.1 and Home Assistant Core 2026.10.1. API names were audited against Forever UI source **1.60.1 (70338), interface 16001**. An in-game smoke test is still required; no live Forever client was available during development. Windows/Linux test workflows are provided but have not been run here.

## What updates live

With game logging enabled, the companion forwards combat and chat records as WoW flushes them to disk. Recognized combat records provide logged damage, effective healing, deaths, casts, interrupts, encounter outcomes, and encounter DPS/HPS when matching start/end records exist. Unknown formats remain available as raw records, without guessed statistics.

Character progression, money, equipment, bags, bank, quests, professions, recipes, reputations, social lists, mail, auctions, collections, and accessible addon events are snapshots. WoW writes SavedVariables on logout or `/reload`; an addon cannot turn arbitrary values into a continuously updated file. Every dataset carries observation time, source, collection status, and scope. Companion connectivity is separate from character presence.

The 0.1.1 hotfix replaces the coroutine scheduler after three build-70338 crash reports pointed to `coroutine.resume` in the addon. Collection now runs through bounded work items on normal frame callbacks. It waits until two seconds after entering the world, pauses during loading, and performs no new scans during logout. Collection journals and achievements require their corresponding Blizzard windows to be open. See [the crash analysis and upgrade instructions](docs/HOTFIX-0.1.1.md). Live-client confirmation is pending.

All collection categories are enabled by default, including accessible chat, social information, and mail bodies. See [coverage](docs/COVERAGE.md) for exact scope and [protocol](docs/PROTOCOL.md) for complete dataset access.

## Install the addon

From a source checkout, copy `addon/ForeverHA` into your Forever client's `Interface/AddOns` folder. To build the ZIP packages mentioned below, run `python tools/package.py` from the repository root; packages are written to the parent directory.

1. Close WoW. Extract `ForeverHA-addon-0.1.1.zip` into your **Forever client's** `Interface/AddOns` folder. The result must be `Interface/AddOns/ForeverHA/ForeverHA.toc`.
2. Enable ForeverHA at character selection, then log in.
3. Run `/foreverha status`. Visit the bank, mailbox, auction house, and each profession window to collect their conditional data. The addon does not open windows or perform gameplay actions.
4. Run `/foreverha logging on` to enable combat and chat logs. It changes logging only when you explicitly request it. Use `/foreverha logging off` to stop logging.
5. Allow collection to finish, then `/reload` once to create the initial snapshot file. `/foreverha refresh` queues collection but does not write it to disk.

Both a regular and a `_Camelot` TOC are included. Use the client directory that contains your Forever `WTF` and `Logs` folders, rather than assuming that every Classic folder is Forever.

## Install the Home Assistant integration

In **HACS → Custom repositories**, add `https://github.com/Technifocal/ForeverHA` with type **Integration**, then download **WoW Forever**. Requires Home Assistant 2026.10 or newer. Restart Home Assistant and add **WoW Forever** under **Settings → Devices & services**. HACS installs only the Home Assistant integration; install the WoW addon and companion separately using the instructions below.

If HACS still offers the old `fe7f9ff` revision, use **Update information**, then redownload the default branch. If its previous repository discovery was cached while the repository was private, remove and re-add the custom repository.

For manual installation, copy `custom_components/forever_ha` into Home Assistant's `custom_components` directory.

1. Extract `ForeverHA-home-assistant-0.1.1.zip` into Home Assistant's configuration directory. It contains `custom_components/forever_ha`.
2. Restart Home Assistant. Open **Settings → Devices & services → Add integration → WoW Forever**.
3. In your administrator profile, create a long-lived access token for the companion. The ingestion and dataset commands require an administrator token because they expose private game data.
4. Use your reachable Home Assistant URL in the companion configuration. For HTTPS, certificate verification stays enabled. HTTP is supported for a trusted local connection.

The integration creates devices for observed characters and a companion device for each installation. Ordinary values become sensors; larger datasets are available through the authenticated API. Gold, health, zone, and similar snapshot sensors retain their observation timestamps. Live counters become unavailable when the companion disconnects.

## Install and run the companion

Install Python **3.11 or newer** on the gaming computer. From the extracted source bundle:

**Windows PowerShell**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .\companion
Copy-Item examples\config.json config.json
```

**macOS / Linux**

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ./companion
cp examples/config.json config.json
```

Alternatively, install the included `forever_ha_bridge-0.1.1-py3-none-any.whl`; pip will install its dependencies. Native executable installers are not included.

Edit `config.json` with your URL and client path. An installation `id` identifies the gaming computer consistently; use a different ID on each computer. Use `accounts` to restrict SavedVariables to named account folders, or omit it to collect all accounts. Game logs are installation-wide and do not contain account-folder names.

To find likely directories:

```sh
forever-ha --discover
```

Omit the `installations` field to use discovery. Manual paths work for custom Windows drives, Wine/Proton prefixes, and unsupported launcher layouts. Verify the detected client is Forever.

Check your configuration before starting. This reads no credentials from disk, does not connect to Home Assistant, and writes no state:

```sh
forever-ha --config config.json --check-config
```

Start with a secure token prompt; the token remains in memory for this run and is not saved:

```sh
forever-ha --config config.json --prompt-token
```

Normal synchronization discovers all character GUIDs automatically. `--character-guid` only filters queries made with `--dataset`.

### Windows setup troubleshooting

The old 0.1.0 companion hid setup failures behind `Configuration/setup error (ValueError)`. Upgrade it with `python -m pip install --upgrade ./companion` from the source checkout, in the same Python environment used to run it. Version 0.1.1 reports the specific setup problem. You can also install the 0.1.1 wheel with pip.

Use an absolute `--config` path if you start from another directory. In JSON game paths, use forward slashes, for example `D:/Games/World of Warcraft/your-client-folder`. Replace that example with the actual folder containing `WTF` and `Logs`; do not use `Interface/AddOns` or the parent launcher folder. Run `forever-ha --discover` to check detected installations; use a manual path if yours is missing.

Use Home Assistant's base URL, such as `http://homeassistant.local:8123`, with no dashboard, `/hacs`, or `/api/websocket` suffix. For a Nabu Casa URL, use `https://YOUR-HOST.ui.nabu.casa` with no page path.

A token environment variable must be set in the terminal that starts the companion. `--prompt-token` avoids that requirement. Create an administrator long-lived access token from your Home Assistant profile; do not put it in `config.json` or share it in error reports. Restart Home Assistant after installing the integration and add **WoW Forever** under **Settings → Devices & services** before running the companion. HACS downloading alone does not configure it.

You may instead set `FOREVER_HA_TOKEN` in the environment through your service manager or terminal. Credentials are never written into the addon or SQLite outbox. Keep your service configuration private.

The companion polls every second, batches up to 128 raw records per envelope, and sends ordered batches. It keeps a transactional retry queue and file checkpoints in the platform's local application-state directory. Read errors and dropped-record counts appear on the companion device. Stop it with Ctrl+C. An expired token stops the process with queued records preserved; update the token and restart.

## Retrieve complete datasets

Use the same configuration and token environment. Find your character GUID in its SavedVariables record or Home Assistant device identifier, then:

```sh
forever-ha --config config.json --dataset bags --character-guid Player-REALM-ID --output bags.json
forever-ha --config config.json --dataset recipes --character-guid Player-REALM-ID --output recipes.json
forever-ha --config config.json --dataset combat_log --installation gaming-pc --offset 0 --limit 200 --output combat.json
```

`Player-REALM-ID` is a placeholder. Snapshot results contain a versioned envelope with the collector record inside `payload`, and its actual data inside `payload.payload`. Raw log queries are paginated, installation-wide, and include original text and parsing diagnostics. Snapshot addon events are character-specific.

Non-UTF-8 Lua byte strings are represented as `{"__lua_bytes_base64":"..."}` so binary addon messages survive export.

## Settings and limits

`/foreverha disable <category>` and `/foreverha enable <category>` control snapshots. Category names appear in `/foreverha status`. `events` controls the snapshot event buffer. To stop live chat or combat forwarding, set `chat_log` or `combat_log` to `false` in the companion configuration; snapshot switches do not disable game logging.

The default retry queue is **20,000 envelopes / 100 MiB**. If it overflows, the oldest queued envelopes are dropped and the lifetime loss counter increases. A single envelope is limited to **4 MiB**; larger datasets are rejected with the same visible loss counter. Large accepted snapshots use fragments to stay below Home Assistant's inbound WebSocket frame limit.

Home Assistant retains **2,000 records / 2 MiB per raw dataset** and reports `retention_dropped`. Its integration storage is capped at 128 MiB. The addon retains 5,000 events per character and reports overwritten events. These limits are explicit: this is an observational sync, not an unlimited archive. Existing game logs remain the authoritative raw files; this program never rotates or deletes them.

Logs are imported from their beginning by default. Set `import_history: false` for a new installation to start at the end of existing files. Persisted checkpoints take precedence thereafter. Historical automation events are marked; the sample automation ignores them.

Set `log_timezone` to the gaming computer's IANA timezone, for example `America/Los_Angeles`. WoW log timestamps may omit the year: it is inferred from file modification time and marked `year_inferred`. A timezone is especially useful for archived logs across daylight-saving changes.

## Verification and development

See [verification](docs/VERIFICATION.md) for measured results and the pending in-game checklist. The repository includes a Windows/macOS/Linux CI matrix and a separate Home Assistant 2026.10.1 job.

```sh
python -m pip install ./companion pytest lupa
python -m pytest -q tests
```

Install `homeassistant==2026.10.1` in a separate Python 3.14 development environment to run the integration-specific tests. Do not install Home Assistant into your running server's environment just to test this package.

The implementation uses ordinary addon APIs and file reads. It does not hook the client, inspect memory, intercept packets, automate input, transmit synthetic chat telemetry, or draw gameplay overlays. Blizzard approval cannot be guaranteed. [Blizzard's statement about combat-log gameplay overlays](https://us.forums.blizzard.com/en/wow/t/nerfing-combat-logs-will-it-happen-should-it-happen/1654695/11) is relevant to how exported data is used.
