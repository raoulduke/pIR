class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


class HardwareError(Exception):
    """Raised when a hardware subsystem is unavailable."""
