class AppState:
    """Central mutable application state.

    A single instance is created at startup and passed through the call
    chain so every layer shares the same object without global variables.
    """

    def __init__(self):
        self.running: bool = True
        self.active_home: str | None = None

        # Active Device Control's current device. None until the first frame
        # picks the default; not persisted across restarts or home switches.
        self.active_device: str | None = None
        self.active_location: str | None = None

