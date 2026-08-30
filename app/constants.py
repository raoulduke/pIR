# ---------------------------------------------------------------------------
# UI string constants
# ---------------------------------------------------------------------------

# Prompts
PROMPT_DEVICE = "Device:"
PROMPT_LOCATION = "Location:"
PROMPT_BUTTON = "Button:"
PROMPT_COMMAND = "Command:"

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
MENU_DEVICES = "Device List"
MENU_LEARN = "Learn"
MENU_COMMAND = "Command"
MENU_HOME = "Home"
MENU_SETTINGS = "Settings"
MENU_QUIT = "Quit"
MENU_IP = "IP Address"

# Active Device Control (screens/active_device_screen.py) — the device
# that gets raw D-pad passthrough instead of a cycled function list.
DEFAULT_ACTIVE_DEVICE = "google_tv"

# Misc
LABEL_ENTER_LOCATION = "Enter location"
LABEL_SINGLE_DEVICE = "Single device"
LABEL_CANCEL = "Cancel"
LABEL_TRY_AGAIN = "Try again"
LABEL_OVERWRITE = "Overwrite?"

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
KEYPAD_POLL_INTERVAL_SECONDS = 0.05 # gap between sweeps when idle
