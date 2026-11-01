"""
python -m gateway [options] — Start the gateway (see --help).
"""

from __future__ import annotations

import logging
import socket
import sys

import uvicorn

from . import settings as gateway_settings
from .accounts import AccountStore
from .bridge_client import BridgeClient
from .web import create_app


def lan_addresses() -> list[str]:
    """This machine's IPv4 addresses other than loopback, to print the
    webapp's links."""
    found = set()
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))      # TEST-NET-1: nothing is sent
            found.add(probe.getsockname()[0])
        except OSError:
            pass
    return sorted(a for a in found if not a.startswith("127."))


def main() -> None:
    settings = gateway_settings.parse()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    log = logging.getLogger("gateway")

    if not settings.cert_file.exists() or not settings.key_file.exists():
        sys.exit(f"No certificate in {settings.cert_file} / {settings.key_file}:"
                 f" type 'api cert new' in Tablescreen's console first.")
    accounts = AccountStore(settings.accounts_file)
    if not accounts.exists():
        sys.exit(f"No accounts in {settings.accounts_file}: prepare them first with"
                 f" 'python tools/prepare_accounts.py <name>:<role> …'.")
    accounts.create()                   # tables a newer gateway added, if any
    purged = accounts.purge_sessions()
    if purged:
        log.info("Forgot %d sessions unused for 30 days.", purged)

    bridge = BridgeClient(settings.bridge_host, settings.bridge_port,
                          settings.secret_file, accounts)
    app = create_app(accounts, bridge, background=(bridge.run,))

    links = [f"https://{a}:{settings.port}" for a in lan_addresses()]
    log.info("The webapp: %s", ", ".join(links) or f"https://localhost:{settings.port}")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning",
                access_log=False, ssl_certfile=str(settings.cert_file),
                ssl_keyfile=str(settings.key_file))


if __name__ == "__main__":
    main()
