"""Prepare offline by default. --execute flow/compare explicitly calls Gemini.

Private fixtures, database, responses and request ledger stay under .local.
This experiment never modifies the production database or approves its codebook.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.ai import GeminiAI, DEFAULT_MODEL, PROMPT_VERSION
from src.config import load_gemini_key
from src.evaluation import RequestLedger, e1_context
from src.models import Code, validate_result

BASE = ROOT / '.local/e1-batch-benchmark-20261006'
OUT = ROOT / '.local/e1-ready-20261007'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def prepare():
    sample = read(BASE/'sample.json')
    context = read(BASE/'question-context.json')
    review = read(BASE/'semantic-review.json')
    original = {r['id']:r for r in sample['records']}
    ids = list(dict.fromkeys(i for f in review['findings'] for i in f['voc_ids']))
    saved = read(BASE/'diagnostic-batch-50.json')['results']
    # Include no-content and long/multi-opinion examples, not only flagged failures.
    for result in saved:
        if result['response_type']=='no_content' and result['voc_id'] not in ids:
            ids.append(result['voc_id'])
            break
    for r in sorted(original.values(), key=lambda r:len(r['text']), reverse=True):
        if r['id'] not in ids:
            ids.append(r['id'])
        if len(ids)==20:
            break
    core = ids[:20]
    ids += [i for i in original if i not in ids]
    records = [{**original[i], 'E1':context['source_scores_attached_for_review_only'][i]} for i in ids[:50]]
    fixture = {'source':sample['source'], 'core_ids':core, 'records':records,
        'model':DEFAULT_MODEL, 'prompt_version':PROMPT_VERSION,
        'context':e1_context(records), 'review_findings':review['findings'],
        'human_gold_standard':False, 'approved_cases':read(BASE/'expected-review-cases.json')}
    assert len(core)==20 and len(records)==50
    target = OUT/'fixture.json'
    if target.exists() and read(target) != fixture:
        raise ValueError('Existing experiment fixture differs; preserve it and review before starting another experiment')
    save(target, fixture)
    (OUT/'분석배경.txt').write_text(fixture['context'], encoding='utf-8-sig')
    return fixture


def service(ledger, stage):
    ai = GeminiAI(load_gemini_key())
    ai.client.models.generate_content = ledger.wrap(ai.client.models.generate_content, stage)
    return ai


def flow(fixture, ledger):
    import pandas as pd
    from src.ingestion import prepare_preview
    from src.storage import Store
    from src.workflow import generate_codebook, execute_run
    from src.results import result_tables
    from src.result_downloads import classification_frame, statistics_frames, xlsx_download
    store = Store(OUT/'experiment.db')
    state_path = OUT/'flow-state.json'
    state = read(state_path) if state_path.exists() else {}
    rows = [r for r in fixture['records'] if r['id'] in fixture['core_ids']]
    remapped = [{**r,'source_id':r['id'],'id':f'V{n:04d}'} for n,r in enumerate(rows,1)]
    context = e1_context(remapped)
    if 'dataset' not in state:
        frame = pd.DataFrame({'VOC':[r['text'] for r in rows], 'source_id':[r['id'] for r in rows]})
        state['dataset'] = store.save_dataset('E1 isolated experiment',prepare_preview(frame,'VOC'),frame,'VOC','evaluation',context)
        state['id_mapping'] = {r['id']:r['source_id'] for r in remapped}
        save(state_path,state)
    ai = service(ledger,'flow20')
    try:
        if 'book' not in state:
            draft_id = generate_codebook(store,state['dataset'],ai,context)
            draft = store.codebook(draft_id)
            # Test-only executable copy. This is not a human approval.
            state['book'] = store.save_codebook(state['dataset'],draft['codes'],context,ai.model,
                draft['sample_ids'],'confirmed',changes=[{'kind':'experiment_only_not_human_approved'}])
            save(state_path,state)
        if 'run' not in state:
            state['run'] = store.create_run(state['dataset'],state['book'],ai.model,PROMPT_VERSION)
            save(state_path,state)
        execute_run(store,state['run'],ai)
        run = store.run(state['run'])
        save(OUT/'flow-results.json',{'run':run,'results':store.effective_results(state['run']),
            'id_mapping':state['id_mapping'],'human_approved_codebook':False,'usage':ledger.summary()})
        originals, issues = result_tables(store,state['run'])
        codes = store.codebook(run['codebook_id'])['codes']
        (OUT/'flow-results.xlsx').write_bytes(xlsx_download({'VOC별 분류':classification_frame(originals,issues),
            **statistics_frames(originals,issues,codes)}))
    finally:
        ai.close()


def compare(fixture, ledger):
    # Same 50 rows, fixed draft, context, model, timeout/output settings for both sizes.
    codes = [Code.model_validate(c) for c in read(BASE/'codebook.json')['codes']]
    for size in (50,10):
        path = OUT/f'compare-{size}.json'
        report = read(path) if path.exists() else {'size':size,'batches':[], 'human_approved_codebook':False}
        ai = service(ledger,f'compare{size}')
        try:
            for offset in range(0,50,size):
                if any(b['offset']==offset for b in report['batches']):
                    continue
                rows = fixture['records'][offset:offset+size]
                response = ai.classify([{'id':r['id'],'text':r['text']} for r in rows],codes,fixture['context'])
                counts = Counter(r.voc_id for r in response.results)
                if counts != Counter(r['id'] for r in rows):
                    raise ValueError('Missing, duplicated or unexpected response IDs')
                actual = {r.voc_id:r for r in response.results}
                for row in rows:
                    validate_result(actual[row['id']],row,codes,fixture['context'])
                report['batches'].append({'offset':offset,'results':[r.model_dump() for r in response.results]})
                save(path,report)
        finally:
            ai.close()


def review_outputs(fixture):
    outputs={}
    flow_path=OUT/'flow-results.json'
    if flow_path.exists():
        data=read(flow_path)
        outputs['flow20']={data['id_mapping'][r['voc_id']]:r['result']
            for r in data['results'] if r['result']}
    for size in (10,50):
        path=OUT/f'compare-{size}.json'
        if path.exists():
            outputs[f'compare{size}']={r['voc_id']:r for b in read(path)['batches'] for r in b['results']}
    checks=[]
    for stage,results in outputs.items():
        for case in fixture['approved_cases']['cases']:
            actual=results.get(case['voc_id'])
            # Exact evidence matching is a triage aid, not a semantic judge.
            expected=case['expected_issues']
            matches=actual is not None and all(any(
                issue['sentiment']==wanted['sentiment'] and wanted['evidence_text'] in issue['evidence_text']
                for issue in actual['issues']) for wanted in expected)
            checks.append({'stage':stage,'source_id':case['voc_id'],'expected_issues':expected,
                'actual':actual,'status':'evidence_and_sentiment_match' if matches else 'needs_review'})
    a,b=outputs.get('compare10',{}),outputs.get('compare50',{})
    def signature(row):
        return row['response_type'],sorted((i['code_id'] or '',i['sentiment'],i['subject_label'] or '') for i in row['issues'])
    common=sorted(a.keys() & b.keys())
    save(OUT/'review-results.json',{'approved_case_checks':checks,
        'comparison_common_count':len(common),'comparison_different_ids':[i for i in common if signature(a[i])!=signature(b[i])],
        'review_checklist':fixture['review_findings'],
        'limitations':['Exact evidence matching may flag valid alternative quotes for review.',
            'Agreement between batch sizes is not accuracy. No human-labelled overall accuracy is claimed.',
            'Request time includes transport and retries; this is not total end-to-end user time.']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',choices=['flow','compare'])
    args = parser.parse_args()
    fixture = prepare()
    if args.execute:
        # Guard against changed source data. No scheduled/background calls.
        source = Path(fixture['source']['path'])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != '7bee5381ea6dc3d4337b0d951e60e08e80dd7f33d63253f6078c1001ea16636d':
            raise ValueError('Source workbook changed')
        ledger = RequestLedger(OUT/'requests.db')
        try:
            {'flow':flow,'compare':compare}[args.execute](fixture,ledger)
        finally:
            save(OUT/'usage.json',ledger.summary())
            review_outputs(fixture)
    print(json.dumps({'prepared_core':20,'comparison_rows':50,'executed':args.execute,
        'output':str(OUT)},ensure_ascii=False))


if __name__=='__main__':
    main()
