import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import ai_handoff_executor as h

GOOD={
 "schema_version":1,
 "id":"session-trigger-check-01",
 "action":"publish_organic_documentary",
 "approved_for_automatic_publication":True,
 "authored_by":"scheduled_chatgpt",
 "evidence_url":"https://github.com/royaltproductions-bot/Agent-Operations/actions/runs/39008422751",
 "heading":"SYSTEM CHECK",
 "subtitle":"Independent execution, real provider responses",
 "line_one":"GITHUB CLOCK: VERIFIED",
 "line_two":"PAID ORDERS: ZERO",
 "punchline":"MY CLOCK WORKS. MY WALLET IS WAITING.",
 "caption":"I just verified a real GitHub schedule triggered a live payment status and inbox check independently. Both services responded. No paying customers appeared in that test. Still searching for actual demand. #AIExperiment #BuildInPublic"
}

class HandoffTests(unittest.TestCase):
    def test_valid_evidence_bound_chatgpt_handoff(self):
        self.assertEqual(h.validate(GOOD)["id"],GOOD["id"])
        self.assertIn('data-auto-publish="true"',h.make_svg(GOOD))
        self.assertIn("GITHUB CLOCK",h.make_svg(GOOD))

    def test_no_public_operator_or_private_data(self):
        for change in [
           {"caption":"My human told me to change this approach."},
           {"caption":"Contact me at private.person@example.com for more"},
           {"heading":"BUY NOW"},
           {"punchline":"I earned $100 today!"},
           {"evidence_url":"https://example.org/fake-proof"},
           {"approved_for_automatic_publication":False},
           {"action":"send_unsolicited_email"},
           {"id":"../../escape"},
        ]:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    h.validate({**GOOD,**change})

    def test_generated_xml_is_escaped_not_code(self):
        obj={**GOOD,"punchline":"MY <NEW> TEST & CHECK WORKED", "id":"session-trigger-check-02"}
        h.validate(obj)
        svg=h.make_svg(obj)
        self.assertIn("MY &lt;NEW&gt; TEST &amp; CHECK",svg)
        self.assertNotIn("MY <NEW>",svg)

    def test_handoff_created_once_and_has_provenance(self):
        with TemporaryDirectory() as folder:
            base=Path(folder)
            queue,creative,results=(base/d for d in ("queue","creative","results"))
            queue.mkdir()
            (queue/(GOOD["id"]+".json")).write_text(json.dumps(GOOD))
            ready=base/"ready.txt"
            with patch.object(h,"READY",ready):
                first=h.process(queue,creative,results)
                self.assertEqual(len(first),1)
                self.assertEqual(ready.read_text().strip(),"yes")
                self.assertTrue((creative/("ai-commerce-"+GOOD["id"]+".svg")).exists())
                receipt=json.loads((results/(GOOD["id"]+".json")).read_text())
                self.assertTrue(receipt["handoff_received"])
                self.assertFalse(receipt["buffer_submitted"])
                second=h.process(queue,creative,results)
                self.assertEqual(second,[])
                self.assertEqual(ready.read_text().strip(),"no")

if __name__=="__main__":
    unittest.main()
