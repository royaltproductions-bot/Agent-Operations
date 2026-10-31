"""Regression tests for checkpointed, source-verified large research lists."""
import os
import unittest
from unittest.mock import patch
from tools import gambit_research_workflow as research


class ResearchWorkflowTests(unittest.TestCase):
    def test_500_records_are_not_blocked_by_artificial_threshold(self):
        task={"bounty":{"title":"Identify 500 CFO candidates",
                        "verification":"Submit a CSV of 500 sourced profiles"}}
        for size in (15,50,500):
            self.assertTrue(research.qualifies(task,size,{"csv"}))
        self.assertFalse(research.qualifies(task,10,{"csv"}))
        self.assertFalse(research.qualifies(task,500,{"pdf"}))

    def test_provenance_column_is_always_last(self):
        self.assertEqual(
            research._clean_columns(["source_url","Name","LinkedIn Profile URL"]),
            ["Name","LinkedIn Profile URL","source_url"])

    def test_rejects_fabricated_profile_not_in_actual_page(self):
        url="https://example.org/people"
        body="Asha Example is chief finance officer. View profile at https://linkedin.com/in/asha-example."
        source={"url":url,"evidence_level":"page_read","page_text":body}
        columns=["Name","LinkedIn Profile URL","source_url"]
        good={"cells":["Asha Example","https://linkedin.com/in/asha-example"],
              "evidence_quote":body,"source_url":url}
        self.assertIsNotNone(research._verified(good,source,columns))
        bad={**good,"cells":["Asha Example","https://linkedin.com/in/fabricated"]}
        self.assertIsNone(research._verified(bad,source,columns))
        no_read={**source,"evidence_level":"search_snippet"}
        self.assertIsNone(research._verified(good,no_read,columns))
        self.assertIsNone(research._verified({**good,"evidence_quote":"invented evidence"},
                                             source,columns))

    def test_sourced_rows_continue_across_cycles_without_plaintext_state(self):
        details={"bounty":{"title":"Find 15 executive contacts",
                           "verification":"Submit 15 sourced records in CSV"}}
        url="https://example.org/team"
        quote="Asha Example is the director and the company profile is available publicly."
        plan={"columns":["Name","Title"],"queries":["site executive team contacts",
                                                   "industry finance directors"]}
        row={"rows":[{"cells":["Asha Example","Director"],
                      "source_url":url,"evidence_quote":quote}]}
        evidence={"status":"ok","results":[{"url":url,"page_text":quote,
                                             "evidence_level":"page_read"}]}
        state={}
        answers=[plan,row]
        with patch.dict(os.environ,{"BOUNTY_AGENT_API_KEY":"test-only-key"}):
            def infer(*args,**kwargs):
                return answers.pop(0)
            def advance():
                return research.progress(details,state,"b1","claim1",15,infer,
                                         lambda queries:evidence)
            first=advance()
            self.assertEqual(first["stage"],"research_plan")
            second=advance()
            self.assertEqual(second["completed"],1)
            self.assertEqual(second["total"],15)
            encrypted=state["research_projects"]["b1"]
            self.assertNotIn("Asha Example",encrypted)
            decoded=research._unseal(encrypted)
            self.assertEqual(len(decoded["rows"]),1)
            research.request_revision(state,"b1","Source column failed review")
            decoded=research._unseal(state["research_projects"]["b1"])
            self.assertEqual(len(decoded["rows"]),0)
            self.assertIn("Source column failed",decoded["feedback"])

    def test_full_batch_exports_one_row_per_verified_record(self):
        url="https://example.org/a"
        project={"claim_id":"claim","columns":["Name","Title","source_url"],
                 "queries":["executive research sources"],"searched":[],
                 "rows":[{"cells":[f"Person {i}","Director"],
                          "source_url":url,"evidence_quote":f"Person {i} - Director"}
                         for i in range(15)],"rounds":3,"feedback":""}
        with patch.dict(os.environ,{"BOUNTY_AGENT_API_KEY":"test-only-key"}):
            state={"research_projects":{"b1":research._seal(project)}}
            result=research.progress({"bounty":{
                "title":"List 15 professionals","verification":"Submit a CSV"}},state,
                "b1","claim",15,lambda *args,**kwargs:None,
                lambda queries:None)
        self.assertEqual(result["status"],"ready")
        self.assertEqual(result["deliverable_format"],"csv")
        self.assertEqual(len(result["deliverable_text"].strip().splitlines()),16)
        self.assertEqual(len(result["_web_evidence"]["results"]),15)


if __name__=="__main__":
    unittest.main()
