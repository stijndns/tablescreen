"""
settings.py — The gateway's settings: command-line options, else
environment variables (for the container), else defaults that match the
app's (run from the repository root).

All local files live in one data folder, ``api/`` by default (gitignored):
the bridge secret (made by the app), the certificate (``api cert new`` in
the app) and the accounts database (``tools/prepare_accounts.py``).
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

ENV = "TABLESCREEN_GATEWAY_"


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    bridge_host: str
    bridge_port: int
    data: Path
    cert_file: Path
    key_file: Path

    @property
    def secret_file(self) -> Path:
        return self.data / "bridge.secret"

    @property
    def accounts_file(self) -> Path:
        return self.data / "accounts.db"


def parse(argv: Optional[Sequence[str]] = None,
          environ: Mapping[str, str] = os.environ) -> Settings:
    def env(name: str, default: str) -> str:
        return environ.get(ENV + name, default)

    parser = argparse.ArgumentParser(
        prog="python -m gateway",
        description="The Tablescreen player webapp's gateway. Every option can also be set"
                    f" as an environment variable {ENV}<OPTION>, e.g. {ENV}PORT.")
    parser.add_argument("--host", default=env("HOST", "0.0.0.0"),
                        help="address to serve the webapp on (default: all)")
    parser.add_argument("--port", type=int, default=int(env("PORT", "8443")),
                        help="HTTPS port (default 8443)")
    parser.add_argument("--bridge-host", default=env("BRIDGE_HOST", "127.0.0.1"),
                        help="the app's bridge address (default 127.0.0.1)")
    parser.add_argument("--bridge-port", type=int, default=int(env("BRIDGE_PORT", "8765")),
                        help="the app's bridge port (default 8765)")
    parser.add_argument("--data", default=env("DATA", "api"),
                        help="folder with bridge.secret, cert.pem, key.pem and"
                             " accounts.db (default: api)")
    parser.add_argument("--cert", default=env("CERT", ""),
                        help="certificate file (default: <data>/cert.pem)")
    parser.add_argument("--key", default=env("KEY", ""),
                        help="certificate key file (default: <data>/key.pem)")
    args = parser.parse_args(argv)
    data = Path(args.data)
    return Settings(host=args.host, port=args.port,
                    bridge_host=args.bridge_host, bridge_port=args.bridge_port,
                    data=data,
                    cert_file=Path(args.cert) if args.cert else data / "cert.pem",
                    key_file=Path(args.key) if args.key else data / "key.pem")
