import subprocess


def shutdown() -> None:
    subprocess.run(["sudo", "poweroff"], check=True)


def reboot() -> None:
    subprocess.run(["sudo", "reboot"], check=True)
