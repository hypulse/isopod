-- isoPod: KOReader's menu while a book is open, cut down to reading. Five tabs are left:
-- where in the book, how the text looks, the screen and the status bar, tools, and the
-- book itself. Tools is KOReader's own list: with its plugins off it holds the reading
-- statistics and the list of plugins, and a plugin somebody adds finds its place there.
-- A list here replaces KOReader's own; what a list drops must also be named under
-- KOMenu:disabled, or KOReader shows it again in the first tab, marked NEW.
return {
    ["KOMenu:menu_buttons"] = { "navi", "typeset", "setting", "tools", "main" },
    navi = {
        "table_of_contents",
        "bookmarks",
        "toggle_bookmark",
        "----------------------------",
        "go_to",
        "skim_to",
    },
    setting = {
        "night_mode",
        "----------------------------",
        "screen",
        "status_bar",
    },
    screen = {
        "screen_rotation",
        "screen_dpi",
        "color_rendering",
    },
    main = {
        "book_status",
        "book_info",
    },
    ["KOMenu:disabled"] = {
        -- the tabs for searching and KOReader's file browser
        "search", "search_settings", "filemanager",
        -- from navi
        "bookmark_browsing_mode", "navi_settings", "hide_nonlinear_flows", "book_map", "page_browser",
        "autoturn", "go_to_previous_location", "go_to_next_location",
        -- from setting; language_support would look for the document menu
        "frontlight", "network", "taps_and_gestures", "navigation", "document", "language_support",
        "language", "device",
        -- from screen
        "screensaver", "coverimage", "autodim", "screen_eink_opt", "autowarmth", "screen_timeout",
        "fullscreen", "screen_notification",
        -- from main
        "history", "open_previous_document", "favorites", "collections", "bookmark_browser",
        "mass_storage_actions", "ota_update", "help", "exit_menu",
    },
}
