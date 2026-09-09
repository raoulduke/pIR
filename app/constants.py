# ---------------------------------------------------------------------------
# UI string constants
# ---------------------------------------------------------------------------

# Prompts
PROMPT_DEVICE = "Device:"
PROMPT_LOCATION = "Location:"
PROMPT_BUTTON = "Button:"
PROMPT_SEARCH = "Search:"
PROMPT_HOME_NAME = "Home name:"

# Status statements
STATEMENT_NO_SIGNAL = "No signal!"
STATEMENT_RECEIVING = "Receiving..."
STATEMENT_SIGNAL_DETECTED = "Signal detected"
STATEMENT_FILE_EXISTS = "File exists"
STATEMENT_INVALID_INPUT = "Invalid input"
STATEMENT_TRY_AGAIN = "Try again"
STATEMENT_CAPTURED = "Captured!"
STATEMENT_SAVED = "Saved!"
STATEMENT_SAVE_FAILED = "Save failed!"
STATEMENT_SENDING = "Sending..."
STATEMENT_SENT = "Done!"
STATEMENT_FAILED = "Failed!"

# Menu options (screens/main_menu.py — the escape-hatch reached via the
# physical Menu key; see docs/active-device-control-plan.md)
MENU_LEARN = "Learn"
MENU_COMMAND = "Command"
MENU_HOME = "Change Home"
MENU_NETWORK = "WiFi"
MENU_QUIT = "Quit"
MENU_IP = "IP Address"

# How often screens/main_menu.py re-checks wifi_service.is_ready() while the
# WiFi entry is the highlighted one, so "WiFi loading..." clears back to
# "WiFi" (and the entry re-enables) on its own once NetworkManager comes up
# — without this, nothing would redraw the menu until the next keypress.
MENU_WIFI_POLL_INTERVAL_SECONDS = 2

# Wi-Fi (screens/network_screen.py, services/wifi_service.py) — wraps
# NetworkManager (nmcli), which actively manages wlan0 on this Pi.
WIFI_INTERFACE = "wlan0"
WIFI_CONNECT_TIMEOUT_SECONDS = 15  # how long to wait for dhcpcd to get an IP before giving up
WIFI_RESCAN_INTERVAL_SECONDS = 10  # re-scan while sitting on the network list, not just on entry
WIFI_SCAN_STOP_AFTER_UNCHANGED = 5  # give up auto-rescanning after this many finds nothing new

# Active Device Control (screens/active_device_screen.py) — the device
# that gets raw D-pad passthrough instead of a cycled function list.
DEFAULT_ACTIVE_DEVICE = "google_tv"

# Sentinel for the "Light" top-level menu stop in < Device/Device >
# cycling — not a real signal_service leaf/directory, never passed to
# anything filesystem-backed. See active_device_screen.py's
# cycle_top_level()/light_leaves() and its LIGHT_MENU_DEVICE branch in
# show()/render_after_cycle().
LIGHT_MENU_DEVICE = "__light_menu__"

# Misc
LABEL_ENTER_LOCATION = "Enter location"
LABEL_SINGLE_DEVICE = "Single device"
LABEL_CANCEL = "Cancel"
LABEL_TRY_AGAIN = "Try again"
LABEL_OVERWRITE = "Overwrite?"
LABEL_NEW_HOME = "+ New Home"

# IR hardware
LIRC_TX_DEVICE = "/dev/lirc0"
LIRC_RX_DEVICE = "/dev/lirc1"

# Signal capture
SIGNAL_TAIL_CAPTURE_SECONDS = 0.5
# Give up and return "" if no real frame (see hardware/ir_receiver.py's
# noise filter) shows up at all within this long — almost certainly means
# no button was actually pressed, not that one is still coming.
SIGNAL_WAIT_TIMEOUT_SECONDS = 15
MIN_SIGNAL_LENGTH = 20

# File ownership
FILE_OWNER = "matt"

# Signals directory (relative to project root)
SIGNALS_DIR = "signals"

# Explicit ordering, consumed by services/signal_service.py's
# list_devices() (< Device/Device > cycle order, keyed by home — e.g.
# "home") and list_locations() (Left/Right cycle order within one device,
# keyed by "<home>/<device>" — e.g. "home/light"). Either way, anything
# not listed for its key falls back to alphabetical, after every listed
# one.
DEVICE_ORDER_FILE = "config/device_order.json"

# Keypad matrix — BCM pin numbers in wiring-table order (wire 1 .. wire 12).
# See CLAUDE.md Pin Wiring Table. Order matters: it is the index the wizard
# and the runtime scanner both use to identify a physical wire.
KEYPAD_GPIO_PINS = [14, 15, 17, 18, 27, 5, 6, 13, 19, 16, 26, 20]

# Mapping file produced by scripts/keypad_wizard.py, consumed by hardware/keypad.py
KEYPAD_MAP_FILE = "config/keypad_map.json"

# Device-slot and preset config consumed by services/device_slot_service.py
# and services/preset_service.py.
KEYPAD_DEVICES_FILE = "config/keypad_devices.json"
KEYPAD_PRESETS_FILE = "config/keypad_presets.json"
PRESET_DEFAULT_DELAY_SECONDS = 0.3

# Debounce / scan timing
# Kept deliberately light: a full sweep already drives every one of the 12
# matrix pins in turn, and this thread runs continuously for the life of the
# app. Too aggressive here (short idle gap, long per-pin settle) generates
# enough sustained RPi.GPIO traffic to disrupt the LCD's bit-banged software
# I2C (GPIO23/24) — seen as garbled LCD output / crashes during testing.
KEYPAD_SCAN_DELAY_SECONDS = 0.0005  # settle time after driving a pin low
KEYPAD_DEBOUNCE_SCANS = 3           # consecutive identical reads required
# gap between sweeps when idle — this is also the minimum press-and-release
# duration guaranteed to be caught at all: a press shorter than this can
# land entirely between two idle sweeps and never get sampled once, not
# just debounced away (confirmed 2026-09-09: missed presses were random
# across buttons and reduced by holding longer, both pointing at this gap
# rather than any one button's hardware). Lowered from 0.05 — the settle
# time above is unchanged and already small, so this keeps the specific
# "long per-pin settle" half of the historical I2C-disruption combination
# out of the picture; re-check for LCD garbling if this ever needs to move
# further.
KEYPAD_POLL_INTERVAL_SECONDS = 0.01

# Auto-repeat while Up/Down is held (see hardware/keypad.py's
# KeypadScanner._repeat_while_held) — scrolling long lists or the
# character picker (screens/widgets/char_input.py) one press at a time
# is tedious otherwise.
KEYPAD_REPEAT_DELAY_SECONDS = 0.4     # hold time before repeat kicks in
KEYPAD_REPEAT_INTERVAL_SECONDS = 0.1  # pace once repeating
