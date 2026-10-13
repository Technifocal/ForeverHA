local _, F = ...
local frame = CreateFrame("Frame")
F.frame = frame
local chatEvents = {
    "CHAT_MSG_SAY", "CHAT_MSG_YELL", "CHAT_MSG_WHISPER", "CHAT_MSG_WHISPER_INFORM", "CHAT_MSG_CHANNEL",
    "CHAT_MSG_GUILD", "CHAT_MSG_OFFICER", "CHAT_MSG_PARTY", "CHAT_MSG_PARTY_LEADER", "CHAT_MSG_RAID",
    "CHAT_MSG_RAID_LEADER", "CHAT_MSG_RAID_WARNING", "CHAT_MSG_EMOTE", "CHAT_MSG_TEXT_EMOTE",
    "CHAT_MSG_SYSTEM", "CHAT_MSG_LOOT", "CHAT_MSG_MONEY", "CHAT_MSG_COMBAT_XP_GAIN", "CHAT_MSG_COMBAT_FACTION_CHANGE",
    "CHAT_MSG_BN_WHISPER", "CHAT_MSG_BN_WHISPER_INFORM", "CHAT_MSG_INSTANCE_CHAT", "CHAT_MSG_INSTANCE_CHAT_LEADER",
    "CHAT_MSG_ADDON", "CHAT_MSG_ADDON_LOGGED", "PLAYER_DEAD", "PLAYER_ALIVE", "PLAYER_UNGHOST",
    "ENCOUNTER_START", "ENCOUNTER_END", "PLAYER_REGEN_DISABLED", "PLAYER_REGEN_ENABLED",
    "QUEST_ACCEPTED", "QUEST_TURNED_IN", "ACHIEVEMENT_EARNED", "ADDON_RESTRICTION_STATE_CHANGED",
}
local capture = {}
for _, event in ipairs(chatEvents) do capture[event] = true end
local routing = {}
for name, collector in pairs(F.collectors) do
    for _, event in ipairs(collector.events) do
        routing[event] = routing[event] or {}
        routing[event][#routing[event] + 1] = name
    end
end
local windows = {
    BANKFRAME_OPENED = {"bank", true}, BANKFRAME_CLOSED = {"bank", false},
    MAIL_SHOW = {"mail", true}, MAIL_CLOSED = {"mail", false},
    AUCTION_HOUSE_SHOW = {"auction", true}, AUCTION_HOUSE_CLOSED = {"auction", false},
    TRADE_SKILL_SHOW = {"profession", true}, TRADE_SKILL_CLOSE = {"profession", false},
}
local registered = {}
local function register(event)
    if registered[event] then return end
    local ok = pcall(frame.RegisterEvent, frame, event)
    registered[event] = ok
end
for event in pairs(routing) do register(event) end
for event in pairs(capture) do register(event) end
for event in pairs(windows) do register(event) end
for _, event in ipairs({"ADDON_LOADED", "PLAYER_LOGIN", "PLAYER_LEAVING_WORLD", "PLAYER_LOGOUT", "TIME_PLAYED_MSG"}) do register(event) end

frame:SetScript("OnEvent", function(_, event, ...)
    if event == "ADDON_LOADED" then
        local name = ...
        if name == "ForeverHA" then ForeverHADB = ForeverHADB or {} end
        return
    end
    if event == "PLAYER_LOGIN" then
        F.worldReady, F.needsInitialization = false, true
        return
    end
    if event == "PLAYER_LEAVING_WORLD" then
        F.worldReady, F.readyDelay = false, nil
        F.cancelWork()
        F.queue, F.pending, F.windows = {}, {}, {}
        return
    end
    if event == "PLAYER_ENTERING_WORLD" then
        F.cancelWork()
        F.queue, F.pending = {}, {}
        F.worldReady, F.readyDelay = false, 2
        return
    end
    if not F.character then return end
    if event == "TIME_PLAYED_MSG" then F.played = F.clean({...}); F.enqueue("character") end
    local window = windows[event]
    if window then F.windows[window[1]] = window[2] end
    if capture[event] then F.record(event, ...) end
    for _, name in ipairs(routing[event] or {}) do F.enqueue(name) end
    if event == "PLAYER_LOGOUT" then
        -- Teardown is not a safe time to query character/collection backends.
        -- Save the last completed observations; do not perform fresh native scans.
        F.cancelWork()
        F.character.saved_at = F.now()
        F.worldReady, F.readyDelay = false, nil
    end
end)
local elapsedTotal, refreshTotal, viewElapsed = 0, 0, 0
local viewState = {}
local viewCollectors = {
    MountJournal = "mounts", PetJournal = "pets", ToyBox = "collections",
    WardrobeCollectionFrame = "appearances", AchievementFrame = "achievements",
}
frame:SetScript("OnUpdate", function(_, elapsed)
    if F.readyDelay then
        F.readyDelay = F.readyDelay - elapsed
        if F.readyDelay <= 0 then
            F.readyDelay = nil
            if not F.character or F.needsInitialization then
                if not F.initialize() then return end
                F.needsInitialization = false
            end
            F.worldReady = true
            F.refresh()
            if F.pixel then F.pixel.setEnabled(F.db.settings.realtime == true) end
        end
    end
    if F.worldReady then
        viewElapsed = viewElapsed + elapsed
        if viewElapsed >= 0.5 then
            viewElapsed = 0
            for view, category in pairs(viewCollectors) do
                local shown = F.visible(view)
                if shown and not viewState[view] then F.enqueue(category) end
                viewState[view] = shown
            end
        end
    end
    elapsedTotal, refreshTotal = elapsedTotal + elapsed, refreshTotal + elapsed
    if elapsedTotal >= 0.05 then elapsedTotal = 0; F.tick() end
    if refreshTotal >= 60 then refreshTotal = 0; F.refresh() end
    if F.pixel then F.pixel.update(elapsed) end
end)

SLASH_FOREVERHA1 = "/foreverha"
SlashCmdList.FOREVERHA = function(message)
    if not F.character then F.print("Log in to initialize collection."); return end
    local command, category = string.match(message or "", "^(%S*)%s*(%S*)")
    if command == "refresh" then
        F.refresh(); F.print("Collection queued. WoW writes snapshots on logout or /reload.")
    elseif command == "enable" or command == "disable" then
        if category ~= "events" and not F.collectors[category] then F.print("Unknown category. Use /foreverha status."); return end
        F.db.settings.disabled[category] = command == "disable" or nil
        if category == "events" and command == "disable" then F.character.events = {} end
        F.enqueue(category); F.print(category .. " " .. command .. "d.")
    elseif command == "logging" then
        -- Changing logging is only done on the user's explicit slash command.
        if category == "on" or category == "off" then
            F.call("LoggingCombat", category == "on"); F.call("LoggingChat", category == "on")
        end
        F.print("Combat: " .. tostring(F.call("LoggingCombat")) .. "; chat: " .. tostring(F.call("LoggingChat")))
    elseif command == "realtime" then
        if category == "on" or category == "off" then F.pixel.setEnabled(category == "on") end
        if category == "test" then F.pixel.state(true) end
        F.print("Screen feed: " .. F.pixel.status())
    elseif command == "status" then
        for name in pairs(F.collectors) do
            local data = F.character.datasets[name] or {}
            F.print(name .. ": " .. (data.status or "queued"))
        end
        F.print("Buffered event losses: " .. F.character.dropped_events)
        F.print("Screen feed: " .. F.pixel.status())
    else F.print("Commands: refresh, status, enable <category>, disable <category>, logging [on|off], realtime [on|off|test]") end
end
