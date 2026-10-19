# ---------------------------------------------------------------------------
# UI string constants
# ---------------------------------------------------------------------------

# Prompts
PROMPT_BUTTON = "Button:"
PROMPT_NAME = "Name:"

# Breadcrumb names (screens/widgets/format.py's crumbs())
MENU_TITLE = "Menu"
STEP_DEVICE = "Device"

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
STATEMENT_FAILED = "Failed!"
STATEMENT_RENAMED = "Renamed!"
STATEMENT_NO_SIGNALS = "No signals"
# Exit message during an OS power-off: the backlight stays on until
# systemd/system-shutdown/shutdown-signal.sh turns it off at the last moment.
STATEMENT_SHUTTING_DOWN = "Shutting down..."
# A Hue bulb the bridge can't reach — almost always cut at the wall switch.
STATEMENT_LIGHT_OFF = "* Not connected"

# Main menu options (screens/main_menu.py)
MENU_LEARN = "Learn"
MENU_SWEEP = "Sweep"
MENU_RENAME = "Rename"
MENU_COMMAND = "Command"
MENU_HOME = "Change Home"
MENU_SETTINGS = "Settings"
MENU_NETWORK = "WiFi"
MENU_IP = "IP Address"

# Sweep menu (screens/fun_screen.py, screens/code_sweep_screen.py)
FUN_SAVED = "Saved"
FUN_ALL_BRANDS = "All Brands"             # every brand's codes in one sweep
FUN_SIGNALS_DIR = "fun_signals"           # saves, relative to project root
FUN_CODES_FILE = "data/fun_codes.json"    # built by scripts/build_fun_codes.py
# Default gap between codes (Menu > Settings > Sweep Speed overrides it). Most
# TVs go dark 0.3-1s after a power code, so at 1s the code that worked is
# usually still on screen when the TV reacts.
SWEEP_GAP_SECONDS = 1.0

# Rename submenu options (screens/rename_menu_screen.py)
RENAME_HOME = "Home"
RENAME_DEVICE = "Device"
RENAME_SIGNAL = "Signal"

# How often the main menu re-checks wifi_service.is_ready() while WiFi is
# highlighted, so "WiFi loading" clears on its own once NetworkManager is up.
MENU_WIFI_POLL_INTERVAL_SECONDS = 2

# Wi-Fi (services/wifi_service.py drives NetworkManager via nmcli)
WIFI_INTERFACE = "wlan0"
WIFI_CONNECT_TIMEOUT_SECONDS = 15   # wait for an IP before giving up
WIFI_RESCAN_INTERVAL_SECONDS = 10   # re-scan while the network list is up
WIFI_SCAN_STOP_AFTER_UNCHANGED = 5  # stop re-scanning after this many find nothing new

# Active Device Control's default device, the one whose Up/Down/Left/Right
# drive the Google TV D-pad instead of cycling functions.
DEFAULT_ACTIVE_DEVICE = "google_tv"

# Google TV screen's line 1 last column: the Android TV Remote connection.
ANDROIDTV_STATUS_CHARS = {"closed": "-", "connecting": "+", "open": "*"}

# Devices whose per-location leaves collapse into one < Device/Device > stop
# (device -> label on its "Press Enter" screen). Enter drills into the first
# leaf; Left/Right then cycles the rest.
MENU_DEVICE_LABELS = {"light": "Lights", "tv": "TVs"}

# Synthetic active_device value for each collapsed stop — never a real
# signals directory.
MENU_DEVICE_SENTINELS = {device: f"__menu_{device}__" for device in MENU_DEVICE_LABELS}
MENU_SENTINEL_TO_DEVICE = {sentinel: device for device, sentinel in MENU_DEVICE_SENTINELS.items()}

# Misc
LABEL_CANCEL = "Cancel"
LABEL_LEARN_ANYWAY = "Learn anyway"
LABEL_TRY_AGAIN = "Try again"
LABEL_OVERWRITE = "Overwrite?"
LABEL_NEW_HOME = "New Home"
LABEL_NEW_DEVICE = "New Device"
LABEL_NEW_LOCATION = "New Location"

# IR hardware
LIRC_TX_DEVICE = "/dev/lirc0"
LIRC_RX_DEVICE = "/dev/lirc1"

# Signal capture
SIGNAL_TAIL_CAPTURE_SECONDS = 0.5
# Give up if no real frame arrives within this long: no button was pressed.
SIGNAL_WAIT_TIMEOUT_SECONDS = 15
MIN_SIGNAL_LENGTH = 20

# File ownership (the app runs as root)
FILE_OWNER = "matt"

# Signals directory (relative to project root)
SIGNALS_DIR = "signals"

# Explicit cycle order for list_devices() (keyed by home) and
# list_locations() (keyed by "<home>/<device>"); anything unlisted follows,
# alphabetically.
DEVICE_ORDER_FILE = "config/device_order.json"

# Keypad matrix — BCM pins in wiring-table order (wire 1 .. 12). The index is
# how the wizard and the scanner identify a physical wire, so order matters.
KEYPAD_GPIO_PINS = [14, 15, 17, 18, 27, 5, 6, 13, 19, 16, 26, 20]

# Written by scripts/keypad_wizard.py, read by hardware/keypad.py
KEYPAD_MAP_FILE = "config/keypad_map.json"

# Device-slot and preset config
KEYPAD_DEVICES_FILE = "config/keypad_devices.json"
KEYPAD_PRESETS_FILE = "config/keypad_presets.json"
PRESET_DEFAULT_DELAY_SECONDS = 0.3

# How long "Sending..."/"Running..." stays up before control returns to the
# keypad (the steps run in the background meanwhile); without it the next
# redraw hides the message before it can be read.
ASYNC_SEND_FLASH_SECONDS = 0.4

# Default backlight idle timeout (Menu > Settings > Backlight overrides it).
# The backlight draws 80-120mA, about as much as the idle Pi, so this is the
# biggest single battery lever.
BACKLIGHT_IDLE_SECONDS = 30

# Settings menu (screens/settings_screen.py). None is "Never", listed first.
SETTINGS_BACKLIGHT = "Backlight"
BACKLIGHT_TIMEOUT_CHOICES = [None, 5, 10, 15, 20, 25, 30]
SETTINGS_SWEEP_SPEED = "Sweep Speed"
SWEEP_GAP_CHOICES = [1.0, 0.5, 0.25]   # seconds between codes

# Keypad scan timing. Kept light: too much GPIO traffic (short gaps, long
# per-pin settle) disrupts the LCD's bit-banged I2C on GPIO23/24.
KEYPAD_SCAN_DELAY_SECONDS = 0.0005  # settle time after driving a pin low
KEYPAD_DEBOUNCE_SCANS = 3           # consecutive identical reads required
# Gap between sweeps while polling (after an edge wake, or when edge
# detection is unavailable): also the shortest press guaranteed to be seen.
KEYPAD_POLL_INTERVAL_SECONDS = 0.01
# After an edge wake, go back to sleep once nothing has read as pressed for
# this long (contact noise, or a press too short to debounce).
KEYPAD_WAKE_IDLE_SECONDS = 0.05

# Auto-repeat while a scroll key is held (lists, the character picker)
KEYPAD_REPEAT_DELAY_SECONDS = 0.4     # hold time before repeat kicks in
KEYPAD_REPEAT_INTERVAL_SECONDS = 0.1  # pace once repeating
# Tap vs. hold for keys a screen opts into (hardware.input.set_hold_keys();
# the sweep's Enter). A tap then acts on release instead of on press.
KEYPAD_HOLD_SECONDS = 0.7
