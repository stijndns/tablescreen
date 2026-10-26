"""
make_cert.py — Spike: a self-signed certificate for this PC's LAN address.

Browsers require the address or name they connect to in the certificate's
subject alternative names, so it lists this PC's host name, its LAN IPv4
addresses, localhost and 127.0.0.1. Extra addresses can be given:

    python make_cert.py                 # writes cert.pem and key.pem here
    python make_cert.py 192.168.1.77    # also include this address
"""

import datetime
import ipaddress
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

HERE = Path(__file__).resolve().parent
VALID_DAYS = 825        # the longest some platforms accept for a certificate


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


def main() -> None:
    host = socket.gethostname()
    addresses = sorted(lan_addresses() | set(sys.argv[1:]))
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
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .sign(key, hashes.SHA256()))

    (HERE / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (HERE / "key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    print(f"Wrote cert.pem and key.pem for {host} ({', '.join(addresses) or 'no LAN address found'}),"
          f" valid {VALID_DAYS} days.")


if __name__ == "__main__":
    main()
