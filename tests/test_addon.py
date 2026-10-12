from conftest import collect


def test_character_and_missing_optional_apis(addon):
    record = collect(addon, "character")
    assert record.payload.level == 60
    assert record.payload.money_copper == 120000
    assert record.status == "partial"
    assert record.missing.UnitClass


def test_secret_values_never_branch_stringify_or_persist(addon):
    addon.execute("function UnitHealth() return SECRET end; function UnitHealthMax() return 100 end")
    record = collect(addon, "stats")
    assert record.status == "restricted"
    assert record.payload.health is None
    assert record.payload.health_max == 100
    assert record.secret_fields == 1
    addon.execute("assert(F.clean({nested=SECRET,good=1}).nested == nil)")


def test_collector_failure_does_not_stop_following_collection(addon):
    addon.execute('F.add("broken",{},function() error("oops") end); F.enqueue("broken"); F.enqueue("character")')
    for _ in range(10):
        addon.execute("F.tick()")
    assert addon.globals().F.character.datasets.broken.status == "error"
    assert addon.globals().F.character.datasets.character.payload.level == 60


def test_window_only_data_preserves_observation_when_closed(addon):
    record = collect(addon, "bank")
    assert record.status == "unobserved"
    addon.execute('F.windows.bank=true; Enum={BagIndex={Bank=-1}}; C_Container={GetContainerNumSlots=function() return 0 end, GetBagName=function() return "Bank" end, GetContainerNumFreeSlots=function() return 0 end}')
    record = collect(addon, "bank")
    assert record.observed_at == 1000
    addon.execute("_clock=2000; F.windows.bank=false")
    record = collect(addon, "bank")
    assert record.observed_at == 1000


def test_large_scans_yield_and_disable_stops_running_collector(addon):
    addon.execute('C_Reputation={GetNumFactions=function() return 100 end, GetFactionDataByIndex=function(i) return {factionID=i} end}; F.enqueue("reputations"); F.tick()')
    assert addon.eval("F.running ~= nil")
    addon.execute('SlashCmdList.FOREVERHA("disable reputations"); F.tick()')
    assert addon.globals().F.character.datasets.reputations.status == "disabled"


def test_unsupported_status_and_no_restricted_combat_subscription(addon):
    assert collect(addon, "quests").status == "unsupported"
    assert not addon.globals().F.frame.events.COMBAT_LOG_EVENT_UNFILTERED
    assert not addon.globals().F.frame.events.COMBAT_LOG_EVENT


def test_event_retention_and_logging_only_on_user_request(addon):
    addon.execute('logged=0; function LoggingCombat(v) if v~=nil then logged=logged+1 end return false end; function LoggingChat(v) if v~=nil then logged=logged+1 end return false end; F.eventLimit=2; F.record("one"); F.record("two"); F.record("three")')
    assert addon.globals().F.character.dropped_events == 1
    assert len(addon.globals().F.character.events) == 2
    assert addon.globals().logged == 0
    addon.execute('SlashCmdList.FOREVERHA("logging on")')
    assert addon.globals().logged == 2


def test_logout_preserves_completed_records_and_performs_no_fresh_scan(addon):
    collect(addon, "character")
    collect(addon, "diagnostics")
    addon.execute('function UnitLevel() error("must not query at logout") end')
    for _ in range(3):
        addon.execute('F.frame.scripts.OnEvent(F.frame,"PLAYER_LOGOUT")')
    record = addon.globals().F.character.datasets.diagnostics
    assert record.payload.capabilities.character.payload is None
    assert addon.globals().F.character.saved_at == 1000
    assert addon.globals().F.character.datasets.character.payload.level == 60


def test_no_coroutine_engine_calls_even_with_nested_scans(addon):
    addon.execute('''
        coroutine = setmetatable({}, {__index=function() error("coroutine API must never be used") end})
        AchievementFrame={IsShown=function() return true end}
        function GetCategoryList() return {1,2} end
        function GetCategoryInfo(id) return "Category" end
        function GetCategoryNumAchievements(id) return 25 end
        function GetAchievementInfo(category,index) return category*100+index,"Test" end
        function GetAchievementNumCriteria(id) return 3 end
        function GetAchievementCriteriaInfo(id,index) return "Criterion",index end
    ''')
    record = collect(addon, "achievements")
    assert len(record.payload.categories) == 2
    assert len(record.payload.categories[1].achievements) == 25
    assert len(record.payload.categories[1].achievements[1].criteria) == 3


def test_login_defers_api_collection_until_world_ready(addon):
    addon.execute('''
        F.character=nil
        unitCalls=0
        function UnitGUID() unitCalls=unitCalls+1; return "Player-1-ABC" end
        F.frame.scripts.OnEvent(F.frame,"PLAYER_LOGIN")
        F.frame.scripts.OnUpdate(F.frame,5)
        assert(unitCalls==0)
        F.frame.scripts.OnEvent(F.frame,"PLAYER_ENTERING_WORLD")
        F.frame.scripts.OnUpdate(F.frame,1)
        assert(unitCalls==0)
        F.frame.scripts.OnUpdate(F.frame,1)
        assert(unitCalls>0 and F.worldReady)
    ''')


def test_leaving_world_cancels_pending_jobs_without_native_calls(addon):
    addon.execute('''
        C_Reputation={GetNumFactions=function() return 100 end,GetFactionDataByIndex=function(i) return {factionID=i} end}
        F.enqueue("reputations");F.tick()
        F.frame.scripts.OnEvent(F.frame,"PLAYER_LEAVING_WORLD")
        C_Reputation.GetFactionDataByIndex=function() error("transition query") end
        F.tick()
        assert(not F.running and #F.queue==0)
    ''')


def test_nil_rank_and_quest_header_never_reach_native_getters(addon):
    addon.execute('''
        rankCalls=0
        function UnitPVPRank() return nil end
        function GetPVPRankInfo(rank) rankCalls=rankCalls+1; error("would assert in client") end
        C_QuestLog={GetNumQuestLogEntries=function() return 2 end,
            GetInfo=function(i) return i==1 and {isHeader=true,questID=0} or {questID=10} end,
            GetQuestObjectives=function(id) assert(id==10); return {} end,
            GetTimeAllowed=function(id) assert(id==10); return 60 end,
            GetAllCompletedQuestIDs=function() return {} end}
    ''')
    collect(addon, "pvp")
    assert addon.globals().rankCalls == 0
    assert collect(addon, "quests").status == "available"
    addon.execute('F.current={apis={},missing={},failed={}};F.call("GetPVPRankInfo",nil);assert(rankCalls==0 and F.current.skipped.GetPVPRankInfo=="nil_argument")')


def test_collection_symbols_are_not_called_before_their_window_opens(addon):
    addon.execute('''
        journalCalls=0
        C_PetJournal={GetOwnedPetIDs=function() journalCalls=journalCalls+1;return {} end}
    ''')
    record = collect(addon, "pets")
    assert addon.globals().journalCalls == 0
    assert record.skipped["C_PetJournal.GetOwnedPetIDs"] == "open_PetJournal"
    addon.execute('PetJournal={IsShown=function() return true end}')
    collect(addon, "pets")
    assert addon.globals().journalCalls == 1


def test_recipe_merge_waits_for_deferred_work(addon):
    addon.execute('''
        F.windows.profession=true
        C_TradeSkillUI={GetAllRecipeIDs=function() return {10,20} end,
            GetBaseProfessionInfo=function() return {professionID=1} end,
            GetRecipeInfo=function(id) return {recipeID=id} end,
            GetRecipeSchematic=function(id) return {recipeID=id} end}
    ''')
    record = collect(addon, "recipes")
    assert record.payload.professions["1"].recipes["10"].info.recipeID == 10
    assert record.payload.professions["1"].recipes["20"].schematic.recipeID == 20


def test_bank_uses_only_purchased_viewable_tab_ids(addon):
    addon.execute('''
        F.windows.bank=true
        Enum={BankType={Character=0,Account=2},BagIndex={Keyring=-1,CharacterBankTab_9=14,AccountBankTab_9=23}}
        C_Bank={CanViewBank=function(kind) return kind==0 end,
            FetchPurchasedBankTabIDs=function(kind) assert(kind==0);return {6} end}
        C_Container={GetContainerNumSlots=function(id) assert(id==0 or id==6);return 0 end,
            GetBagName=function() return "Bank" end,GetContainerNumFreeSlots=function() return 0 end}
    ''')
    result = collect(addon, "bank")
    assert len(result.payload) == 1
    assert result.payload[1].bag == 6


def test_wardrobe_queries_selected_initialized_category_only(addon):
    addon.execute('''
        WardrobeCollectionFrame={IsShown=function() return true end,
            ItemsCollectionFrame={activeCategory=1,transmogLocation={GetData=function() return {slotID=1,type=0,modification=0} end}}}
        Enum={TransmogCollectionType={Head=1,Paired=29,Count=30}}
        C_TransmogCollection={GetCategoryInfo=function(id) assert(id==1);return "Head" end,
            GetCategoryAppearances=function(id,location) assert(id==1 and location.slotID==1);return {{id=100}} end}
    ''')
    result = collect(addon, "appearances")
    assert len(result.payload) == 1
    assert result.payload[1].appearances[1].id == 100


def test_absent_pet_never_queries_pet_happiness(addon):
    addon.execute('''
        function UnitExists() return false end
        C_PetInfo={GetPetHappiness=function() error("no pet") end,GetPetLoyalty=function() error("no pet") end}
    ''')
    result = collect(addon, "pets")
    assert not result.failed["C_PetInfo.GetPetHappiness"]


def test_opening_a_journal_enqueues_a_scan_without_waiting_sixty_seconds(addon):
    addon.execute('''
        C_MountJournal={GetMountIDs=function() return {1} end,
            GetMountInfoByID=function() return "Horse" end,GetMountInfoExtraByID=function() return 10 end}
        MountJournal={IsShown=function() return true end}
        F.frame.scripts.OnUpdate(F.frame,0.5)
    ''')
    for _ in range(5):
        addon.execute("F.tick()")
    result = addon.globals().F.character.datasets.mounts
    assert result.payload[1].info[1] == "Horse"


def test_missing_optional_pet_journal_keeps_active_pet(addon):
    addon.execute('function UnitName(unit) return unit == "pet" and "Imp" or "Tester" end')
    result = collect(addon, "pets")
    assert result.payload.active.name == "Imp"
    assert result.status == "partial"
    assert result.missing["C_PetJournal.GetOwnedPetIDs"]


def test_missing_pvp_scoreboard_keeps_supported_flags(addon):
    addon.execute('C_PvP={IsBattleground=function() return false end}')
    result = collect(addon, "pvp")
    assert result.payload.active_battleground is False
    assert result.status == "partial"


def test_future_schema_is_not_overwritten(addon):
    addon.execute('ForeverHADB={schema=2,keep="valuable"}; assert(not F.initialize())')
    assert addon.globals().ForeverHADB.schema == 2
    assert addon.globals().ForeverHADB.keep == "valuable"
