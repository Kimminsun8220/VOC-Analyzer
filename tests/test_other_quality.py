import pytest
from src.models import Code, CodebookDraft, CodingBatch
from src.storage import Store
from src.workflow import execute_run, other_quality
from tests.test_workflow import FakeAI, issue, opinion, setup_run, code

OTHER = Code(id='CO', category='기타', name='기타 의견', definition='일반 주제로 묶기 어려운 지엽적 의견', reason='검증')

@pytest.mark.parametrize('count,status,calls', [(1,'completed',0),(2,'completed',1),(3,'completed',1),(4,'completed',1)])
def test_other_thresholds_use_unique_vocs(count,status,calls):
    store=Store();run=setup_run(store,['빠름']*20,[code(),OTHER])
    class AI(FakeAI):
        supplements=0
        def classify(self,records,codes,context,feedback=None):
            return CodingBatch(results=[opinion(r['id'],[issue('CO' if int(r['id'][1:])<=count else 'C1','빠름')]*2) for r in records])
        def supplement(self,*args,frequency_summary=None,**kwargs):
            self.supplements+=1
            assert frequency_summary['other_quality']['response_count']==count
            return CodebookDraft(codes=[])
    ai=AI();execute_run(store,run,ai)
    assert store.run(run)['status']==status
    assert ai.supplements==calls

def test_high_other_is_supplemented_even_without_missing_codes():
    store=Store();run=setup_run(store,['응대 좋아요']*20,[OTHER])
    class AI(FakeAI):
        def classify(self,records,codes,context,feedback=None):
            target=next((c.id for c in codes if c.name=='고객 응대'),'CO')
            return CodingBatch(results=[opinion(r['id'],[issue(target,r['text'])]) for r in records])
        def supplement(self,records,codes,context,candidates,constraints,frequency_summary=None):
            assert frequency_summary['other_quality']['percent']==100
            assert len(candidates)==20
            return CodebookDraft(codes=[dict(category='서비스',name='고객 응대',definition='고객 문의에 대한 응대 품질에 관한 의견',reason='반복 주제 복구',evidence=[dict(voc_id=records[0]['id'],quote=records[0]['text'])])])
    execute_run(store,run,AI())
    current=store.run(run)
    assert current['status']=='completed' and current['round']==1
    assert other_quality(store.effective_results(run),store.codebook(current['codebook_id'])['codes'],20)[1]['percent']==0

def test_failed_responses_retry_in_smaller_batches():
    store=Store();run=setup_run(store,['빠름']*21)
    current=store.run(run)
    for row in store.dataset(current['dataset_id'])['records']:
        store.save_result(run,0,row['id'],error='응답 누락')
    store.update_status(run,"failed","응답 누락")
    ai=FakeAI();execute_run(store,run,ai)
    assert [len(batch) for batch in ai.calls]==[10,10,1]
    assert store.run(run)['status']=='completed'
