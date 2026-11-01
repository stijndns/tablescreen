"""
make_cert.py — The gateway's HTTPS certificate: self-signed, for this PC.

    python tools/make_cert.py                       # show the current one
    python tools/make_cert.py new                   # make a new one
    python tools/make_cert.py new 192.168.1.77      # also for this address

Browsers check that the address they connect to is in the certificate, so
it lists this PC's host name (also as .local), its LAN IPv4 addresses,
localhost and 127.0.0.1. Run it on the table PC, before a session: every
device accepts the certificate once, and a new one means accepting it
again, so it's only renewed when the PC's address changed or it expires.

Run from the repository root; the files are api/cert.pem and api/key.pem
unless --data names another folder (the gateway's --data).
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALID_DAYS = 825        # the longest some platforms accept


@dataclass
class CertInfo:
    names: list[str]
    addresses: list[str]
    expires: datetime.datetime
    fingerprint: str        # SHA-256, as browsers show it


def lan_addresses() -> set[str]:
    """This PC's IPv4 addresses other than loopback."""
    found = set()
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    # The address of the interface that routes outwards; nothing is sent.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))      # TEST-NET-1, never routed
            found.add(probe.getsockname()[0])
        except OSError:
            pass
    return {a for a in found if not a.startswith("127.")}


def generate(cert_file: Path, key_file: Path, extra: list[str] = ()) -> CertInfo:
    """Write a new key and certificate; ``extra`` addresses are added to
    the LAN ones. ValueError for an extra that isn't an IP address."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    for address in extra:
        ipaddress.ip_address(address)       # ValueError
    host = socket.gethostname()
    addresses = sorted(lan_addresses() | set(extra))
    names = [x509.DNSName(n) for n in (host, f"{host}.local", "localhost")]
    ips = [x509.IPAddress(ipaddress.ip_address(a)) for a in [*addresses, "127.0.0.1"]]

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Tablescreen ({host})")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName(names + ips), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .sign(key, hashes.SHA256()))

    cert_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass                                # Windows: the folder's rights apply
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return read(cert_file)


def read(cert_file: Path) -> CertInfo:
    """What a certificate file covers; OSError or ValueError if unreadable."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        san = x509.SubjectAlternativeName([])
    digest = cert.fingerprint(hashes.SHA256()).hex().upper()
    return CertInfo(
        names=san.get_values_for_type(x509.DNSName),
        addresses=[str(a) for a in san.get_values_for_type(x509.IPAddress)],
        expires=cert.not_valid_after_utc,
        fingerprint=":".join(digest[i:i + 2] for i in range(0, len(digest), 2)))


def describe(info: CertInfo) -> None:
    print(f"    Names:     {', '.join(info.names)}")
    print(f"    Addresses: {', '.join(info.addresses)}")
    print(f"    Expires:   {info.expires:%Y-%m-%d}")
    print(f"    SHA-256:   {info.fingerprint}")
    missing = sorted(lan_addresses() - set(info.addresses))
    if missing:
        print(f"This PC's address {', '.join(missing)} isn't in the certificate: devices"
              f" using it will be refused. Make a new one with 'new'.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("command", nargs="?", choices=["new"],
                        help="make a new certificate (without: show the current one)")
    parser.add_argument("addresses", nargs="*", metavar="address",
                        help="extra IP addresses for 'new'")
    parser.add_argument("--data", default=str(ROOT / "api"),
                        help="the gateway's data folder (default: api)")
    args = parser.parse_args(argv)
    cert_file, key_file = Path(args.data) / "cert.pem", Path(args.data) / "key.pem"
    if args.addresses and args.command != "new":
        parser.error("addresses only go with 'new'")

    try:
        if args.command == "new":
            if cert_file.exists():
                answer = input("There is a certificate already; every device will have to"
                               " accept the new one. Replace it? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("Kept the current certificate.")
                    return
            info = generate(cert_file, key_file, args.addresses)
            print(f"Made a new certificate ({cert_file}, key {key_file}).")
            print("Every device has to accept it once (the browser's warning);"
                  " restart the gateway to use it.")
        elif cert_file.exists():
            info = read(cert_file)
            print(f"The certificate in {cert_file}:")
        else:
            print(f"No certificate in {cert_file} yet: python tools/make_cert.py new")
            return
    except ImportError:
        sys.exit("This needs the gateway's packages:"
                 " python -m pip install -r gateway/requirements.txt")
    except EOFError:
        print("\nKept the current certificate.")
        return
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
    describe(info)


if __name__ == "__main__":
    main()
