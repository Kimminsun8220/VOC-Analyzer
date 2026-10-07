"""Explicit evaluation helpers; no network activity on import or construction."""

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from src.ai import AIError


class RequestLedger:
    """Reserve before transport, including failures and interrupted requests.

    One persistent ledger is shared by all stages/restarts of an experiment.
    This is an experiment cap, not a measurement of account-wide quota.
    """

    def __init__(self, path, limit=20):
        if not 1 <= limit <= 20:
            raise ValueError("Evaluation request limit must be between 1 and 20")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY, cap INTEGER)')
            db.execute('INSERT OR IGNORE INTO budget VALUES (1, ?)', (limit,))
            if db.execute('SELECT cap FROM budget WHERE id=1').fetchone()[0] != limit:
                raise ValueError("Existing evaluation budget cannot be changed")
            db.execute('''CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY, stage TEXT, started TEXT, status TEXT,
                seconds REAL, usage TEXT, response TEXT, error_code INTEGER)''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, stage):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            used = db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
            cap = db.execute('SELECT cap FROM budget WHERE id=1').fetchone()[0]
            if used >= cap:
                raise AIError("시험 요청 상한에 도달했습니다. 저장된 결과를 확인해주세요.")
            return db.execute('INSERT INTO calls(stage,started,status) VALUES (?,?,?)',
                (stage, datetime.now(timezone.utc).isoformat(), 'reserved')).lastrowid

    def wrap(self, transport, stage):
        def measured(*args, **kwargs):
            call_id = self.reserve(stage)
            start = time.perf_counter()
            status, usage, body, code = 'interrupted', None, None, None
            try:
                response = transport(*args, **kwargs)
                status = 'received'
                metadata = getattr(response, 'usage_metadata', None)
                usage = metadata.model_dump(mode='json') if metadata else None
                body = response.text
                return response
            except Exception as exc:
                status, code = 'failed', getattr(exc, 'code', None)
                # Do not persist exception messages/URLs/keys. Stop on quota errors.
                if code == 429:
                    raise AIError("시험 중 API 한도 응답을 받아 중단했습니다.") from None
                raise
            finally:
                with self.connect() as db:
                    db.execute('''UPDATE calls SET status=?,seconds=?,usage=?,response=?,error_code=?
                                  WHERE id=?''', (status, time.perf_counter()-start,
                        json.dumps(usage) if usage is not None else None, body, code, call_id))
        return measured

    def summary(self):
        with self.connect() as db:
            rows = db.execute('SELECT stage,status,seconds,usage FROM calls').fetchall()
        return {'physical_requests':len(rows), 'received_responses':sum(r[1]=='received' for r in rows),
            'request_seconds':sum(r[2] or 0 for r in rows),
            'by_stage':{stage:{'requests':sum(r[0]==stage for r in rows),
                'request_seconds':sum(r[2] or 0 for r in rows if r[0]==stage)}
                for stage in sorted({r[0] for r in rows})},
            'requests_without_usage':sum(r[3] is None for r in rows),
            'known_tokens':{field:sum((json.loads(r[3]) or {}).get(field,0) or 0 for r in rows if r[3])
                for field in ('prompt_token_count','candidates_token_count','thoughts_token_count','total_token_count')},
            'actual_billed_cost':None}


def e1_context(records):
    """Only explicitly selected E1 scores enter context; no arbitrary metadata."""
    return ('공통 질문: E1 문항에 점수를 그렇게 준 이유. E1은 높을수록 좋은 평가다. '
        '점수별 감성 경계는 정하지 않았다. 현재 정기 안내·관리의 부족을 뜻하는 개선 요구는 부정이다. '
        '예: 불만은 없지만 정기적으로 안내해주면 좋겠다는 긍정 평가와 안내 부족 부정을 각각 보존한다. '
        '모든 희망을 부정으로 보지 않는다. 높은 점수로 개별 부정을 덮어쓰거나 세부 의미를 추측하지 않는다.\n'
        + '\n'.join(f"{r['id']}: E1={r['E1']}" for r in records))
