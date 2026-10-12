# Data coverage

Static audit: [Forever source commit 9437644](https://github.com/Gethe/wow-ui-source/commit/943764493e6b16d63ded3ab304150d1f05e58b57), build 1.60.1 (70338). The source mirror contains UI code for multiple game flavors: a declaration or shared UI use does **not** prove a feature is enabled in Forever. Each collector probes the actual client and records missing APIs, failures, omitted secret fields, scope, and timestamps.

The generated `docs/api-audit.json` provides exact API names, source evidence, restrictions, event metadata, and dataset routes. It is excluded from Git and can be reproduced with `tools/audit_apis.py` and a local copy of the pinned source. Some UI-object/constant metadata files cannot be evaluated without the game environment; the audit lists them explicitly. They are not evidence of runtime support.

## Snapshot collectors

Every row below reaches Home Assistant through **SavedVariables on logout/reload**. In-memory collection starts two seconds after entering the world, on relevant events, and through a paced refresh every 60 seconds. A collector status may be `available`, `partial`, `restricted`, `unobserved`, `unsupported`, `disabled`, or `error`.

| Dataset | Principal APIs | Scope / conditions |
|---|---|---|
| `character` | `UnitGUID`, `UnitClass`, `UnitRace`, `UnitLevel`, `UnitXP`, `GetMoney` | Identity, progression, rested XP, money, title; `/played` results when received |
| `location` | `C_Map.GetBestMapForUnit`, `C_Map.GetPlayerMapPosition`, `UnitPosition`, `GetInstanceInfo` | Zone, subzone, map coordinates and instance; inaccessible coordinates omitted |
| `stats` | `UnitHealth`, `UnitPower`, `UnitStat`, `UnitArmor`, `UnitDamage` | Accessible health, power, attributes, attacks, defensive stats and activity flags; secret values omitted |
| `equipment` | `GetInventoryItemLink`, `GetInventoryItemID`, `GetInventoryItemDurability`, `C_Item.GetItemInfo` | Slots 1–19; item metadata can be uncached |
| `bags` | `C_Container.GetContainerNumSlots`, `GetContainerItemInfo` | Equipped bags; item records, free slots and durability |
| `bank` | `C_Container` bag APIs, `Enum.BagIndex` | Only while bank is open; purchased/viewable bank tab IDs (or a declared legacy Bank index), without guessing container numbers; previous snapshot kept when closed |
| `currencies` | `C_CurrencyInfo.GetCurrencyListSize`, `GetCurrencyListInfo` | Visible currency-list rows; collapsed UI rows may be absent; marked partial |
| `quests` | `C_QuestLog.GetInfo`, `GetQuestObjectives`, `GetAllCompletedQuestIDs` | Active quest info/objectives plus accessible completed IDs |
| `reputations` | `C_Reputation.GetNumFactions`, `GetFactionDataByIndex` | Visible faction-list rows; collapsed rows may be absent; marked partial |
| `spells` | `C_SpellBook` skill-line/item APIs | Spellbook entries, names and accessible cooldowns |
| `talents` | Legacy talent getters or `C_ClassTalents` / `C_Traits` | Active supported talent tree/configuration; absent systems reported |
| `professions` | `C_SkillInfo` or legacy skill/profession getters | Known skill lines and profession information |
| `recipes` | `C_TradeSkillUI.GetAllRecipeIDs` or `GetFilteredRecipeIDs`, `GetRecipeInfo`, `GetRecipeSchematic` | Profession window open; accumulates observed recipes across professions, with per-recipe timestamps; filtered fallback marked partial |
| `achievements` | `GetCategoryList`, `GetAchievementInfo`, `GetAchievementCriteriaInfo` | Categories, criteria and points, while the built-in achievement window is visible |
| `mounts` | `C_MountJournal.GetMountIDs`, mount info getters | Visible MountJournal entries; owned flags retained |
| `pets` | `UnitGUID('pet')`, `C_PetInfo`, `C_PetJournal.GetOwnedPetIDs` | Active pet; owned-pet journal only while PetJournal is visible |
| `collections` | `C_ToyBox` enumeration / info getters | Visible ToyBox filtered view; marked partial |
| `appearances` | `Enum.TransmogCollectionType`, `C_TransmogCollection.GetCategoryAppearances` | Currently selected, initialized wardrobe category; accumulated observed categories retain timestamps |
| `lockouts` | `GetNumSavedInstances`, `GetSavedInstanceInfo`, encounter getters | Cached instance lockouts and encounter completion |
| `pvp` | PvP history/rank getters, `C_PvP.IsBattleground`, battlefield score getters | Supported honor/rank history and cached scoreboard; flavor-dependent APIs |
| `mail` | `GetInboxNumItems`, inbox header/text/item getters | Mailbox open; loaded inbox, bodies and attachments; no opening, claiming or sending mail |
| `auctions` | `C_AuctionHouse` owned/bid/browse getters, or legacy auction getters | Auction window open; cached lists only; no searches, bids, purchases or listings |
| `guild` | `GetGuildInfo`, guild roster getters | Cached roster, ranks, notes, MOTD and guild information visible to the player |
| `friends` | `C_FriendList`, `C_BattleNet.GetFriendAccountInfo` | Accessible friends, ignores and Battle.net friend records |
| `group` | `GetNumGroupMembers`, `UnitGUID`, `UnitFullName`, group role getters | Party/raid identity and roles visible to the player |
| `auras` | `C_UnitAuras.GetAuraDataByIndex` | Accessible helpful/harmful player auras; secret fields omitted |
| `diagnostics` | `GetBuildInfo`, `GetLocale`, `GetNetStats`, logging/status getters | Build, locale, network/framerate snapshot, collector capabilities and loss counts |
| `addon_events` | Standard events delivered to the addon | Accessible chat, loot, quest, death, encounter and addon-message arguments; secret arguments omitted and counted; bounded event history |
| `export_status` | Addon metadata | Last saved time, character label and overwritten-event count; generated by companion |

Closed-window data retains its original observation time. Cached server data may have been last refreshed before the collector ran; collection time records when this addon saw it, not a guarantee that the server refreshed every underlying field then. Filters and visibility are not changed by the addon.

Addon messages are limited to events the game delivers. ForeverHA does not subscribe to every unknown message prefix or inspect other addons' internal data. All broad categories are attempted; this does not mean every client subsystem exists or every API is readable.

## Live file routes

| Dataset | Route | Supported behavior |
|---|---|---|
| `combat_log` | `LoggingCombat` / `/combatlog` → `Logs/WoWCombatLog*.txt` | Original lines, recognized common combat prefix, timestamps, source/destination GUIDs and flags, supported metrics, encounter records |
| `chat_log` | `LoggingChat` / `/chatlog` → `Logs/WoWChatLog*.txt` | Original lines and timestamped text the game actually logs |
| `combat_statistics` | Derived from recognized file records | Logged damage/healing done/taken, deaths, successful casts, interrupts and unparsed-statistic counts for own/previously known characters |
| `encounter_statistics` | Explicit start/end file records | Started/completed/won/failed counts, last encounter, matched duration, explicitly logged active encounter; no timeout inference |
| `last_encounter` | Matched start/end and recognized actor records | Observed character totals and duration; DPS/HPS omitted when that metric is unobserved or the encounter has unparsed statistic records |
| `file_status` | Companion file reader | Rotation and oversized-line loss diagnostics |

Parsing supports common timestamped CSV records with the standard common prefix. Standard swing/spell/ranged damage and healing shapes are recognized conservatively. Advanced logging layouts that insert GUID/position metadata before amounts are retained raw and marked `statistics_unparsed`. This version does not guess an advanced damage layout without a validated sample from the actual Forever client. Unknown event types and log-version headers also remain raw.

Live export means **after disk flush**, not direct access to WoW's in-memory state. A quiet log does not prove offline, out of combat, or alive. Combat data hidden by the client stays hidden. Neither secret values nor restricted in-game combat events are used as a bypass.

## Unavailable by design

Arbitrary live gold/bag/quest export cannot be achieved through SavedVariables alone. There are no client hooks, memory reads, packet interception, screen decoding, synthetic chat transport, protected gameplay operations, automated reloads, or API restriction bypasses. Hidden server data and unvisited conditional views cannot be exported.

Blizzard's [Forever combat API definitions](https://raw.githubusercontent.com/Gethe/wow-ui-source/forever/Interface/AddOns/Blizzard_APIDocumentationGenerated/CombatLogDocumentation.lua) flag both combat event subscriptions as restricted. The addon does not register them. Its ordinary read APIs are sanitized before inspection/persistence; the audit found no `HasRestrictions` read API or event registration in the implemented collector set at the pinned source build.

## 0.1.1 crash mitigation

No coroutine API is called. Deferred work runs on normal frame callbacks (at most 20 work items per tick), including nested achievement/recipe scans. Data getters are paused while entering/leaving the world and are not refreshed during logout. Collection backend queries require the relevant built-in window. Explicit nil arguments, quest headers with ID zero, empty-slot durability queries and guessed bank indices are excluded. These changes remove the entry point shown in the supplied crash reports; live-client confirmation remains pending.
