# Login crash hotfix — addon 0.1.1

## What the reports establish

All three supplied text reports identify official Forever client **1.60.1.70338 / Camelot**, `BC_ASSERT(this->m_has_value)`, and this Lua stack from version 0.1.0:

```text
#1 [C]: in function 'resume'
#2 Interface/AddOns/ForeverHA/Core.lua:166: in function 'tick'
#3 Interface/AddOns/ForeverHA/Events.lua:75: in function <...>
```

The third report lists ForeverHA as the only loaded addon. The old code at Core.lua:166 calls `coroutine.resume`. This identifies the scheduler's entry point during the fatal failure. The reports do not name the active collector or establish which native operation inside/resuming that thread asserted. Private WoW symbols would be needed to resolve the native addresses in the dump to internal function names.

The generic Lua interpreter tests used for the first release do not emulate WoW's native scripting context. Passing those tests did not establish that this coroutine path was compatible with the client.

## Changes

- Removed every coroutine API call: no create, resume, yield, status or secondary Lua threads.
- Replaced scans with an explicit queue processed from the ordinary frame callback, at most 20 work items per tick. Nested scans and recipe finalization wait for their queued rows to finish.
- Defer initialization/collection until two seconds after entering the world; cancel unfinished work while leaving/loading. Logout saves existing observations without making fresh data-getter calls.
- Require the built-in MountJournal, PetJournal, ToyBox, Wardrobe or Achievement window before scanning the corresponding backend.
- Guard nil rank arguments and quest-header IDs, skip durability on empty slots, and use purchased/viewable bank tab IDs rather than guessed indices. These are additional preventative fixes, not a confirmed identification of the reported native assertion.

The wire schema, companion and Home Assistant integration are unchanged. This release is a code-level hotfix with offline regression coverage; confirmation in the actual Forever client is still required.

## Upgrade

1. Close WoW completely.
2. Replace the entire `Interface/AddOns/ForeverHA` folder with the folder in **ForeverHA-addon-0.1.1.zip**. Replacing the folder avoids mixing scripts from different versions.
3. Keep `WTF/Account/.../SavedVariables/ForeverHA.lua`; existing data and settings use the same schema. Do not delete it to apply this hotfix.
4. Log in. Leave the character in-world long enough for collection, then check `/foreverha status`.
5. Open collection windows individually for their conditional snapshots. Reload only after collection has completed; logout no longer forces fresh scans.

If a new native crash occurs, its latest report is needed: the `resume` entry point is absent from 0.1.1. Do not repeatedly test the known-crashing 0.1.0 build.

Only the addon needs replacement. The existing companion 0.1.0 and Home Assistant integration 0.1.0 can remain installed.
