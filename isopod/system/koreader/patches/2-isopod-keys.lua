-- isoPod: KOReader on a handheld with a gamepad, as the rest of the system uses it.
--
--   left/right, up/down  turn the page     A  the next page, and selects in a menu
--   B  back; from the page, leave the book, which keeps its place
--   X  KOReader's menu                     Y  the text: its size, spacing and the like
--
-- Select, Start and the four shoulder buttons do nothing here. Home, the volume keys
-- and the power key are isopodd's, which also has Start play or pause the music.
local Device = require("device")
local Gamepad = require("device/sdl/gamepad")

-- What the device is: buttons and a D-pad, no keyboard. The environment's
-- DISABLE_TOUCH=1 has said that it has no touch screen, and would make it a Kindle 4.
local no = function() return false end
Device.hasKeyboard = no
Device.hasSymKey = no
Device.useDPadAsActionKeys = no

-- SDL numbers the face buttons by where they sit: 0 at the bottom (B), 1 on the
-- right (A), 2 X and 3 Y. A button left out of the table does nothing.
Gamepad.button_key_names = {
    [0] = "Back",
    [1] = "Press",
    [2] = "Menu",
    [3] = "AA",
    [11] = "Up",
    [12] = "Down",
    [13] = "Left",
    [14] = "Right",
}
Gamepad.axis_key_names = {}

local function also(module, change)
    local Module = require(module)
    local register = Module.registerKeyEvents
    function Module:registerKeyEvents()
        register(self)
        change(self.key_events)
    end
end

-- KOReader opens the text settings on Press as well; here Press is the next page.
also("apps/reader/modules/readerconfig", function(keys)
    keys.ShowConfigMenu = { { "AA" } }
end)
also("apps/reader/modules/readerrolling", function(keys)
    table.insert(keys.GotoNextView[1][1], "Press")
end)
also("apps/reader/modules/readerpaging", function(keys)
    table.insert(keys.GotoNextPage[1][1], "Press")
end)
