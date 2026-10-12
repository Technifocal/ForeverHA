local _, F = ...
F.collectors, F.queue, F.pending, F.windows = {}, {}, {}, {}
F.VERSION, F.eventLimit = "0.1.1", 5000
local unpack = unpack or table.unpack
local function pack(...) return { n = select("#", ...), ... } end

function F.resolve(path)
    local value = _G
    for part in string.gmatch(path, "[^.]+") do
        if type(value) ~= "table" then return nil end
        value = value[part]
    end
    return value
end

-- Never stringify, compare, branch on, or persist a secret value.
function F.clean(value, depth, seen)
    if issecretvalue and issecretvalue(value) then
        F.secretCount = (F.secretCount or 0) + 1
        return nil
    end
    local kind = type(value)
    if kind == "string" or kind == "boolean" then return value end
    if kind == "number" then
        if value == value and value ~= math.huge and value ~= -math.huge then return value end
        return nil
    end
    if kind ~= "table" then return nil end
    if canaccesstable and not canaccesstable(value) then
        F.secretCount = (F.secretCount or 0) + 1
        return nil
    end
    depth, seen = depth or 0, seen or {}
    if depth >= 20 or seen[value] then return nil end
    seen[value] = true
    local out = {}
    for k, v in pairs(value) do
        local key = F.clean(k, depth + 1, seen)
        if type(key) == "number" or type(key) == "string" then
            out[key] = F.clean(v, depth + 1, seen)
        end
    end
    seen[value] = nil
    return out
end

function F.visible(path)
    local frame = F.resolve(path)
    if not frame or type(frame.IsShown) ~= "function" then return false end
    local ok, shown = pcall(frame.IsShown, frame)
    return ok and F.clean(shown) == true
end

local collectionViews = {
    C_MountJournal = "MountJournal", C_PetJournal = "PetJournal",
    C_ToyBox = "ToyBox", C_TransmogCollection = "WardrobeCollectionFrame",
}

function F.skip(path, reason)
    if F.current then
        F.current.skipped = F.current.skipped or {}
        F.current.skipped[path] = reason
        F.current.partial = true
    end
end

function F.call(path, ...)
    local fn = F.resolve(path)
    F.calls = (F.calls or 0) + 1
    if F.current then
        F.current.apis[path] = type(fn) == "function"
        if type(fn) ~= "function" then F.current.missing[path] = true end
    end
    if type(fn) ~= "function" then return nil end
    -- pcall cannot catch a native assertion. Never pass an absent computed
    -- argument, or probe a collection backend just because its symbol exists.
    local args = pack(...)
    for i = 1, args.n do
        if args[i] == nil then F.skip(path, "nil_argument"); return nil end
    end
    local view = collectionViews[string.match(path, "^([^.]+)%.")]
    if view and not F.visible(view) then F.skip(path, "open_" .. view); return nil end
    local result = pack(pcall(fn, ...))
    if not result[1] then
        if F.current then F.current.failed[path] = true end
        return nil
    end
    for i = 2, result.n do result[i] = F.clean(result[i]) end
    return unpack(result, 2, result.n)
end

function F.require(path, ...)
    if type(F.resolve(path)) ~= "function" then error("unsupported:" .. path, 0) end
    return F.call(path, ...)
end

function F.defer(callback)
    F.workTail = F.workTail + 1
    F.work[F.workTail] = callback
end

function F.afterWork(callback)
    F.finalizers[#F.finalizers + 1] = callback
end

-- Compatibility with small existing loops; indexed scans use F.defer instead.
function F.pause() end

function F.cancelWork()
    F.running, F.current = nil, nil
    F.work, F.finalizers, F.workHead, F.workTail = {}, {}, 1, 0
    F.result = nil
end

function F.add(name, events, collect, window)
    F.collectors[name] = { events = events, collect = collect, window = window }
end

function F.now() return F.call("GetServerTime") or time() end

function F.initialize()
    if ForeverHADB and ForeverHADB.schema and ForeverHADB.schema ~= 1 then
        F.print("Unsupported saved-data schema; preserved existing data. Update ForeverHA.")
        return false
    end
    ForeverHADB = ForeverHADB or {}
    F.db = ForeverHADB
    F.db.schema, F.db.version = 1, F.VERSION
    F.db.settings = F.db.settings or { disabled = {} }
    F.db.settings.disabled = F.db.settings.disabled or {}
    F.db.characters = F.db.characters or {}
    local guid = F.call("UnitGUID", "player")
    if not guid then return false end
    local name, realm = F.call("UnitFullName", "player")
    realm = realm or F.call("GetRealmName")
    F.guid = guid
    F.character = F.db.characters[guid] or { datasets = {}, events = {}, dropped_events = 0 }
    F.db.characters[guid] = F.character
    F.character.name, F.character.realm = name, realm
    F.character.guid = guid
    F.character.session = tostring(F.now()) .. ":" .. tostring(F.call("GetTime") or 0)
    F.character.build = { F.call("GetBuildInfo") }
    F.db.last_character = guid
    return true
end

function F.enqueue(name)
    if not F.character or not F.worldReady or F.pending[name] then return end
    local collector = F.collectors[name]
    if not collector then return end
    if F.db.settings.disabled[name] then
        F.character.datasets[name] = { status = "disabled", checked_at = F.now() }
        return
    end
    if collector.window and not F.windows[collector.window] then
        if not F.character.datasets[name] then
            F.character.datasets[name] = { status = "unobserved", reason = "open_" .. collector.window }
        end
        return
    end
    F.pending[name] = true
    F.queue[#F.queue + 1] = name
end

function F.finish(name, payload, ok, reason)
    local old = F.character.datasets[name] or {}
    local current = F.current
    local status = "available"
    if not ok then
        reason = type(reason) == "string" and reason or "collector_failed"
        status = string.find(reason, "unsupported:", 1, true) and "unsupported" or
            (string.find(reason, "unobserved:", 1, true) and "unobserved" or "error")
    elseif F.secretCount > 0 then status = "restricted"
    elseif current.partial or next(current.missing) or next(current.failed) then status = "partial" end
    local record = {
        status = status, checked_at = F.now(), apis = current.apis,
        missing = current.missing, failed = current.failed, secret_fields = F.secretCount,
        reason = reason, scope = current.scope, skipped = current.skipped,
        payload = old.payload, observed_at = old.observed_at,
    }
    if ok then
        record.payload, record.observed_at = F.clean(payload), F.current.started
    end
    F.character.datasets[name] = record
    F.cancelWork()
end

function F.tick()
    if not F.character or not F.worldReady then return end
    if not F.running and #F.queue > 0 then
        local name = table.remove(F.queue, 1)
        F.pending[name] = nil
        local collector = F.collectors[name]
        if F.db.settings.disabled[name] then F.enqueue(name); return end
        if collector.window and not F.windows[collector.window] then return end
        F.current = { name = name, apis = {}, missing = {}, failed = {}, started = F.now() }
        F.secretCount = 0
        F.work, F.finalizers, F.workHead, F.workTail = {}, {}, 1, 0
        F.running = true
        local ok, payload = pcall(collector.collect)
        if not ok then F.finish(name, nil, false, F.clean(payload)); return end
        F.result = payload
    end
    if not F.running then return end
    local collector = F.collectors[F.current.name]
    if F.db.settings.disabled[F.current.name] or (collector.window and not F.windows[collector.window]) then
        local name = F.current.name
        F.cancelWork()
        F.enqueue(name)
        return
    end
    -- All native API calls run on the ordinary frame callback, never on a
    -- secondary Lua thread. Limit the number of work items per frame tick.
    local steps = 0
    while F.workHead <= F.workTail and steps < 20 do
        local callback = F.work[F.workHead]
        F.work[F.workHead] = nil
        F.workHead, steps = F.workHead + 1, steps + 1
        local ok, reason = pcall(callback)
        if not ok then F.finish(F.current.name, nil, false, F.clean(reason)); return end
    end
    if F.workHead > F.workTail then
        for _, callback in ipairs(F.finalizers) do
            local ok, reason = pcall(callback)
            if not ok then F.finish(F.current.name, nil, false, F.clean(reason)); return end
        end
        F.finish(F.current.name, F.result, true)
    end
end

function F.refresh()
    for name in pairs(F.collectors) do F.enqueue(name) end
end

function F.record(event, ...)
    if not F.character or F.db.settings.disabled.events then return end
    local before = F.secretCount or 0
    local args = F.clean(pack(...))
    local secrets = (F.secretCount or 0) - before
    local list = F.character.events
    if #list >= F.eventLimit then
        table.remove(list, 1)
        F.character.dropped_events = F.character.dropped_events + 1
    end
    F.character.event_sequence = (F.character.event_sequence or 0) + 1
    list[#list + 1] = { sequence = F.character.event_sequence, event = event,
        observed_at = F.now(), session = F.character.session, args = args,
        secret_fields = secrets, status = secrets > 0 and "restricted" or "available" }
end

function F.print(message)
    if DEFAULT_CHAT_FRAME then DEFAULT_CHAT_FRAME:AddMessage("ForeverHA: " .. message) end
end
