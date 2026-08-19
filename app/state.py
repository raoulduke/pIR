class AppState:
    """Central mutable application state.

    A single instance is created at startup and passed through the call
    chain so every layer shares the same object without global variables.
    """

    def __init__(self):
        self.running: bool = True
        self.active_home: str | None = None

        self.input_available: bool = False

        self.ir_tx_available: bool = False
        self.ir_rx_available: bool = False

        self.network_connected: bool = False
        self.ip_address: str | None = None
