"""Regression checks for autonomous selection, continuation and durable outcomes."""
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
from tools import autonomous_commerce_brain as brain
from tools import cyclops_asset_engine as cyclops
from tools import persist_run_receipt as receipts
from tools import research_memory


class OptimizationTests(unittest.TestCase):
    def test_provider_rate_limit_carries_between_research_runs(self):
        from tools import research_memory
        from datetime import datetime, timezone, timedelta
        memory=research_memory.ResearchMemory.__new__(research_memory.ResearchMemory)
        memory.state={"lane":"wolverine"}
        self.assertTrue(memory.provider_available("brave"))
        memory.defer_provider("brave",minutes=90)
        self.assertFalse(memory.provider_available("brave"))
        merged=research_memory._merge({},memory.state,"wolverine")
        self.assertIn("brave",merged["provider_cooldowns"])
        memory.state=merged
        self.assertFalse(memory.provider_available("brave"))
        memory.state["provider_cooldowns"]["brave"]=(
            datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        self.assertTrue(memory.provider_available("brave"))

    def test_cyclops_rechecks_fabricated_candidate_id_once(self):
        rows=[{"id":"candidate", "title":"Business data report",
               "source_url":"https://example.org/report",
               "page_fetched":True,
               "page_text":"A real user describes difficulty updating routine reports. "*4}]
        plan={"evidence_candidate_id":"candidate","title":"Reporting helper",
              "description":"Original structured report","audience":"Small business",
              "pitch":"Simplify repeated work","price_usd":"17",
              "target_terms":["reports"],"output_files":["report.html"],
              "production_goal":"Make a complete report"}
        with patch.object(cyclops,"model_json",side_effect=[
             {"candidate_id":"invented","decision":"pursue","reason":"Potential"},
             {"candidate_id":"candidate","decision":"pursue","reason":"Real problem"},
             plan]) as infer, patch.object(cyclops,"discover_tools",return_value={}):
            result=cyclops.choose_asset_concept(rows)
        self.assertEqual(infer.call_count,3)
        self.assertEqual(result["evidence_candidate_id"],"candidate")

    def test_cyclops_reviews_remaining_market_after_first_pass(self):
        rows=[{'id':str(i),'source_url':f'https://example.org/{i}',
               'title':f'Useful product {i}','page_fetched':True,
               'page_text':'A real original user describes a recurring problem in their daily work. '*3}
              for i in range(2)]
        plan={'evidence_candidate_id':'1','title':'Useful tool','description':'Usable product',
              'audience':'Customers','pitch':'A concrete useful product','price_usd':'12',
              'target_terms':['reporting'],'output_files':['tool.html'],'production_goal':'Produce a functional tool'}
        with patch.object(cyclops,'model_json',side_effect=[
            {'decision':'pass','candidate_id':'0','reason':'Not feasible'},
            {'decision':'pursue','candidate_id':'1','reason':'Feasible differentiated solution'},plan]) as model, \
             patch.object(cyclops,'discover_tools',return_value={}):
            result=cyclops.choose_asset_concept(rows)
        self.assertEqual(result['evidence_candidate_id'],'1')
        self.assertEqual(model.call_args_list[1].args[2]['public_market_observations'][0]['candidate_id'],'1')

    def test_natural_language_request_is_verified_against_fetched_source(self):
        body='Our small team could use outside assistance fixing these reports. Please email your estimate to work@example.org.'
        row={'id':'request','title':'Reporting assistance','source_url':'https://example.org/request',
             'page_fetched':True,'page_text':body,'contact':'work@example.org'}
        with patch.object(brain,'model_json',return_value={'requests':[{
                'candidate_id':'request','invited':True,'quote':body}]}):
            brain.review_requester_pages([row])
        self.assertTrue(brain.public_buyer_invitation(row))
        row['page_text']='An unrelated generic information page with a contact address.'
        self.assertFalse(brain.public_buyer_invitation(row))

    def test_invented_invitation_quote_cannot_authorize_outreach(self):
        row={'id':'generic','title':'Company information','page_fetched':True,
             'contact':'info@example.org','page_text':'General company background, useful products and services available for purchase. '*3}
        with patch.object(brain,'model_json',return_value={'requests':[{
                'candidate_id':'generic','invited':True,'quote':'We invite you to send your bids and proposals.'}]}):
            brain.review_requester_pages([row])
        self.assertFalse(brain.public_buyer_invitation(row))

    def test_completed_file_continues_in_same_execution(self):
        plan={'title':'Finished multi-file product'}
        with patch.object(cyclops,'create_asset',side_effect=[
            cyclops.ProductionPending('artifact_created_and_saved:one.html'),plan]) as create:
            self.assertEqual(cyclops.complete_asset([],[]),plan)
        self.assertEqual(create.call_count,2)

    def test_actual_build_failure_preserves_pending_retry(self):
        with patch.object(cyclops,'create_asset',side_effect=cyclops.ProductionPending('build_failed')):
            with self.assertRaises(cyclops.ProductionPending):
                cyclops.complete_asset([],[])

    def test_receipt_race_retries_and_verifies_ambiguous_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'receipt.json'; path.write_text('{"earned":0}')
            missing=urllib.error.HTTPError('url',404,'missing',{},io.BytesIO())
            conflict=urllib.error.HTTPError('url',409,'concurrent branch write',{},io.BytesIO())
            existing=io.BytesIO(json.dumps({'content':base64.b64encode(path.read_bytes()).decode()}).encode())
            with patch.dict(os.environ,{'GITHUB_REPOSITORY':'owner/repo','GH_TOKEN':'dummy'}), \
                 patch.object(receipts,'time'):
                from unittest.mock import Mock
                opener=Mock(side_effect=[missing,conflict,existing]); sleep=Mock()
                receipts.persist(str(path),'receipt',opener=opener,sleep=sleep)
                self.assertEqual(opener.call_count,3)
                sleep.assert_called_once()

    def test_planner_receives_commercial_outcomes_without_internal_errors(self):
        memory=research_memory.ResearchMemory.__new__(research_memory.ResearchMemory)
        memory.state={}
        memory.note_outcome({'production':{'reason':'Observed customers need smaller packages'},
                             'commercial_outcomes':[{'reason':'No usable reply route'}],
                             'structural_failures':[{'reason':'internal_error_123'}]})
        context=memory.plan_context()
        self.assertIn('No usable reply route',str(context))
        self.assertIn('smaller packages',str(context))
        self.assertNotIn('internal_error_123',str(context))
