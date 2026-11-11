"""Load a scoped BountyBoard key without publishing plaintext credentials."""
import base64
import hashlib
import json
import os
from pathlib import Path
from cryptography.fernet import Fernet

CREDENTIAL_PATH = Path('ops/gambit/bountyboard-credential.json')
AGENT_ID = 'cmwcob1sx05ew11s1ul2mphy7'


def cipher(secret):
    if not secret:
        raise ValueError('credential_root_missing')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(
        b'ari-bountyboard-credential-v1:' + secret.encode()).digest()))


def load():
    direct = os.getenv('BOUNTYBOARD_AGENT_API_KEY', '').strip()
    if direct:
        return direct
    if not CREDENTIAL_PATH.exists():
        return None
    record = json.loads(CREDENTIAL_PATH.read_text())
    if record.get('agent_id') != AGENT_ID or record.get('version') != 1:
        raise ValueError('credential_identity_mismatch')
    return cipher(os.getenv('BOUNTY_AGENT_API_KEY', '')).decrypt(
        record['encrypted_key'].encode()).decode()
