from app.exceptions import ConfigError
from services import config_service, signal_service


def get_active_home() -> str:
    return config_service.get_active_home()


def set_active_home(name: str) -> None:
    config_service.set_active_home(name)


def verify_active_home() -> str:
    """Return the active home name or raise ConfigError."""
    home = get_active_home()
    if not home:
        raise ConfigError("No active home configured")
    if not signal_service.home_exists(home):
        raise ConfigError(f"Home '{home}' directory not found")
    return home


def list_homes() -> list[str]:
    return signal_service.list_homes()


def create_home(name: str) -> None:
    signal_service.create_home(name)
