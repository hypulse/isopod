-- isoPod: KOReader on a handheld with a gamepad, as the rest of the system uses it.
--
--   left/right, up/down  turn the page     A  the next page, and selects in a menu
--   left/right  in a menu or a list, where nothing stands beside the row, its pages
--   B  back; from the page, leave the book, which keeps its place
--   X  KOReader's menu                     Y  the text: its size, spacing and the like
--   Select  start selecting text, as "Start text selection" in the menu does, and
--           give it up again until the selection is made
--
-- Start and the four shoulder buttons do nothing here. Home, the volume keys and the
-- power key are isopodd's, which also has Start play or pause the music.
local Device = require("device")
local Gamepad = require("device/sdl/gamepad")

-- What the device is: buttons and a D-pad, no keyboard. The environment's
-- DISABLE_TOUCH=1 has said that it has no touch screen, and would make it a Kindle 4.
local no = function() return false end
Device.hasKeyboard = no
Device.hasSymKey = no
Device.useDPadAsActionKeys = no

-- SDL numbers the face buttons by where they sit: 0 at the bottom (B), 1 on the
-- right (A), 2 X and 3 Y; 4 is Select. A button left out of the table does nothing.
Gamepad.button_key_names = {
    [0] = "Back",
    [1] = "Press",
    [2] = "Menu",
    [3] = "AA",
    [4] = "Select",
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

-- Select is a key name of isoPod's own, which nothing else in KOReader answers. It
-- brings the cross, and takes it away again with what it had begun to select.
local Selection = require("apps/reader/modules/readerkeyselection")
function Selection:onToggleHighlightIndicator()
    if self:isActive() then
        return self:stopHighlightIndicator(true)
    end
    return self:startHighlightIndicator()
end
also("apps/reader/modules/readerkeyselection", function(keys)
    keys.ToggleHighlightIndicator = { { "Select" } }
end)

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

-- KOReader turns the pages of a menu or a list with page keys, which this device has
-- not. Left and right move the focus where something stands beside it; under a menu's
-- icons and in a list nothing does, and there they turn the page.
local function sideways_turns_pages(module, pages, turned)
    local Widget = require(module)
    local move = Widget.onFocusMove
    function Widget:onFocusMove(args)
        local x, y = self.selected.x, self.selected.y
        local handled = move(self, args)
        local stayed = self.selected.x == x and self.selected.y == y
        if args[1] == 0 or args[2] ~= 0 or not stayed or (self[pages] or 1) < 2 then
            return handled
        end
        if args[1] > 0 then
            self:onNextPage()
        else
            self:onPrevPage()
        end
        if turned then
            turned(self)
        end
        return true
    end
end

-- A menu shown anew has its focus on the icons, unseen. On a page turned to, and in a
-- menu below another, the rows are where it was and where it is wanted.
local TouchMenu = require("ui/widget/touchmenu")
local function focus_first_row(menu)
    menu:moveFocusTo(menu.cur_tab, 2)
end
local update = TouchMenu.updateItems
function TouchMenu:updateItems(...)
    update(self, ...)
    if self.item_table_stack and #self.item_table_stack > 0 then
        focus_first_row(self)
    end
end

sideways_turns_pages("ui/widget/touchmenu", "page_num", focus_first_row)
sideways_turns_pages("ui/widget/menu", "page_num")
sideways_turns_pages("ui/widget/keyvaluepage", "pages")
