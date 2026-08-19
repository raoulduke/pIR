class StartupError(Exception):
    """Raised when a startup verification step fails."""


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


class HardwareError(Exception):
    """Raised when a hardware subsystem is unavailable."""


class SignalError(Exception):
    """Raised when an IR signal operation fails."""
