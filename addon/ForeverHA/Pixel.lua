-- Strip geometry/protocol adapted from DOINK (Ryan Flanagan, MIT).
-- Copyright notice and full permission terms: licenses/DOINK.txt.
local _, F = ...
local P = { enabled = false, dropped = 0, queue = {}, sequence = 0 }
F.pixel = P
local SYNC, ROWS, BLOCKS, SIZE = "1010101010110011", 3, 400, 3
local STEP, REPEATS, MAX_BYTES, MAX_QUEUE = 0.25, 2, 2048, 16
local frame, textures, shown, elapsed, stateElapsed, messageID
local function xor(a, b)
    if bit then return bit.bxor(a, b) end
    local out, factor = 0, 1
    while a > 0 or b > 0 do
        if a % 2 ~= b % 2 then out = out + factor end
        a, b, factor = math.floor(a / 2), math.floor(b / 2), factor * 2
    end
    return out
end
function P.crc(bytes, width, polynomial, initial)
    local result, high, limit = initial, 2 ^ (width - 1), 2 ^ width
    for i = 1, #bytes do
        result = xor(result, bytes:byte(i) * 2 ^ (width - 8))
        for _ = 1, 8 do
            local top = result >= high
            result = (result * 2) % limit
            if top then result = xor(result, polynomial) end
        end
    end
    return result
end
local function bits(row, byte)
    for shift = 7, 0, -1 do row[#row + 1] = math.floor(byte / 2 ^ shift) % 2 end
end
function P.encode(id, message, width)
    width = width or BLOCKS
    local capacity = math.floor(width / 4)
    local count = math.max(1, math.ceil(#message / capacity))
    local chunks = {}
    for index = 0, count - 1 do
        local payload = message:sub(index * capacity + 1, (index + 1) * capacity)
        local crc = P.crc(payload, 16, 0x1021, 0xFFFF)
        local header = string.char(0x10, math.floor(width / 256), width % 256,
            math.floor(id / 256), id % 256, index, count, #payload,
            math.floor(crc / 256), crc % 256)
        header = header .. string.char(P.crc(header, 8, 7, 0))
        local rows, data = { {}, {}, {} }, {}
        for i = 1, #SYNC do rows[1][i] = tonumber(SYNC:sub(i, i)) end
        for i = 1, #header do bits(rows[1], header:byte(i)) end
        for i = 1, #payload do bits(data, payload:byte(i)) end
        for c = 1, width do
            rows[1][c] = rows[1][c] or 0
            rows[2][c], rows[3][c] = data[c] or 0, data[width + c] or 0
        end
        chunks[#chunks + 1] = rows
    end
    return chunks
end
function P.json(value)
    local out, size = {}, 0
    local function emit(text)
        size = size + #text
        if size > MAX_BYTES then error("pixel packet too large", 0) end
        out[#out + 1] = text
    end
    local function encode(v, depth)
        if depth > 12 then error("pixel nesting too deep", 0) end
        local kind = type(v)
        if v == nil then emit("null")
        elseif kind == "boolean" then emit(v and "true" or "false")
        elseif kind == "number" then emit(tostring(v))
        elseif kind == "string" then
            emit('"' .. v:gsub('[%z\1-\31\\"]', function(c)
                return string.format('\\u%04x', string.byte(c))
            end) .. '"')
        elseif kind == "table" then
            if v.n then
                emit("[")
                for i = 1, v.n do if i > 1 then emit(",") end; encode(v[i], depth + 1) end
                emit("]")
            else
                emit("{")
                local keys = {}
                for key in pairs(v) do keys[#keys + 1] = key end
                table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
                for i, key in ipairs(keys) do
                    if i > 1 then emit(",") end
                    encode(tostring(key), depth + 1); emit(":"); encode(v[key], depth + 1)
                end
                emit("}")
            end
        else error("unsupported pixel value", 0) end
    end
    encode(value, 0)
    return table.concat(out)
end
local function build()
    if frame then return true end
    if not GetPhysicalScreenSize or not UIParent then return false end
    local width, height = GetPhysicalScreenSize()
    if not width or not height or width < 400 or height < 100 then return false end
    BLOCKS = math.max(104, math.min(400, math.floor((width - 24) / SIZE)))
    frame = CreateFrame("Frame", nil, UIParent)
    frame:SetFrameStrata("TOOLTIP"); frame:SetFrameLevel(10000); frame:EnableMouse(false)
    if frame.SetIgnoreParentScale then
        frame:SetIgnoreParentScale(true); frame:SetScale(768 / height)
    else frame:SetScale(768 / height / UIParent:GetEffectiveScale()) end
    frame:SetSize(BLOCKS * SIZE, ROWS * SIZE)
    frame:SetPoint("BOTTOMLEFT", UIParent, "BOTTOMLEFT", 12, 0)
    textures = {}
    for r = 1, ROWS do
        textures[r] = {}
        for c = 1, BLOCKS do
            local t = frame:CreateTexture(nil, "ARTWORK")
            t:SetSize(SIZE, SIZE); t:SetPoint("TOPLEFT", frame, "TOPLEFT", (c - 1) * SIZE, -(r - 1) * SIZE)
            t:SetColorTexture(0, 0, 0, 1); textures[r][c] = t
        end
    end
    messageID = math.random(0, 65535)
    frame:Hide()
    return true
end
local function paint(rows)
    for r = 1, ROWS do
        for c = 1, BLOCKS do
            local v = rows[r][c]
            if not shown or shown[r][c] ~= v then textures[r][c]:SetColorTexture(v, v, v, 1) end
        end
    end
    shown = rows; frame:Show()
end
local function advance()
    local item = P.queue[1]
    if not item then if frame then frame:Hide() end; shown = nil; return end
    paint(item.chunks[item.index])
    item.index = item.index + 1
    if item.index > #item.chunks then
        item.index, item.left = 1, item.left - 1
        if item.left == 0 then table.remove(P.queue, 1) end
    end
end
local function queue(packet, state, urgent)
    if not P.enabled then return end
    local ok, bytes = pcall(P.json, packet)
    if not ok or not build() then P.dropped = P.dropped + 1; return end
    if state then
        for i = #P.queue, 2, -1 do if P.queue[i].state then table.remove(P.queue, i) end end
    end
    if #P.queue >= MAX_QUEUE then
        -- Preserve queued events where possible; state packets can be replaced.
        if state then P.dropped = P.dropped + 1; return end
        table.remove(P.queue, #P.queue); P.dropped = P.dropped + 1
    end
    messageID = (messageID + 1) % 65536
    local item = { chunks = P.encode(messageID, bytes), index = 1, left = REPEATS, state = state }
    if urgent then
        -- Preempt promptly. An interrupted state will be refreshed; interrupted
        -- event chunks restart at index one so the reader can still assemble them.
        if P.queue[1] then P.queue[1].index = 1 end
        table.insert(P.queue, 1, item); elapsed = 0; advance()
    else
        local empty = #P.queue == 0
        P.queue[#P.queue + 1] = item
        if empty then elapsed = 0; advance() end
    end
end
function P.event(record)
    if not P.enabled or not F.worldReady or record.event == "CHAT_MSG_ADDON" or record.event == "CHAT_MSG_ADDON_LOGGED" then return end
    queue({ n = 10, "FHA2", F.guid, record.session, record.sequence, record.observed_at,
            "event", record.event, record.args, record.secret_fields, record.status }, false,
            record.event == "PLAYER_DEAD")
end
function P.state(force)
    if not P.enabled or not F.worldReady then return end
    local s, before = { n = 16 }, F.secretCount or 0
    if not F.db.settings.disabled.stats then
        s[1], s[2] = F.call("UnitHealth", "player"), F.call("UnitHealthMax", "player")
        s[3], s[4] = F.call("UnitPower", "player"), F.call("UnitPowerMax", "player")
        s[5], s[6], s[7] = F.call("UnitIsDeadOrGhost", "player"), F.call("UnitAffectingCombat", "player"), F.call("IsMounted")
    end
    if not F.db.settings.disabled.character then
        s[8], s[9] = F.call("UnitLevel", "player"), F.call("GetMoney")
        s[10], s[11], s[12] = F.call("UnitXP", "player"), F.call("UnitXPMax", "player"), F.call("GetXPExhaustion")
    end
    if not F.db.settings.disabled.location then s[13], s[14] = F.call("GetZoneText"), F.call("GetSubZoneText") end
    s[15], s[16] = P.dropped, (F.secretCount or 0) - before
    local ok, signature = pcall(P.json, s)
    local now = F.now()
    if not ok then P.dropped = P.dropped + 1; return end
    if not force and signature == P.lastState and P.lastStateAt and now - P.lastStateAt < 5 then return end
    P.lastState, P.lastStateAt = signature, now
    P.sequence = P.sequence + 1
    queue({ n = 7, "FHA2", F.guid, F.character.session, P.sequence, now, "state", s }, true, false)
end
function P.setEnabled(enabled)
    F.db.settings.realtime = enabled
    P.enabled = enabled
    if enabled then P.state(true)
    else P.queue = {}; shown = nil; if frame then frame:Hide() end end
end
function P.update(delta)
    if not P.enabled then return end
    if not F.worldReady then if frame then frame:Hide() end; P.queue = {}; shown = nil; return end
    elapsed, stateElapsed = (elapsed or 0) + delta, (stateElapsed or 0) + delta
    if stateElapsed >= 1 then stateElapsed = 0; P.state() end
    if elapsed >= STEP then elapsed = 0; advance() end
end
function P.status()
    return (P.enabled and "on" or "off") .. "; queued " .. #P.queue .. "; dropped " .. P.dropped
end
