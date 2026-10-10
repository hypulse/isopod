-- isoPod: KOReader on the RG35XX SP follows the same rules as the rest of the system.
-- A selects, B goes back (leaving a book saves the page and returns to isoPod),
-- X and Start open the menu, Y opens the context menu, the shoulders and triggers
-- turn pages. Home belongs to isoPod, so KOReader never sees it.
local ok, Gamepad = pcall(require, "device/sdl/gamepad")
if not ok then
    return
end

local keys = Gamepad.button_key_names
keys[0] = "Press"       -- A
keys[1] = "Back"        -- B
keys[2] = "ContextMenu" -- Y
keys[3] = "Menu"        -- X
keys[4] = nil           -- Select
keys[5] = nil           -- Home
keys[6] = "Menu"        -- Start
keys[9] = "RPgBack"     -- L1
keys[10] = "RPgFwd"     -- R1

Gamepad.axis_key_names[4] = { plus = "RPgBack" } -- L2
Gamepad.axis_key_names[5] = { plus = "RPgFwd" }  -- R2
