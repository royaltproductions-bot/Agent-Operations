"""One-time encrypted export of a previously captured bounty for its operator.

Never write buyer terms to public GitHub Actions logs or unencrypted artifacts.
The recipient holds the private RSA key; this job receives only its public key.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tools.gambit_worker import load_state, saved_brief


def main():
    bounty_id = os.environ.get("GAMBIT_EXPORT_BOUNTY_ID", "").strip()
    if not bounty_id:
        raise RuntimeError("missing_archive_export_id")
    state = load_state()
    brief = saved_brief(state, bounty_id)
    if not brief:
        raise RuntimeError("archive_brief_unavailable")
    public_pem = Path("assets/gambit-report-public.pem").read_bytes()
    public_key = serialization.load_pem_public_key(public_pem)
    symmetric_key = os.urandom(32)
    nonce = os.urandom(12)
    plaintext = json.dumps({
        "bounty_id": bounty_id,
        "brief": brief,
        "final_status": (state.get("bounties", {}).get(bounty_id) or {}).get("platform_status"),
    }, ensure_ascii=False).encode()
    sealed = public_key.encrypt(symmetric_key, padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(), label=None))
    ciphertext = AESGCM(symmetric_key).encrypt(nonce, plaintext, None)
    report = {"format": "gambit-archive-v1",
              "sealed_key": base64.b64encode(sealed).decode(),
              "nonce": base64.b64encode(nonce).decode(),
              "ciphertext": base64.b64encode(ciphertext).decode()}
    destination = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "gambit-archive-report.json"
    destination.write_text(json.dumps(report), encoding="utf-8")
    print("Encrypted bounty report prepared for operator; no buyer text logged.")


if __name__ == "__main__":
    main()
