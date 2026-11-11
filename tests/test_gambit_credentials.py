import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import InvalidToken
from tools import gambit_credentials as creds

class CredentialsTests(unittest.TestCase):
    def test_encrypted_roundtrip_and_wrong_root_rejection(self):
        encrypted=creds.cipher('root-test').encrypt(b'bb_ag_test').decode()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'credential.json'
            path.write_text(json.dumps({'version':1,'agent_id':creds.AGENT_ID,'encrypted_key':encrypted}))
            with patch.object(creds,'CREDENTIAL_PATH',path),patch.dict('os.environ',{'BOUNTY_AGENT_API_KEY':'root-test','BOUNTYBOARD_AGENT_API_KEY':''}):
                self.assertEqual(creds.load(),'bb_ag_test')
            with patch.object(creds,'CREDENTIAL_PATH',path),patch.dict('os.environ',{'BOUNTY_AGENT_API_KEY':'wrong','BOUNTYBOARD_AGENT_API_KEY':''}):
                with self.assertRaises(InvalidToken):creds.load()
        self.assertNotIn('bb_ag_test',encrypted)

    def test_wrong_identity_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'credential.json'
            path.write_text(json.dumps({'version':1,'agent_id':'other'}))
            with patch.object(creds,'CREDENTIAL_PATH',path),patch.dict('os.environ',{'BOUNTYBOARD_AGENT_API_KEY':''}):
                with self.assertRaisesRegex(ValueError,'identity'):creds.load()
