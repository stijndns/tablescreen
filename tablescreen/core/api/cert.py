"""
cert.py — The gateway's HTTPS certificate: self-signed, for this PC.

Browsers check that the address they connect to is in the certificate, so
it lists this PC's host name (also as .local), its LAN IPv4 addresses,
localhost and 127.0.0.1. Every device accepts it once; a new certificate
means accepting it again, so it's only renewed when the PC's address
changed or it expires.

``cryptography`` is imported only when a certificate is made or read: the
app runs without it otherwise.
"""

from __future__ import annotations

import datetime
import ipaddress
import os
import socket
from dataclasses import dataclass
from pathlib import Path

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
