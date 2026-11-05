"""Create a signing key once, or sign the exact installer and portable artifacts."""
import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from amazing_overlay import __version__
from amazing_overlay.updates import REPOSITORY, verify_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-key", type=Path, help="Private key file; keep outside Git")
    parser.add_argument("--directory", type=Path, default=ROOT / ".cache/dist")
    args = parser.parse_args()
    public_path = ROOT / "amazing_overlay/update_public_key.txt"
    if args.generate_key:
        key = Ed25519PrivateKey.generate()
        # Exclusive creation prevents accidentally replacing a deployed trust root.
        if public_path.exists():
            raise ValueError("Public signing key already exists; rotation must be explicit")
        args.generate_key.parent.mkdir(parents=True, exist_ok=True)
        with args.generate_key.open("xb") as stream:
            stream.write(base64.b64encode(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())) + b"\n")
        public_path.write_bytes(base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)) + b"\n")
        print("Signing key created. Private material was not printed.")
        return
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ["UPDATE_SIGNING_KEY"].strip(), validate=True))
    public = base64.b64decode(public_path.read_bytes().strip(), validate=True)
    if key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) != public:
        raise ValueError("Release signing key does not match the application's public key")
    packages = []
    for kind, name in (("installer", "AmazingHelperSetup.exe"), ("portable", f"AmazingHelper-{__version__}-portable-x64.zip")):
        path = args.directory / name
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        packages.append({"type": kind, "name": name, "arch": "x64", "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    payload = (json.dumps({"format": 1, "repository": REPOSITORY, "version": __version__, "packages": packages}, indent=2) + "\n").encode()
    signature = key.sign(payload)
    verify_manifest(payload, signature, public)
    (args.directory / "updates.json").write_bytes(payload)
    (args.directory / "updates.sig").write_bytes(signature)
    (args.directory / "SHA256SUMS.txt").write_text("".join(f"{p['sha256']}  {p['name']}\n" for p in packages), encoding="ascii")
    print("Release artifacts signed and verified.")


if __name__ == "__main__":
    main()
