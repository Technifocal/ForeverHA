# Verification

## Executed here

The test suite was run on macOS arm64 using Python 3.14, a real Lua 5.1 interpreter through Lupa, and the actual Home Assistant Core 2026.10.1 package. The original run passed 64 tests with one upstream Home Assistant deprecation warning. Generated audit and test-output files are excluded from Git; the source tests remain reproducible.

The suite covers:

- Lua collection, unavailable optional APIs, unsupported required APIs, secret values that throw if compared/stringified, frame work-queue pacing, closed-window freshness, disable switches, event overflow, explicit logging commands, and logout behavior.
- Literal SavedVariables parsing, UTF-8/escaped characters, sparse tables, malicious/executable input rejection, incomplete writes and nesting bounds.
- File partial writes, Unicode split across writes, read checkpoints across process restart, truncation, replacement, copy-truncate growth, rename without replay, large-line recovery and raid-log batching.
- Standard damage/healing/death parsing, preserved unknown advanced layouts, encounter duration/outcome metrics, missing-metric handling and raw retention pagination.
- Ordered retries, bounded outbox overflow, transaction rollback, duplicate application after lost acknowledgement, expired tokens and incomplete acknowledgement.
- Actual Home Assistant command handlers, configuration flow, storage/restore, administrator enforcement, entity properties, disconnected live sensors, omitted secret fields, failed persistence without acknowledgement, and fragmented snapshot retry.

The standard combat fixtures are synthetic fixtures matching supported layouts. They are not captured Forever combat logs. Advanced combat layouts are intentionally not interpreted into amounts without validated client samples.

Additional 0.1.1 regressions replace coroutine functions with throwing stubs, verify nested scans complete without accessing them, test delayed login/loading cancellation, and exercise nil-rank, quest-header and journal-window guards. The user's three crash reports establish the old scheduler crash entry point, not an in-game pass for the replacement.

## Not executed here

There was no running WoW Forever client, no deployed Home Assistant server connected to the user's game installation, and no Windows/Linux runner. The supplied GitHub Actions matrix runs the portable suite on all three operating systems and Python 3.11/3.14, plus the Home Assistant suite on Linux/Python 3.14. That workflow has not been dispatched or claimed as passed.

Offline API/source checks do not validate every Lua call's runtime signature, feature availability, UI load conditions, or client logging latency. The custom integration was exercised against Home Assistant's real Python interfaces and storage, with a separate real WebSocket server used for companion transport tests; this is not a full deployed Home Assistant HTTP/WebSocket smoke test.

## In-game acceptance checklist

1. Install in the official Forever client. Record `/foreverha status` and the `diagnostics` dataset; verify client build, interface and absence of addon errors.
2. Test at login, out of combat, during combat, after death/resurrection, and during an encounter. Confirm secret values are omitted, and no restricted combat event subscription is attempted.
3. Change money, XP, equipment and bag contents; reload after collection completes. Verify values and observation timestamps in Home Assistant.
4. Visit bank, mailbox, auction house and multiple professions. Close each window, wait, reload, and confirm the closed view keeps its original observation time. Check recipe accumulation and filtered-view scope.
5. Enable logging; capture real Forever standard and advanced log samples. Measure disk-flush latency and compare recognized totals with the records. Do not enable an advanced amount parser based solely on a retail sample.
6. Switch characters/accounts. Confirm per-character snapshots remain distinct while matching GUIDs unify their log statistics.
7. Stop/restart the companion and Home Assistant during a batch. Confirm no double-counted counters, pending outbox replay, and disconnected live sensor availability.
8. Repeat file-watching and transport smoke tests on Windows and Linux/Wine/Proton paths.

Record results and update the coverage table before labeling this release validated in-game.

## 0.2.0 validation

New tests cover Lua-to-pixel-to-Python roundtrips, Unicode, fractional block widths and contrast, CRC corruption, queue bounds, texture reuse, urgent death preemption, repeated/missing/out-of-order chunks, message ID reuse, DOINK namespace isolation, secret/null values, CPU/handle limits, thread cleanup, source-ID preservation and HA deduplication persisted across restart. A Windows-only GDI allocation/resize leak test is included in the existing CI matrix; it is skipped on macOS. No live Forever client or Windows capture latency/CPU measurement is available locally.

Local release run: 96 passed, one Windows-only GDI test skipped, one upstream Home Assistant deprecation warning. This does not substitute for the live Windows/Forever smoke test.
