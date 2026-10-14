-- Shadow Man Remastered - Location Namer (Cheat Engine)
-- Usage: CE -> attach to thoth_x64_patched.exe -> Table -> Show Cheat Table Lua
--        Script -> paste this file -> Execute.
--
-- Reads data/locations.csv directly (no hardcoded tables), auto-detects the
-- current level from live memory, and lets you name the nearest location with
-- one key. Names are built as:  {room} {tag} {object} {n}
--   room   - sticky text you set once per area ("Leftside Vents Cable Govi")
--   tag    - optional sticky word for the required item ("Gad Marcher")
--   object - derived from the RSC object (Pot / Barrel / Cadeaux / Govi ...)
--   n      - next free number for that exact base name in this level
-- The tag is purely cosmetic and resets to (none) whenever the room changes,
-- since most locations don't need an item in their name. The requirement
-- (sub_region column) is left untouched unless "Change requirement" is ticked.
--
-- Every accepted name is appended to data/naming_log.csv. Nothing touches
-- locations.csv directly; run  python tools/apply_naming_log.py  to merge.
--
-- Hotkeys (work while the game window is focused):
--   F5  name the selected/nearest unnamed location
--   F6  cycle the tag
--   F7  undo the last name
--   F8  toggle "only unnamed"
--   F9  save softlock / verified / notes only (keeps the current name)
--
-- Softlock / Verified / Notes load from the target location. Touching any of
-- them pins that location (it won't switch as you move) until you press F5
-- or F9, so an edit can't accidentally land on a different barrel.

local REPO_DIR   = [[C:\Users\jonat\Documents\shadow-man-remastered-randomizer]]
local CSV_PATH   = REPO_DIR .. [[\data\locations.csv]]
local LOG_PATH   = REPO_DIR .. [[\data\naming_log.csv]]

local BASE              = "thoth_x64_patched.exe"
local OFFSET_X          = 0xC92EB0
local OFFSET_Y          = 0xC92EB4
local OFFSET_Z          = 0xC92EB8
local CURRENT_LEVEL_RVA = 0xF5EE9C   -- int32, same address client.py uses
local UPDATE_MS         = 500
local MAX_LIST          = 25
local NAME_TEMPLATE     = "{room} {tag} {object}"   -- reorder freely

local LEVEL_NUMBER_TO_ID = {
  [0]="swampday", [1]="tenement", [2]="prison", [3]="uground", [4]="florida",
  [5]="salvage", [6]="deadside", [7]="wastland", [8]="asylum", [9]="as2exper",
  [10]="as3schis", [11]="as4dkeng", [12]="t1tchgad", [13]="ah1cagew",
  [14]="ah2playr", [15]="t2wlkgad", [16]="ah3lavad", [17]="t3swmgad",
  [18]="ah4fogom",
  -- night phases share the day level's location data
  [19]="swampday", [20]="tenement", [21]="prison", [22]="uground",
  [23]="florida", [24]="salvage",
}

-- Tag vocabulary (F6 cycles). Edit to taste; "" = no tag.
local TAGS = { "", "Eng Key", "Gad Toucher", "Gad Marcher", "Gad Nager",
  "Poigne", "Baton", "Calabash", "Flambeau", "Marteau", "Night", "Key Card" }

-- Requirement tokens always offered in the dropdown (plus every value
-- already used in the current level).
local COMMON_REQS = { "N", "ENG_KEY", "GAD1_HAND", "GAD2_WALK", "GAD3_SWIM",
  "POIGNE", "BATON", "CALABASH", "FLAMBEAU", "MARTEAU", "NIGHT",
  "PRISON_KEY_CARD", "ENG_KEY & GAD2_WALK", "ENG_KEY | GAD2_WALK" }

local OBJECT_WORDS = {
  RSC_X_BARREL_D="Pot", RSC_X_BARREL_L="Barrel", RSC_X_BARREL_A="Barrel",
  RSC_X_BARREL="Barrel", RSC_EXPLOSIVE_BARREL="Explosive Barrel",
  RSC_FL_CRATE="Crate", RSC_UN_CRATES="Crate", RSC_UN_POTS="Pot",
  RSC_TE_PACKBOX1="Box", RSC_TE_PACKBOX2="Box",
  RSC_CADEAUX="Cadeaux", RSC_X_CADEAUX="Cadeaux", RSC_PICKUP_CADEAUX="Cadeaux",
  RSC_X_GOVI="Govi", RSC_X_DARK_SOUL="Dark Soul",
  RSC_X_WEAPON_ALTAR="Altar",
}

------------------------------------------------------------------------
-- CSV
------------------------------------------------------------------------
local function parseCSV(text)
  if text:sub(1,3) == "\239\187\191" then text = text:sub(4) end
  local rows, row, field, inq = {}, {}, {}, false
  local i, n = 1, #text
  while i <= n do
    local c = text:byte(i)
    if inq then
      if c == 34 then
        if text:byte(i+1) == 34 then field[#field+1] = '"'; i = i + 1
        else inq = false end
      else field[#field+1] = string.char(c) end
    elseif c == 34 then inq = true
    elseif c == 44 then row[#row+1] = table.concat(field); field = {}
    elseif c == 10 then
      row[#row+1] = table.concat(field); field = {}
      rows[#rows+1] = row; row = {}
    elseif c ~= 13 then field[#field+1] = string.char(c) end
    i = i + 1
  end
  if #field > 0 or #row > 0 then row[#row+1] = table.concat(field); rows[#rows+1] = row end
  return rows
end

local function readFile(path)
  local f = io.open(path, "rb"); if not f then return nil end
  local s = f:read("*a"); f:close(); return s
end

local function csvQuote(s)
  s = tostring(s or "")
  if s:find('[,"\r\n]') then s = '"' .. s:gsub('"', '""') .. '"' end
  return s
end

-- Columns snapshotted into naming_log.csv for every saved location.
local EDIT_FIELDS = { "friendly_name", "sub_region", "can_softlock", "is_verified", "notes" }
local NOTE_PRESETS = { "? INVIS ?", "? OUT OF BOUNDS ?", "? UNDERGROUND ?",
  "secret area", "NIGHT required only for GOVI" }

local function trim(s) return (tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", "")) end
local function keyOf(level, src, off) return level .. "|" .. src .. "|" .. off end

------------------------------------------------------------------------
-- Data
------------------------------------------------------------------------
local byLevel = {}       -- level_id -> { row, ... }
local byKey   = {}       -- key -> row
local nameCount = {}     -- level -> { name -> count }
local undoStack = {}
local sessionNamed = 0

local function rebuildCounts(level)
  local c = {}
  for _, r in ipairs(byLevel[level] or {}) do
    c[r.friendly_name] = (c[r.friendly_name] or 0) + 1
  end
  nameCount[level] = c
end

local function isGeneric(r)
  local n = r.friendly_name
  if n == "" or n:find("%.rsc %- 0x") then return true end
  return ((nameCount[r.level_id] or {})[n] or 0) > 1
end

local function loadData()
  byLevel, byKey, nameCount = {}, {}, {}
  local text = readFile(CSV_PATH)
  if not text then return false, "Cannot read " .. CSV_PATH end
  local rows = parseCSV(text)
  local hdr = {}
  for i, h in ipairs(rows[1]) do hdr[h] = i end
  for i = 2, #rows do
    local f = rows[i]
    if #f >= 14 then
      local r = {
        level_id = f[hdr.level_id], source_file = f[hdr.source_file],
        offset = f[hdr.offset], category = f[hdr.category],
        object = f[hdr.object], friendly_name = f[hdr.friendly_name] or "",
        sub_region = f[hdr.sub_region] or "",
        can_softlock = f[hdr.can_softlock] or "",
        is_verified = f[hdr.is_verified] or "",
        notes = f[hdr.notes] or "",
        x = tonumber(f[hdr.x]) or 0, y = tonumber(f[hdr.y]) or 0,
        z = tonumber(f[hdr.z]) or 0,
      }
      r.key = keyOf(r.level_id, r.source_file, r.offset)
      byLevel[r.level_id] = byLevel[r.level_id] or {}
      table.insert(byLevel[r.level_id], r)
      byKey[r.key] = r
    end
  end
  -- Upgrade a log written by an older version of this script (fewer
  -- columns): fill the missing columns from locations.csv's current values,
  -- which is what those entries implicitly left them as.
  local oldText = readFile(LOG_PATH)
  if oldText then
    local lrows = parseCSV(oldText)
    local lh = {}
    for i, h in ipairs(lrows[1] or {}) do lh[h] = i end
    local missing = false
    for _, k in ipairs(EDIT_FIELDS) do if not lh[k] then missing = true end end
    if missing and lh.level_id and lh.source_file and lh.offset then
      local out = { "level_id,source_file,offset," .. table.concat(EDIT_FIELDS, ",") .. ",timestamp" }
      for i = 2, #lrows do
        local f = lrows[i]
        if #f >= 3 then
          local lv, src, off = f[lh.level_id], f[lh.source_file], f[lh.offset]
          local base = byKey[keyOf(lv, src, off)] or {}
          local line = { csvQuote(lv), csvQuote(src), csvQuote(off) }
          for _, k in ipairs(EDIT_FIELDS) do
            line[#line+1] = csvQuote(lh[k] and f[lh[k]] or base[k] or "")
          end
          line[#line+1] = csvQuote(lh.timestamp and f[lh.timestamp] or "")
          out[#out+1] = table.concat(line, ",")
        end
      end
      local bak = io.open(LOG_PATH .. ".bak", "wb"); bak:write(oldText); bak:close()
      local w = io.open(LOG_PATH, "wb"); w:write(table.concat(out, "\r\n") .. "\r\n"); w:close()
    end
  end

  -- replay names logged in earlier sessions but not merged yet
  local logText = readFile(LOG_PATH)
  local replayed = 0
  if logText then
    local lrows = parseCSV(logText)
    local lh = {}
    for i, h in ipairs(lrows[1] or {}) do lh[h] = i end
    for i = 2, #lrows do
      local f = lrows[i]
      local r = byKey[keyOf(f[lh.level_id] or "", f[lh.source_file] or "", f[lh.offset] or "")]
      if r then
        for _, k in ipairs(EDIT_FIELDS) do
          if lh[k] and f[lh[k]] ~= nil then r[k] = f[lh[k]] end
        end
        replayed = replayed + 1
      end
    end
  end
  for lv in pairs(byLevel) do rebuildCounts(lv) end
  return true, string.format("Loaded %d rows (%d pending from naming_log)", #rows - 1, replayed)
end

------------------------------------------------------------------------
-- Name building
------------------------------------------------------------------------
local function objectWord(r)
  local w = OBJECT_WORDS[r.object]
  if w then return w end
  local s = r.object:gsub("^RSC_X_", ""):gsub("^RSC_", ""):gsub("_", " "):lower()
  return (s:gsub("(%a)([%w]*)", function(a, b) return a:upper() .. b end))
end

local function nextNumber(r, base)
  local best = 0
  for _, o in ipairs(byLevel[r.level_id] or {}) do
    if o ~= r then
      local n = o.friendly_name
      if n == base then best = math.max(best, 1)
      elseif n:sub(1, #base + 1) == base .. " " then
        local num = tonumber(n:sub(#base + 2))
        if num then best = math.max(best, num) end
      end
    end
  end
  return best + 1
end

------------------------------------------------------------------------
-- UI
------------------------------------------------------------------------
local form = createForm(false)
form.Caption = "Shadow Man - Location Namer"
form.Width, form.Height = 760, 690

local function lbl(text, l, t, w)
  local x = createLabel(form); x.Left, x.Top = l, t; x.Caption = text
  if w then x.Width = w end; return x
end

lbl("Level:", 8, 11)
local levelEdit = createEdit(form); levelEdit.Left, levelEdit.Top, levelEdit.Width = 50, 8, 90
local autoChk = createCheckBox(form); autoChk.Left, autoChk.Top = 148, 9
autoChk.Caption = "Auto-detect"; autoChk.Checked = true
lbl("Range:", 250, 11)
local threshEdit = createEdit(form); threshEdit.Left, threshEdit.Top, threshEdit.Width = 295, 8, 60
threshEdit.Text = "2000"
local onlyChk = createCheckBox(form); onlyChk.Left, onlyChk.Top = 365, 9
onlyChk.Caption = "Only unnamed (F8)"; onlyChk.Checked = true
local reloadBtn = createButton(form); reloadBtn.Left, reloadBtn.Top, reloadBtn.Width = 660, 6, 90
reloadBtn.Caption = "Reload CSV"

lbl("Room:", 8, 43)
local roomBox = createComboBox(form); roomBox.Left, roomBox.Top, roomBox.Width = 50, 40, 300
lbl("Tag:", 360, 43)
local tagBox = createComboBox(form); tagBox.Left, tagBox.Top, tagBox.Width = 390, 40, 130
for _, t in ipairs(TAGS) do tagBox.Items.Add(t == "" and "(none)" or t) end
tagBox.ItemIndex = 0
lbl("Object:", 530, 43)
local objEdit = createEdit(form); objEdit.Left, objEdit.Top, objEdit.Width = 575, 40, 175

local reqChk = createCheckBox(form); reqChk.Left, reqChk.Top = 8, 73
reqChk.Caption = "Change requirement:"; reqChk.Checked = false
local reqBox = createComboBox(form); reqBox.Left, reqBox.Top, reqBox.Width = 150, 72, 370
reqBox.Text = "N"; reqBox.Enabled = false
local numChk = createCheckBox(form); numChk.Left, numChk.Top = 530, 73
numChk.Caption = "Number"; numChk.Checked = true

local softChk = createCheckBox(form); softChk.Left, softChk.Top = 8, 103
softChk.Caption = "Can softlock"
local verChk = createCheckBox(form); verChk.Left, verChk.Top = 110, 103
verChk.Caption = "Verified"
lbl("Notes:", 190, 106)
local notesBox = createComboBox(form); notesBox.Left, notesBox.Top, notesBox.Width = 230, 102, 400
for _, n in ipairs(NOTE_PRESETS) do notesBox.Items.Add(n) end
local saveBtn = createButton(form); saveBtn.Left, saveBtn.Top, saveBtn.Width = 640, 100, 110
saveBtn.Caption = "Save flags (F9)"

local preview = lbl("", 8, 134, 740)
preview.Font.Size = 12; preview.Font.Style = "[fsBold]"
local previewSub = lbl("", 8, 158, 740)
local coordLbl = lbl("", 8, 178, 740)
coordLbl.Font.Name = "Courier New"; coordLbl.Font.Size = 9

local nameBtn = createButton(form); nameBtn.Left, nameBtn.Top, nameBtn.Width = 8, 222, 140
nameBtn.Caption = "Name it (F5)"
local undoBtn = createButton(form); undoBtn.Left, undoBtn.Top, undoBtn.Width = 156, 222, 100
undoBtn.Caption = "Undo (F7)"
local copyBtn = createButton(form); copyBtn.Left, copyBtn.Top, copyBtn.Width = 264, 222, 100
copyBtn.Caption = "Copy name"

local list = createListBox(form)
list.Left, list.Top, list.Width, list.Height = 8, 254, 742, 322
list.Font.Name = "Courier New"; list.Font.Size = 9

local status = lbl("", 8, 584, 742)

-- Progress: this level and all levels, with bars.
local levelProg = lbl("", 8, 608, 365)
levelProg.Font.Size = 10; levelProg.Font.Style = "[fsBold]"
local allProg = lbl("", 385, 608, 365)
allProg.Font.Size = 10; allProg.Font.Style = "[fsBold]"
local levelBar = createProgressBar(form)
levelBar.Left, levelBar.Top, levelBar.Width, levelBar.Height = 8, 630, 365, 18
levelBar.Min, levelBar.Max = 0, 1000
local allBar = createProgressBar(form)
allBar.Left, allBar.Top, allBar.Width, allBar.Height = 385, 630, 365, 18
allBar.Min, allBar.Max = 0, 1000

------------------------------------------------------------------------
-- State
------------------------------------------------------------------------
local currentLevel, listed, selectedKey, lastSig = nil, {}, nil, ""
local player = { x = 0, y = 0, z = 0 }

local function currentTag()
  local t = trim(tagBox.Text); return t == "(none)" and "" or t
end

local function buildName(r)
  local obj = trim(objEdit.Text); if obj == "" then obj = objectWord(r) end
  local base = NAME_TEMPLATE:gsub("{room}", trim(roomBox.Text))
    :gsub("{tag}", currentTag()):gsub("{object}", obj)
  base = trim(base:gsub("%s+", " "))
  if numChk.Checked then base = base .. " " .. nextNumber(r, base) end
  return base
end

-- Flag editor state: which location the checkboxes/notes were loaded from,
-- and whether the user has changed them since.
local flagKey, flagDirty, flagLoading = nil, false, false
local flagOrig = {}

local function loadFlags(r)
  flagLoading = true
  flagKey, flagDirty = r and r.key or nil, false
  flagOrig = r and { can_softlock = r.can_softlock, is_verified = r.is_verified } or {}
  softChk.Checked = r ~= nil and r.can_softlock == "TRUE"
  -- blank is_verified means "not audited yet" and counts as verified
  verChk.Checked = r == nil or r.is_verified ~= "FALSE"
  notesBox.Text = r and r.notes or ""
  flagLoading = false
end

local function flagValues(r)
  if r.key ~= flagKey then return r.can_softlock, r.is_verified, r.notes end
  local soft = softChk.Checked and "TRUE" or "NULL"
  if (flagOrig.can_softlock == "TRUE") == softChk.Checked then soft = flagOrig.can_softlock end
  local ver = verChk.Checked and "TRUE" or "FALSE"
  if (flagOrig.is_verified ~= "FALSE") == verChk.Checked then ver = flagOrig.is_verified end
  return soft, ver, trim(notesBox.Text)
end

local function target()
  if flagDirty and flagKey and byKey[flagKey] then return byKey[flagKey] end
  if selectedKey then
    for _, e in ipairs(listed) do if e.r.key == selectedKey then return e.r end end
  end
  for _, e in ipairs(listed) do if isGeneric(e.r) then return e.r end end
  return nil
end

local function fillReqChoices(level)
  local seen, items = {}, {}
  local function add(v) if v ~= "" and not seen[v] then seen[v] = true; items[#items+1] = v end end
  for _, v in ipairs(COMMON_REQS) do add(v) end
  for _, r in ipairs(byLevel[level] or {}) do add(r.sub_region) end
  local keep = reqBox.Text
  reqBox.Items.Clear()
  for _, v in ipairs(items) do reqBox.Items.Add(v) end
  reqBox.Text = keep
end

local function remainingInLevel()
  local n, total = 0, 0
  for _, r in ipairs(byLevel[currentLevel] or {}) do
    total = total + 1; if isGeneric(r) then n = n + 1 end
  end
  return n, total
end

local function commas(n)
  local s = tostring(n)
  repeat local k; s, k = s:gsub("^(%d+)(%d%d%d)", "%1,%2") until k == 0
  return s
end

local function pct(done, total) return total > 0 and done * 100 / total or 0 end

local lastLevelDone, lastLevelName = nil, nil

local function updateProgress()
  local left, total = remainingInLevel()
  local done = total - left
  local allDone, allTotal = 0, 0
  for _, rows in pairs(byLevel) do
    for _, r in ipairs(rows) do
      allTotal = allTotal + 1
      if not isGeneric(r) then allDone = allDone + 1 end
    end
  end
  local lp, ap = pct(done, total), pct(allDone, allTotal)
  levelProg.Caption = string.format("%s: %s / %s named (%.1f%%)",
    currentLevel or "?", commas(done), commas(total), lp)
  allProg.Caption = string.format("All levels: %s / %s named (%.1f%%)",
    commas(allDone), commas(allTotal), ap)
  levelBar.Position = math.floor(lp * 10)
  allBar.Position = math.floor(ap * 10)

  -- A little celebration when a level hits 100% during this session.
  local finished = total > 0 and left == 0
  local celebrate = finished and lastLevelName == currentLevel
    and lastLevelDone ~= nil and lastLevelDone < total
  lastLevelDone, lastLevelName = done, currentLevel
  return celebrate
end

local function setStatus(msg)
  local celebrate = updateProgress()
  if celebrate then msg = "*** " .. tostring(currentLevel) .. " is fully named! ***   " .. (msg or "") end
  status.Caption = string.format("%s   |   named this session: %d", msg or "", sessionNamed)
end

------------------------------------------------------------------------
-- Actions
------------------------------------------------------------------------
local function appendLog(r)
  local exists = io.open(LOG_PATH, "rb"); if exists then exists:close() end
  local f = io.open(LOG_PATH, "ab"); if not f then return false end
  if not exists then
    f:write("level_id,source_file,offset," .. table.concat(EDIT_FIELDS, ",") .. ",timestamp\r\n")
  end
  local out = { csvQuote(r.level_id), csvQuote(r.source_file), csvQuote(r.offset) }
  for _, k in ipairs(EDIT_FIELDS) do out[#out+1] = csvQuote(r[k]) end
  out[#out+1] = os.date("%Y-%m-%d %H:%M:%S")
  f:write(table.concat(out, ",") .. "\r\n")
  f:close(); return true
end

-- Save name/requirement/flags for r as one log line, with undo.
local function commit(r, newName, newReq)
  local snap = { r = r }
  for _, k in ipairs(EDIT_FIELDS) do snap[k] = r[k] end
  local soft, ver, notes = flagValues(r)
  r.friendly_name, r.sub_region = newName, newReq
  r.can_softlock, r.is_verified, r.notes = soft, ver, notes
  if not appendLog(r) then
    for _, k in ipairs(EDIT_FIELDS) do r[k] = snap[k] end
    setStatus("ERROR: cannot write " .. LOG_PATH); return false
  end
  table.insert(undoStack, snap)
  rebuildCounts(r.level_id)
  flagDirty, flagKey, selectedKey, lastSig = false, nil, nil, ""
  return true
end

local function saveFlags()
  local r = target()
  if not r then setStatus("Nothing selected."); return end
  if commit(r, r.friendly_name, r.sub_region) then
    setStatus(string.format("Saved flags for %s (softlock=%s verified=%s notes=%q)",
      r.offset, r.can_softlock, r.is_verified, r.notes))
  end
end

local function nameIt()
  local r = target()
  if not r then setStatus("Nothing selected / no unnamed location in range."); return end
  if trim(roomBox.Text) == "" then setStatus("Set a Room first."); return end
  local newName = buildName(r)
  local newReq = reqChk.Checked and trim(reqBox.Text) or r.sub_region
  if newReq == "" then newReq = r.sub_region end
  if not commit(r, newName, newReq) then return end
  sessionNamed = sessionNamed + 1
  local room = trim(roomBox.Text)
  local found = false
  for i = 0, roomBox.Items.Count - 1 do if roomBox.Items[i] == room then found = true end end
  if not found then roomBox.Items.Insert(0, room) end
  setStatus("Named " .. r.offset .. " -> " .. newName)
end

local function undo()
  local u = table.remove(undoStack)
  if not u then setStatus("Nothing to undo."); return end
  local text = readFile(LOG_PATH) or ""
  local lines = {}
  for line in (text .. "\n"):gmatch("(.-)\r?\n") do lines[#lines+1] = line end
  local prefix = table.concat({ csvQuote(u.r.level_id), csvQuote(u.r.source_file), csvQuote(u.r.offset) }, ",") .. ","
  for i = #lines, 2, -1 do
    if lines[i]:sub(1, #prefix) == prefix then table.remove(lines, i); break end
  end
  while #lines > 0 and lines[#lines] == "" do table.remove(lines) end
  local f = io.open(LOG_PATH, "wb"); f:write(table.concat(lines, "\r\n") .. "\r\n"); f:close()
  local renamed = u.r.friendly_name ~= u.friendly_name
  for _, k in ipairs(EDIT_FIELDS) do u.r[k] = u[k] end
  rebuildCounts(u.r.level_id)
  if renamed then sessionNamed = math.max(0, sessionNamed - 1) end
  flagDirty, flagKey, lastSig = false, nil, ""
  setStatus("Undid " .. u.r.offset .. " (back to: " .. u.friendly_name .. ")")
end

local function cycleTag()
  local i = (tagBox.ItemIndex or 0) + 1
  if i >= tagBox.Items.Count then i = 0 end
  tagBox.ItemIndex = i
end

local function setLevel(level)
  if level == currentLevel then return end
  currentLevel = level; levelEdit.Text = level or ""
  selectedKey, lastSig = nil, ""
  fillReqChoices(level)
  setStatus("Level: " .. tostring(level))
end

------------------------------------------------------------------------
-- Tick
------------------------------------------------------------------------
local function readLive()
  local base = getAddressSafe(BASE)
  if not base or base == 0 then return false end
  player.x = readFloat(base + OFFSET_X) or 0
  player.y = readFloat(base + OFFSET_Y) or 0
  player.z = readFloat(base + OFFSET_Z) or 0
  if autoChk.Checked then
    local num = readInteger(base + CURRENT_LEVEL_RVA)
    local lv = num and LEVEL_NUMBER_TO_ID[num]
    if lv then setLevel(lv) end
  end
  return true
end

local function tick()
  local live = readLive()
  if not autoChk.Checked and trim(levelEdit.Text) ~= currentLevel then setLevel(trim(levelEdit.Text)) end
  local range = tonumber(threshEdit.Text) or 2000

  local found = {}
  for _, r in ipairs(byLevel[currentLevel] or {}) do
    if not onlyChk.Checked or isGeneric(r) or r.key == selectedKey then
      local dx, dy, dz = r.x - player.x, r.y - player.y, r.z - player.z
      local d = math.sqrt(dx*dx + dy*dy + dz*dz)
      if d <= range then found[#found+1] = { r = r, d = d } end
    end
  end
  table.sort(found, function(a, b) return a.d < b.d end)
  while #found > MAX_LIST do table.remove(found) end
  listed = found

  local sigParts = {}
  for _, e in ipairs(found) do sigParts[#sigParts+1] = e.r.key .. math.floor(e.d / 50) end
  local sig = table.concat(sigParts, ";")
  if sig ~= lastSig then
    lastSig = sig
    list.Items.Clear()
    local sel = -1
    for i, e in ipairs(found) do
      local r = e.r
      list.Items.Add(string.format("%5.0f %+6.0f  %-10s %-7s %-22s %s",
        e.d, r.y - player.y, r.source_file, r.offset, (isGeneric(r) and "* " or "  ") .. r.friendly_name:sub(1, 60),
        (r.sub_region ~= "N" and ("[" .. r.sub_region .. "]") or "")
          .. (r.can_softlock == "TRUE" and " SOFTLOCK" or "")
          .. (r.is_verified == "FALSE" and " UNVERIFIED" or "")))
      if r.key == selectedKey then sel = i - 1 end
    end
    list.ItemIndex = sel
  end

  local r = target()
  if not flagDirty and (r and r.key or nil) ~= flagKey then loadFlags(r) end
  if r then
    preview.Caption = buildName(r)
    previewSub.Caption = string.format("%s %s  %s/%s   was: \"%s\"   requires: %s  (was %s)",
      r.category, r.object, r.source_file, r.offset, r.friendly_name,
      reqChk.Checked and trim(reqBox.Text) ~= "" and trim(reqBox.Text) or r.sub_region, r.sub_region)
    local dy = r.y - player.y
    local height = math.abs(dy) < 40 and "same height"
      or string.format("%.0f %s you", math.abs(dy), dy > 0 and "above" or "below")
    coordLbl.Caption = string.format(
      "You:    X %9.1f  Y %9.1f  Z %9.1f\n" ..
      "Target: X %9.1f  Y %9.1f  Z %9.1f   (%.0f away, %s)",
      player.x, player.y, player.z, r.x, r.y, r.z,
      math.sqrt((r.x-player.x)^2 + dy^2 + (r.z-player.z)^2), height)
  else
    preview.Caption = live and "(no unnamed location in range)" or "(game not attached)"
    previewSub.Caption = ""
    coordLbl.Caption = string.format("You:    X %9.1f  Y %9.1f  Z %9.1f", player.x, player.y, player.z)
  end
end

------------------------------------------------------------------------
-- Wiring
------------------------------------------------------------------------
list.OnClick = function()
  local i = list.ItemIndex
  if i and i >= 0 and listed[i + 1] then
    selectedKey = listed[i + 1].r.key
    if not reqChk.Checked then reqBox.Text = listed[i + 1].r.sub_region end
  end
end
nameBtn.OnClick = nameIt
saveBtn.OnClick = saveFlags
local function markDirty() if not flagLoading and flagKey then flagDirty = true end end
softChk.OnChange = markDirty
verChk.OnChange = markDirty
notesBox.OnChange = markDirty
reqChk.OnChange = function() reqBox.Enabled = reqChk.Checked end
roomBox.OnChange = function() tagBox.ItemIndex = 0 end  -- tags rarely carry over between rooms
undoBtn.OnClick = undo
copyBtn.OnClick = function() writeToClipboard(preview.Caption) end
reloadBtn.OnClick = function()
  local ok, msg = loadData(); local lv = currentLevel; currentLevel = nil
  setLevel(lv); setStatus(msg)
end

local timer = createTimer(nil)
timer.Interval = UPDATE_MS
timer.OnTimer = function()
  local ok, err = pcall(tick)
  if not ok then status.Caption = "ERROR: " .. tostring(err) end
end

local hotkeys = {
  createHotkey(function() nameIt() end, VK_F5),
  createHotkey(function() cycleTag() end, VK_F6),
  createHotkey(function() undo() end, VK_F7),
  createHotkey(function() onlyChk.Checked = not onlyChk.Checked; lastSig = "" end, VK_F8),
  createHotkey(function() saveFlags() end, VK_F9),
}

form.OnClose = function()
  timer.destroy()
  for _, h in ipairs(hotkeys) do h.destroy() end
  return caFree
end

local ok, msg = loadData()
setLevel("swampday")
setStatus(msg)
timer.Enabled = true
form.show()
print("Location Namer running. " .. msg)
