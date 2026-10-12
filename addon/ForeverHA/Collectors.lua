local _, F = ...
local function tuple(path, ...) return { F.call(path, ...) } end
local function count(path, ...)
    local n = F.require(path, ...)
    if type(n) ~= "number" then error("unobserved:" .. path, 0) end
    return n
end
local function indexed(n, fn)
    local rows = {}
    for i = 1, n do
        local index = i
        F.defer(function() rows[index] = fn(index) end)
    end
    return rows
end
local function ids(path, fn, ...)
    local values = F.require(path, ...)
    if type(values) ~= "table" then error("unobserved:" .. path, 0) end
    local rows, n = {}, 0
    for _, id in pairs(values) do
        n = n + 1
        local index, value = n, id
        F.defer(function() rows[index] = fn(value) end)
    end
    return rows
end

F.add("character", {"PLAYER_LEVEL_UP", "PLAYER_XP_UPDATE", "UPDATE_EXHAUSTION", "PLAYER_MONEY"}, function()
    return {
        guid = F.require("UnitGUID", "player"), name = F.call("UnitName", "player"),
        realm = F.call("GetRealmName"), class = tuple("UnitClass", "player"),
        race = tuple("UnitRace", "player"), faction = tuple("UnitFactionGroup", "player"),
        level = F.call("UnitLevel", "player"), sex = F.call("UnitSex", "player"),
        xp = F.call("UnitXP", "player"), xp_max = F.call("UnitXPMax", "player"),
        rested_xp = F.call("GetXPExhaustion"), money_copper = F.call("GetMoney"),
        played = F.played, title = F.call("GetCurrentTitle"),
    }
end)

F.add("location", {"ZONE_CHANGED_NEW_AREA", "ZONE_CHANGED", "ZONE_CHANGED_INDOORS"}, function()
    local map = F.call("C_Map.GetBestMapForUnit", "player")
    local x, y
    if map then
        -- Vector2D is a game object; explicitly extract its scalar coordinates.
        local ok, position = pcall(function() return C_Map.GetPlayerMapPosition(map, "player") end)
        if ok and position and not (issecretvalue and issecretvalue(position)) then
            local good, px, py = pcall(function() return position:GetXY() end)
            if good then x, y = F.clean(px), F.clean(py) end
        end
    end
    return { zone = F.require("GetZoneText"), subzone = F.call("GetSubZoneText"),
        map_id = map, x = x, y = y, instance = tuple("GetInstanceInfo"),
        position = tuple("UnitPosition", "player"), }
end)

F.add("stats", {"UNIT_STATS", "UNIT_HEALTH", "UNIT_MAXHEALTH", "UNIT_POWER_UPDATE", "PLAYER_REGEN_ENABLED"}, function()
    local attributes = indexed(4, function(i) return tuple("UnitStat", "player", i) end)
    return { health = F.call("UnitHealth", "player"), health_max = F.call("UnitHealthMax", "player"),
        power = F.call("UnitPower", "player"), power_max = F.call("UnitPowerMax", "player"),
        power_type = tuple("UnitPowerType", "player"), attributes = attributes,
        armor = tuple("UnitArmor", "player"), damage = tuple("UnitDamage", "player"),
        attack_power = tuple("UnitAttackPower", "player"), speed = tuple("UnitAttackSpeed", "player"),
        crit = F.call("GetCritChance"), haste = F.call("GetHaste"),
        dodge = F.call("GetDodgeChance"), parry = F.call("GetParryChance"), block = F.call("GetBlockChance"),
        dead = F.call("UnitIsDeadOrGhost", "player"), mounted = F.call("IsMounted"),
        combat = F.call("UnitAffectingCombat", "player"), }
end)

F.add("equipment", {"PLAYER_EQUIPMENT_CHANGED", "UPDATE_INVENTORY_DURABILITY", "GET_ITEM_INFO_RECEIVED"}, function()
    F.require("GetInventoryItemLink", "player", 1)
    return indexed(19, function(slot)
        local link = F.call("GetInventoryItemLink", "player", slot)
        local durability, maximum
        if link then durability, maximum = F.call("GetInventoryItemDurability", slot) end
        return { slot = slot, link = link, id = F.call("GetInventoryItemID", "player", slot),
            durability = durability, durability_max = maximum,
            item = link and tuple("C_Item.GetItemInfo", link), }
    end)
end)

local function containers(bagIDs)
    F.require("C_Container.GetContainerNumSlots", 0)
    local rows = {}
    for _, bag in ipairs(bagIDs) do
        local n = F.call("C_Container.GetContainerNumSlots", bag)
        if type(n) == "number" then
            rows[#rows + 1] = { bag = bag, name = F.call("C_Container.GetBagName", bag),
                slots = n, free = F.call("C_Container.GetContainerNumFreeSlots", bag),
                items = indexed(n, function(slot)
                    return { slot = slot, info = F.call("C_Container.GetContainerItemInfo", bag, slot),
                        -- Empty bag slots need no durability backend lookup.
                        durability = F.call("C_Container.GetContainerItemID", bag, slot) and
                            tuple("C_Container.GetContainerItemDurability", bag, slot) or nil }
                end) }
        end
        F.pause()
    end
    return rows
end
F.add("bags", {"BAG_UPDATE_DELAYED", "GET_ITEM_INFO_RECEIVED"}, function()
    local bagIDs = {}
    for i = 0, (NUM_TOTAL_EQUIPPED_BAG_SLOTS or NUM_BAG_SLOTS or 4) do bagIDs[#bagIDs + 1] = i end
    return containers(bagIDs)
end)
F.add("bank", {"BANKFRAME_OPENED", "PLAYERBANKSLOTS_CHANGED", "BAG_UPDATE_DELAYED", "BANK_TABS_CHANGED"}, function()
    local bagIDs, used = {}, {}
    if Enum and Enum.BankType and type(F.resolve("C_Bank.FetchPurchasedBankTabIDs")) == "function" then
        for _, kind in ipairs({Enum.BankType.Character, Enum.BankType.Account}) do
            if F.call("C_Bank.CanViewBank", kind) == true then
                for _, id in pairs(F.call("C_Bank.FetchPurchasedBankTabIDs", kind) or {}) do
                    if type(id) == "number" and id >= 0 and not used[id] then
                        bagIDs[#bagIDs + 1], used[id] = id, true
                    end
                end
            end
        end
    elseif Enum and Enum.BagIndex and type(Enum.BagIndex.Bank) == "number" then
        bagIDs[1] = Enum.BagIndex.Bank
    end
    if #bagIDs == 0 then error("unobserved:bank_container_ids", 0) end
    table.sort(bagIDs)
    return containers(bagIDs)
end, "bank")

F.add("currencies", {"CURRENCY_DISPLAY_UPDATE"}, function()
    F.current.partial, F.current.scope = true, "ui_visible_rows"
    return indexed(count("C_CurrencyInfo.GetCurrencyListSize"), function(i)
        return F.call("C_CurrencyInfo.GetCurrencyListInfo", i)
    end)
end)
F.add("quests", {"QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "QUEST_TURNED_IN"}, function()
    local n = count("C_QuestLog.GetNumQuestLogEntries")
    local rows = indexed(n, function(i)
        local info = F.call("C_QuestLog.GetInfo", i)
        local id = info and not info.isHeader and type(info.questID) == "number" and info.questID > 0 and info.questID or nil
        return { info = info, objectives = id and F.call("C_QuestLog.GetQuestObjectives", id),
            time_left = id and F.call("C_QuestLog.GetTimeAllowed", id), }
    end)
    return { active = rows, completed_ids = F.call("C_QuestLog.GetAllCompletedQuestIDs") }
end)
F.add("reputations", {"UPDATE_FACTION"}, function()
    F.current.partial, F.current.scope = true, "ui_visible_rows"
    return indexed(count("C_Reputation.GetNumFactions"), function(i)
        return F.call("C_Reputation.GetFactionDataByIndex", i)
    end)
end)

F.add("spells", {"SPELLS_CHANGED", "LEARNED_SPELL_IN_TAB"}, function()
    local bank = Enum and Enum.SpellBookSpellBank and Enum.SpellBookSpellBank.Player
    if bank == nil then error("unsupported:Enum.SpellBookSpellBank.Player", 0) end
    return indexed(count("C_SpellBook.GetNumSpellBookSkillLines"), function(i)
        local line = F.call("C_SpellBook.GetSpellBookSkillLineInfo", i)
        if not line then return { index = i } end
        local spells = indexed(line.numSpellBookItems or 0, function(j)
            local slot = (line.itemIndexOffset or 0) + j
            local info = F.call("C_SpellBook.GetSpellBookItemInfo", slot, bank)
            return { slot = slot, info = info, name = tuple("C_SpellBook.GetSpellBookItemName", slot, bank),
                cooldown = F.call("C_SpellBook.GetSpellBookItemCooldown", slot, bank), }
        end)
        return { info = line, spells = spells }
    end)
end)
F.add("talents", {"PLAYER_TALENT_UPDATE", "TRAIT_CONFIG_UPDATED"}, function()
    if type(F.resolve("GetNumTalentTabs")) == "function" then
        return indexed(count("GetNumTalentTabs"), function(tab)
            return { info = tuple("GetTalentTabInfo", tab), talents = indexed(count("GetNumTalents", tab), function(i)
                return tuple("GetTalentInfo", tab, i)
            end) }
        end)
    end
    local config = F.require("C_ClassTalents.GetActiveConfigID")
    if not config then error("unobserved:active_talent_config", 0) end
    local info = F.require("C_Traits.GetConfigInfo", config)
    local trees = {}
    for _, tree in pairs((info or {}).treeIDs or {}) do
        trees[#trees + 1] = { id = tree, nodes = ids("C_Traits.GetTreeNodes", function(node)
            return F.call("C_Traits.GetNodeInfo", config, node)
        end, tree) }
    end
    return { info = info, trees = trees }
end)

F.add("professions", {"SKILL_LINES_CHANGED", "CHAT_MSG_SKILL"}, function()
    if type(F.resolve("C_SkillInfo.GetNumSkillLines")) == "function" then
        return indexed(count("C_SkillInfo.GetNumSkillLines"), function(i) return F.call("C_SkillInfo.GetSkillLineInfo", i) end)
    end
    if type(F.resolve("GetNumSkillLines")) == "function" then
        return indexed(count("GetNumSkillLines"), function(i) return tuple("GetSkillLineInfo", i) end)
    end
    local slots = { F.require("GetProfessions") }
    local rows = {}
    for _, slot in pairs(slots) do rows[#rows + 1] = { index = slot, info = tuple("GetProfessionInfo", slot) } end
    return rows
end)
F.add("recipes", {"TRADE_SKILL_SHOW", "TRADE_SKILL_LIST_UPDATE", "NEW_RECIPE_LEARNED"}, function()
    local enumeration = type(F.resolve("C_TradeSkillUI.GetAllRecipeIDs")) == "function" and
        "C_TradeSkillUI.GetAllRecipeIDs" or "C_TradeSkillUI.GetFilteredRecipeIDs"
    if type(F.resolve(enumeration)) == "function" then
        local profession = F.call("C_TradeSkillUI.GetBaseProfessionInfo") or {}
        local old = F.character.datasets.recipes
        local saved = F.clean(old and old.payload and old.payload.professions or {})
        if enumeration == "C_TradeSkillUI.GetFilteredRecipeIDs" then
            F.current.partial, F.current.scope = true, "ui_filtered"
        end
        local key = tostring(profession.professionID or profession.skillLineID or profession.professionName or "current")
        local prior = saved[key] or { recipes = {} }
        local recipes = ids(enumeration, function(id)
                return { id = id, info = F.call("C_TradeSkillUI.GetRecipeInfo", id),
                    schematic = F.call("C_TradeSkillUI.GetRecipeSchematic", id, false), observed_at = F.now() }
            end)
        saved[key] = prior
        F.afterWork(function()
            for _, recipe in ipairs(recipes) do prior.recipes[tostring(recipe.id)] = recipe end
            prior.profession, prior.observed_at = profession, F.now()
            prior.scope = enumeration == "C_TradeSkillUI.GetFilteredRecipeIDs" and "ui_filtered" or "all_recipe_ids"
        end)
        return { professions = saved }
    end
    return indexed(count("GetNumTradeSkills"), function(i)
        return { info = tuple("GetTradeSkillInfo", i), link = F.call("GetTradeSkillItemLink", i),
            reagents = indexed(count("GetTradeSkillNumReagents", i), function(j)
                return { info = tuple("GetTradeSkillReagentInfo", i, j), link = F.call("GetTradeSkillReagentItemLink", i, j) }
            end) }
    end)
end, "profession")

F.add("achievements", {"ACHIEVEMENT_EARNED", "CRITERIA_UPDATE"}, function()
    if not F.visible("AchievementFrame") then error("unobserved:open_AchievementFrame", 0) end
    local categories = ids("GetCategoryList", function(category)
        return { id = category, info = tuple("GetCategoryInfo", category),
            achievements = indexed(count("GetCategoryNumAchievements", category), function(i)
                local info = tuple("GetAchievementInfo", category, i)
                local id = info[1]
                return { info = info, criteria = id and indexed(count("GetAchievementNumCriteria", id), function(j)
                    return tuple("GetAchievementCriteriaInfo", id, j)
                end) }
            end) }
    end)
    return { points = F.call("GetTotalAchievementPoints"), categories = categories }
end)
F.add("mounts", {"NEW_MOUNT_ADDED", "COMPANION_UPDATE"}, function()
    return ids("C_MountJournal.GetMountIDs", function(id)
        return { id = id, info = tuple("C_MountJournal.GetMountInfoByID", id),
            extra = tuple("C_MountJournal.GetMountInfoExtraByID", id) }
    end)
end)
F.add("pets", {"PET_JOURNAL_LIST_UPDATE", "UNIT_PET"}, function()
    local journal, owned = {}, F.call("C_PetJournal.GetOwnedPetIDs")
    if type(owned) == "table" then
        for _, id in pairs(owned) do
            journal[#journal + 1] = { id = id, info = F.call("C_PetJournal.GetPetInfoTableByPetID", id),
                stats = tuple("C_PetJournal.GetPetStats", id) }
            if #journal % 20 == 0 then F.pause() end
        end
    else
        journal = nil
        F.current.partial, F.current.scope = true, "active_pet_only; journal_unobserved_or_unsupported"
    end
    local exists = F.call("UnitExists", "pet") == true
    return { active = { guid = F.call("UnitGUID", "pet"), name = F.call("UnitName", "pet"),
        health = F.call("UnitHealth", "pet"), health_max = F.call("UnitHealthMax", "pet"), },
        happiness = exists and F.call("C_PetInfo.GetPetHappiness") or nil,
        loyalty = exists and F.call("C_PetInfo.GetPetLoyalty") or nil,
        journal = journal }
end)
F.add("collections", {"TOYS_UPDATED", "TRANSMOG_COLLECTION_UPDATED"}, function()
    F.current.partial, F.current.scope = true, "ui_filtered"
    local enumeration = type(F.resolve("C_ToyBox.GetNumToys")) == "function" and "C_ToyBox.GetNumToys" or "C_ToyBox.GetNumFilteredToys"
    return { scope = "ui_filtered", toys = indexed(count(enumeration), function(i)
        local id = F.call("C_ToyBox.GetToyFromIndex", i)
        return { id = id, owned = id and F.call("PlayerHasToy", id), info = id and tuple("C_ToyBox.GetToyInfo", id) }
    end) }
end)
F.add("appearances", {"TRANSMOG_COLLECTION_UPDATED"}, function()
    if not F.visible("WardrobeCollectionFrame") then error("unobserved:open_WardrobeCollectionFrame", 0) end
    local view = F.resolve("WardrobeCollectionFrame.ItemsCollectionFrame")
    local category = view and F.clean(view.activeCategory)
    local location = view and view.transmogLocation
    if type(category) ~= "number" or category <= 0 or not location or type(location.GetData) ~= "function" then
        error("unobserved:wardrobe_selected_category", 0)
    end
    local ok, data = pcall(location.GetData, location)
    data = ok and F.clean(data) or nil
    if not data then error("unobserved:wardrobe_location", 0) end
    local info = tuple("C_TransmogCollection.GetCategoryInfo", category)
    if not info[1] then error("unobserved:wardrobe_category", 0) end
    local old = F.character.datasets.appearances
    local rows = F.clean(old and old.payload or {})
    local replacement = { category = category, observed_at = F.now(), info = info,
        appearances = F.require("C_TransmogCollection.GetCategoryAppearances", category, data) }
    local found = false
    for i, row in ipairs(rows) do
        if row.category == category then rows[i], found = replacement, true; break end
    end
    if not found then rows[#rows + 1] = replacement end
    F.current.partial, F.current.scope = true, "observed_wardrobe_categories"
    return rows
end)
F.add("lockouts", {"UPDATE_INSTANCE_INFO"}, function()
    return indexed(count("GetNumSavedInstances"), function(i)
        local _, _, _, _, _, _, _, _, _, _, encounters = F.call("GetSavedInstanceInfo", i)
        return { info = tuple("GetSavedInstanceInfo", i), encounters = indexed(encounters or 0, function(j)
            return tuple("GetSavedInstanceEncounterInfo", i, j)
        end) }
    end)
end)
F.add("pvp", {"PVP_RANK_UPDATE", "PLAYER_PVP_KILLS_CHANGED", "UPDATE_BATTLEFIELD_SCORE"}, function()
    local scoreCount = F.call("GetNumBattlefieldScores")
    local rank = F.call("UnitPVPRank", "player")
    local rankInfo
    if type(rank) == "number" and rank > 0 then rankInfo = tuple("GetPVPRankInfo", rank) end
    return { honor = tuple("GetPVPThisWeekStats"), last_week = tuple("GetPVPLastWeekStats"),
        lifetime = tuple("GetPVPLifetimeStats"), rank_index = rank, rank = rankInfo,
        active_battleground = F.call("C_PvP.IsBattleground"),
        active_rated_match = F.call("C_PvP.GetPVPActiveMatchPersonalRatedInfo"),
        honor_points = F.call("UnitHonor", "player"), honor_level = F.call("UnitHonorLevel", "player"),
        scores = type(scoreCount) == "number" and indexed(scoreCount, function(i) return tuple("GetBattlefieldScore", i) end) or nil }
end)

F.add("mail", {"MAIL_SHOW", "MAIL_INBOX_UPDATE"}, function()
    return indexed(count("GetInboxNumItems"), function(i)
        return { index = i, header = tuple("GetInboxHeaderInfo", i),
            body = tuple("GetInboxText", i), attachments = indexed(ATTACHMENTS_MAX_RECEIVE or 16, function(j)
                return { info = tuple("GetInboxItem", i, j), link = F.call("GetInboxItemLink", i, j) }
            end) }
    end)
end, "mail")
F.add("auctions", {"AUCTION_HOUSE_SHOW", "OWNED_AUCTIONS_UPDATED", "AUCTION_OWNED_LIST_UPDATE", "AUCTION_ITEM_LIST_UPDATE"}, function()
    if type(F.resolve("C_AuctionHouse.GetOwnedAuctions")) == "function" then
        return { owned = F.call("C_AuctionHouse.GetOwnedAuctions"), bids = F.call("C_AuctionHouse.GetBids"),
            browse = F.call("C_AuctionHouse.GetBrowseResults"),
            owned_complete = F.call("C_AuctionHouse.HasFullOwnedAuctionResults"),
            browse_complete = F.call("C_AuctionHouse.HasFullBrowseResults") }
    end
    return { owned = indexed(count("GetNumAuctionItems", "owner"), function(i) return tuple("GetAuctionItemInfo", "owner", i) end),
        browse = indexed(count("GetNumAuctionItems", "list"), function(i) return tuple("GetAuctionItemInfo", "list", i) end), }
end, "auction")
F.add("guild", {"GUILD_ROSTER_UPDATE", "PLAYER_GUILD_UPDATE"}, function()
    return { player = tuple("GetGuildInfo", "player"), motd = F.call("GetGuildRosterMOTD"),
        info = F.call("GetGuildInfoText"), members = indexed(count("GetNumGuildMembers"), function(i)
            return tuple("GetGuildRosterInfo", i)
        end) }
end)
F.add("friends", {"FRIENDLIST_UPDATE", "BN_FRIEND_INFO_CHANGED", "IGNORELIST_UPDATE"}, function()
    return { friends = indexed(count("C_FriendList.GetNumFriends"), function(i) return F.call("C_FriendList.GetFriendInfoByIndex", i) end),
        ignored = indexed(count("C_FriendList.GetNumIgnores"), function(i) return F.call("C_FriendList.GetIgnoreName", i) end),
        battle_net = type(F.resolve("BNGetNumFriends")) == "function" and indexed(count("BNGetNumFriends"), function(i)
            return F.call("C_BattleNet.GetFriendAccountInfo", i)
        end) }
end)
F.add("group", {"GROUP_ROSTER_UPDATE", "PLAYER_ROLES_ASSIGNED"}, function()
    local raid = F.call("IsInRaid")
    local units = { "player" }
    local n = count("GetNumGroupMembers")
    for i = 1, (raid and n or math.max(0, n - 1)) do units[#units + 1] = (raid and "raid" or "party") .. i end
    local rows = {}
    for _, unit in ipairs(units) do
        rows[#rows + 1] = { unit = unit, guid = F.call("UnitGUID", unit), name = tuple("UnitFullName", unit),
            class = tuple("UnitClass", unit), level = F.call("UnitLevel", unit),
            role = F.call("UnitGroupRolesAssigned", unit), connected = F.call("UnitIsConnected", unit), }
        F.pause()
    end
    return rows
end)
F.add("auras", {"UNIT_AURA", "PLAYER_REGEN_ENABLED"}, function()
    F.require("C_UnitAuras.GetAuraDataByIndex", "player", 1, "HELPFUL")
    local rows = {}
    for _, filter in ipairs({"HELPFUL", "HARMFUL"}) do
        for i = 1, 255 do
            local aura = F.call("C_UnitAuras.GetAuraDataByIndex", "player", i, filter)
            if aura == nil then break end
            rows[#rows + 1] = { filter = filter, info = aura }
            if i % 20 == 0 then F.pause() end
        end
    end
    return rows
end)
F.add("diagnostics", {"PLAYER_ENTERING_WORLD", "ADDON_RESTRICTION_STATE_CHANGED"}, function()
    local capabilities = {}
    for name, record in pairs(F.character.datasets) do
        capabilities[name] = { status = record.status, apis = record.apis, missing = record.missing,
            failed = record.failed, secret_fields = record.secret_fields, observed_at = record.observed_at }
    end
    return { addon_version = F.VERSION, build = tuple("GetBuildInfo"), locale = F.call("GetLocale"),
        project = WOW_PROJECT_ID, network = tuple("GetNetStats"), framerate = F.call("GetFramerate"),
        combat_log_restricted = F.call("C_CombatLog.IsCombatLogRestricted"),
        logging_combat = F.call("LoggingCombat"), logging_chat = F.call("LoggingChat"),
        dropped_events = F.character.dropped_events, capabilities = capabilities, }
end)
