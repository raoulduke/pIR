#!/usr/bin/env python3
"""One-time Android TV Remote v2 pairing helper.

Run this ON THE PI, once, before services/androidtv_service.py's Home/Back/
app-launch presets will work — it's a separate pairing flow from adb's
Wireless debugging (no Developer Options needed at all):

    .venv/bin/python3 scripts/pair_androidtv.py

Generates a client certificate/key under the paths configured in
config/settings.json's androidtv_cert_file/androidtv_key_file (created if
missing), starts pairing against androidtv_ip, and prompts for the code
the TV displays on screen. Pairing trust persists in that cert/key pair —
re-run this only if pairing is ever lost (e.g. a TV factory reset).

pihome.service does not need to be stopped for this — it doesn't touch the
IR receiver or LCD, only the network.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from androidtvremote2 import AndroidTVRemote

from services import config_service


async def main() -> None:
    ip = config_service.get_androidtv_ip()
    certfile = config_service.get_androidtv_certfile()
    keyfile = config_service.get_androidtv_keyfile()
    if not ip or not certfile or not keyfile:
        print("androidtv_ip / androidtv_cert_file / androidtv_key_file not set in config/settings.json")
        sys.exit(1)

    Path(certfile).parent.mkdir(parents=True, exist_ok=True)
    Path(keyfile).parent.mkdir(parents=True, exist_ok=True)

    remote = AndroidTVRemote(client_name="pi-ir", certfile=certfile, keyfile=keyfile, host=ip)
    generated = await remote.async_generate_cert_if_missing()
    print(f"Certificate {'generated' if generated else 'already existed'} at {certfile}")

    print(f"Starting pairing with {ip} — watch the TV screen for a code...")
    await remote.async_start_pairing()
    code = input("Enter the pairing code shown on the TV: ").strip()
    try:
        await remote.async_finish_pairing(code)
        print("Paired successfully.")
    except Exception as e:
        print(f"Pairing failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
