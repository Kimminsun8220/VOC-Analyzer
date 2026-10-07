from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import json

import pytest
from google.genai import errors

from src.ai import AIError, GeminiAI, PROMPT_VERSION
from src.evaluation import RequestLedger, e1_context
from src.models import CodebookDraft, CodingBatch, CodingResult


def test_budget_survives_restart_counts_failure_and_prevents_transport(tmp_path):
    path = tmp_path/'ledger.db'
    ledger = RequestLedger(path,limit=2)
    calls=[]
    def fail():
        calls.append(1)
        raise TimeoutError('secret must not be saved')
    with pytest.raises(TimeoutError):
        ledger.wrap(fail,'first')()
    # Reserved-before-crash consumes budget even without a recorded response.
    ledger.reserve('crashed')
    restarted = RequestLedger(path,limit=2)
    with pytest.raises(AIError):
        restarted.wrap(fail,'third')()
    assert len(calls)==1
    assert restarted.summary()['physical_requests']==2
    assert restarted.summary()['requests_without_usage']==2
    assert b'secret' not in path.read_bytes()
    with pytest.raises(ValueError):
        RequestLedger(path,limit=3)


def test_concurrent_reservations_share_cap(tmp_path):
    ledger = RequestLedger(tmp_path/'ledger.db',limit=2)
    def reserve(_):
        try:
            return ledger.reserve('parallel')
        except AIError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(reserve,range(8)))
    assert sum(r is not None for r in results)==2


def test_quota_error_stops_gemini_automatic_retries(tmp_path):
    ledger = RequestLedger(tmp_path/'ledger.db')
    def quota(**kwargs):
        raise errors.APIError(429,{'error':{'code':429,'message':'private'}})
    ai = GeminiAI.__new__(GeminiAI)
    ai.model='fake'
    ai.client=SimpleNamespace(models=SimpleNamespace(generate_content=ledger.wrap(quota,'quota')))
    with pytest.raises(AIError):
        ai.generate('task',{},CodebookDraft)
    assert ledger.summary()['physical_requests']==1


def test_usage_and_context_are_transmitted_without_extra_metadata(tmp_path):
    ledger=RequestLedger(tmp_path/'ledger.db')
    seen=[]
    def transport(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(text='{"codes":[]}',usage_metadata=SimpleNamespace(
            model_dump=lambda **kwargs:{'prompt_token_count':7,'candidates_token_count':3,'thoughts_token_count':2}))
    ai=GeminiAI.__new__(GeminiAI)
    ai.model='fake'
    ai.client=SimpleNamespace(models=SimpleNamespace(generate_content=ledger.wrap(transport,'test')))
    context=e1_context([{'id':'V1','E1':9,'UID':'private-id','gold':'secret-label'}])
    ai.codebook([{'id':'V1','text':'안내해주면 좋겠다'}],context)
    payload=json.loads(seen[0]['contents'].split('분석 데이터 JSON:\n')[1])
    assert payload['context']==context and 'V1: E1=9' in context
    assert 'private-id' not in context and 'secret-label' not in context
    assert ledger.summary()['known_tokens']['thoughts_token_count']==2
    assert ledger.summary()['requests_without_usage']==0


def test_offline_runner_flow_remaps_scores_and_exports_without_production_db(tmp_path,monkeypatch):
    from scripts import evaluate_e1 as runner
    from src.storage import Store
    monkeypatch.setattr(runner,'OUT',tmp_path)
    contexts=[]
    class FakeAI:
        from tests.test_workflow import FakeAI as BaseAI
        review_codebook = BaseAI.review_codebook
        model='fake'
        def codebook(self,records,context):
            contexts.append(context)
            return CodebookDraft(codes=[])
        def classify(self,records,codes,context,**kwargs):
            return CodingBatch(results=[CodingResult(voc_id=r['id'],response_type='no_content',
                no_content_reason='의견 없음') for r in records])
        def close(self):
            pass
    monkeypatch.setattr(runner,'service',lambda *args:FakeAI())
    fixture={'records':[{'id':'V0344','text':'없음','E1':9}], 'core_ids':['V0344']}
    ledger=RequestLedger(tmp_path/'requests.db')
    runner.flow(fixture,ledger)
    state=runner.read(tmp_path/'flow-state.json')
    assert state['id_mapping']=={'V0001':'V0344'}
    assert 'V0001: E1=9' in contexts[0] and 'V0344: E1=9' not in contexts[0]
    assert Store(tmp_path/'experiment.db').run(state['run'])['status']=='completed'
    assert (tmp_path/'flow-results.xlsx').exists()
    runner.flow(fixture,ledger)
    assert len(contexts)==1 and ledger.summary()['physical_requests']==0
