"""SQLite 입력·분류 기준표·AI 스냅샷, 별도 사용자 수정 이력, 조회 묶음을 보존한다."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from src.models import Code, validate_codes

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "voc_analyzer.db"


def dump(value):
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, path=None):
        self.path = Path(path or DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
                    input_type TEXT NOT NULL, text_column TEXT NOT NULL,
                    records_json TEXT NOT NULL, context TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS codebooks (
                    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    version INTEGER NOT NULL, status TEXT NOT NULL, codes_json TEXT NOT NULL,
                    context TEXT NOT NULL, model TEXT NOT NULL, sample_ids_json TEXT NOT NULL,
                    parent_id TEXT REFERENCES codebooks(id), changes_json TEXT NOT NULL,
                    constraints_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(dataset_id, version)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    codebook_id TEXT NOT NULL REFERENCES codebooks(id),
                    model TEXT NOT NULL, prompt_version TEXT NOT NULL,
                    status TEXT NOT NULL, round INTEGER NOT NULL DEFAULT 0,
                    round_limit INTEGER NOT NULL DEFAULT 2, context TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS results (
                    run_id TEXT NOT NULL REFERENCES runs(id), round INTEGER NOT NULL,
                    voc_id TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT,
                    error TEXT NOT NULL DEFAULT '', PRIMARY KEY(run_id, round, voc_id)
                );
                CREATE TABLE IF NOT EXISTS result_groups (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    codebook_id TEXT NOT NULL REFERENCES codebooks(id),
                    name TEXT NOT NULL, code_ids_json TEXT NOT NULL,
                    sentiment TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(run_id, name)
                );
                CREATE TABLE IF NOT EXISTS corrections (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    round INTEGER NOT NULL, voc_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    codebook_id TEXT NOT NULL REFERENCES codebooks(id), kind TEXT NOT NULL,
                    status TEXT NOT NULL, result_json TEXT, edits_json TEXT NOT NULL,
                    previous_json TEXT, reason TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(run_id, revision)
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            for name, definition in {
                "parent_run_id": "TEXT REFERENCES runs(id)", "parent_result_revision": "INTEGER",
                "result_revision": "INTEGER NOT NULL DEFAULT 0",
                "inheritance_json": "TEXT NOT NULL DEFAULT '[]'",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def list_datasets(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,name,created_at FROM datasets ORDER BY created_at DESC")]

    def save_dataset(self, name, preview, frame, text_column, input_type, context):
        records = []
        for index, row in enumerate(preview.records.to_dict("records")):
            records.append({
                "id": row["VOC ID"], "row": row["입력 행"], "text": row["VOC 원문"],
                "metadata": {str(k): str(v) for k, v in frame.iloc[index].fillna("").items() if k != text_column},
            })
        identifier = uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO datasets VALUES (?,?,?,?,?,?,?)", (
                identifier, name.strip() or "새 VOC 분석", datetime.now(timezone.utc).isoformat(),
                input_type, str(text_column), dump(records), context.strip(),
            ))
        return identifier

    def dataset(self, identifier):
        with self.connect() as db:
            saved = db.execute("SELECT * FROM datasets WHERE id=?", (identifier,)).fetchone()
            if saved is None:
                raise ValueError("입력 자료를 찾을 수 없습니다. 목록을 새로고침해주세요.")
            row = dict(saved)
        row["records"] = json.loads(row.pop("records_json"))
        return row

    def dataset_summary(self, identifier):
        with self.connect() as db:
            row = db.execute("""
                SELECT d.id, d.name, d.created_at, d.input_type, d.records_json,
                    (SELECT COUNT(*) FROM codebooks WHERE dataset_id=d.id) AS codebook_count,
                    (SELECT COUNT(*) FROM runs WHERE dataset_id=d.id) AS run_count,
                    (SELECT COUNT(*) FROM corrections c JOIN runs r ON r.id=c.run_id
                        WHERE r.dataset_id=d.id) AS correction_count,
                    (SELECT COUNT(*) FROM result_groups g JOIN runs r ON r.id=g.run_id
                        WHERE r.dataset_id=d.id) AS group_count,
                    EXISTS(SELECT 1 FROM runs WHERE dataset_id=d.id AND lease_until>?) AS analysis_running
                FROM datasets d WHERE d.id=?
            """, (time.time(), identifier)).fetchone()
            if row is None:
                raise ValueError("입력 자료를 찾을 수 없습니다. 목록을 새로고침해주세요.")
        summary = dict(row)
        summary["record_count"] = len(json.loads(summary.pop("records_json")))
        return summary

    def rename_dataset(self, identifier, name):
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("자료 이름을 1~100자로 입력해주세요.")
        with self.connect() as db:
            if not db.execute("UPDATE datasets SET name=? WHERE id=?", (name, identifier)).rowcount:
                raise ValueError("입력 자료를 찾을 수 없습니다. 목록을 새로고침해주세요.")

    def delete_dataset(self, identifier):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM datasets WHERE id=?", (identifier,)).fetchone():
                raise ValueError("입력 자료를 찾을 수 없습니다. 목록을 새로고침해주세요.")
            if db.execute("SELECT 1 FROM runs WHERE dataset_id=? AND lease_until>?",
                          (identifier, time.time())).fetchone():
                raise ValueError("분류가 진행 중인 자료는 삭제할 수 없습니다. 완료 후 다시 시도해주세요.")
            for table in ("corrections", "result_groups", "results"):
                db.execute(f"DELETE FROM {table} WHERE run_id IN (SELECT id FROM runs WHERE dataset_id=?)", (identifier,))
            db.execute("DELETE FROM runs WHERE dataset_id=?", (identifier,))
            db.execute("DELETE FROM codebooks WHERE dataset_id=?", (identifier,))
            db.execute("DELETE FROM datasets WHERE id=?", (identifier,))

    def _save_codebook(self, db, dataset_id, codes, context, model, sample_ids, status, parent_id, changes, constraints):
        validate_codes(codes)
        identifier = uuid4().hex
        version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM codebooks WHERE dataset_id=?", (dataset_id,)).fetchone()[0]
        db.execute("INSERT INTO codebooks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            identifier, dataset_id, version, status, dump([c.model_dump() for c in codes]), context, model,
            dump(sample_ids), parent_id, dump(changes), dump(constraints), datetime.now(timezone.utc).isoformat(),
        ))
        return identifier

    def save_codebook(self, dataset_id, codes, context, model, sample_ids, status="draft", parent_id=None, changes=None, constraints=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._save_codebook(db, dataset_id, codes, context, model, sample_ids, status, parent_id, changes or [], constraints or [])

    def codebook(self, identifier):
        with self.connect() as db:
            row = dict(db.execute("SELECT * FROM codebooks WHERE id=?", (identifier,)).fetchone())
        row["codes"] = [Code.model_validate(code) for code in json.loads(row.pop("codes_json"))]
        for key in ("sample_ids", "changes", "constraints"):
            row[key] = json.loads(row.pop(key + "_json"))
        return row

    def list_codebooks(self, dataset_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,version,status FROM codebooks WHERE dataset_id=? ORDER BY version DESC", (dataset_id,))]

    def create_run(self, dataset_id, codebook_id, model, prompt_version, parent_run_id=None):
        book = self.codebook(codebook_id)
        if book["dataset_id"] != dataset_id or book["status"] != "confirmed":
            raise ValueError("이 자료의 확정된 분류 기준표를 선택해주세요.")
        identifier = uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            inheritance, parent_revision = [], None
            if parent_run_id:
                parent = self.run(parent_run_id)
                if parent["dataset_id"] != dataset_id or parent["status"] != "completed":
                    raise ValueError("같은 입력 자료의 완료된 분석을 승계 기준으로 선택해주세요.")
                parent_revision = parent["result_revision"]
                inheritance = self.correction_snapshot(parent_run_id)
            db.execute("INSERT INTO runs(id,dataset_id,codebook_id,model,prompt_version,status,context,created_at,"
                       "parent_run_id,parent_result_revision,inheritance_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                identifier, dataset_id, codebook_id, model, prompt_version, "queued", book["context"], datetime.now(timezone.utc).isoformat(),
                parent_run_id, parent_revision, dump(inheritance),
            ))
        return identifier

    def run(self, identifier):
        with self.connect() as db:
            return dict(db.execute("SELECT * FROM runs WHERE id=?", (identifier,)).fetchone())

    def list_runs(self, dataset_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM runs WHERE dataset_id=? ORDER BY created_at DESC", (dataset_id,))]

    def claim(self, identifier):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM runs WHERE lease_until>?", (time.time(),)).fetchone():
                raise ValueError("분류가 이미 진행 중입니다. 중단된 실행은 최대 6분 후 이어서 처리할 수 있습니다.")
            db.execute("UPDATE runs SET status='running',error='',lease_until=? WHERE id=?", (time.time() + 360, identifier))

    def update_status(self, identifier, status, error=""):
        with self.connect() as db:
            db.execute("UPDATE runs SET status=?,error=?,lease_until=0 WHERE id=?", (status, error, identifier))

    def refresh_lease(self, identifier):
        with self.connect() as db:
            db.execute("UPDATE runs SET lease_until=? WHERE id=?", (time.time() + 360, identifier))

    def save_result(self, run_id, round_number, voc_id, result=None, error=""):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?)", (
                run_id, round_number, voc_id, "failed" if error else "success",
                dump(result.model_dump()) if result is not None else None, error,
            ))
            db.execute("UPDATE runs SET lease_until=? WHERE id=?", (time.time() + 360, run_id))

    def results(self, run_id, round_number=None):
        if round_number is None:
            round_number = self.run(run_id)["round"]
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM results WHERE run_id=? AND round=? ORDER BY voc_id", (run_id, round_number))]
        for row in rows:
            row["result"] = json.loads(row.pop("result_json")) if row["result_json"] else None
            row.pop("result_json", None)
        return rows

    def add_round(self, run_id, codes, changes):
        run = self.run(run_id)
        book = self.codebook(run["codebook_id"])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            inherited = {row["voc_id"]: row for row in json.loads(run["inheritance_json"])}
            inherited.update({row["voc_id"]: row for row in self.correction_snapshot(run_id)})
            identifier = self._save_codebook(db, run["dataset_id"], codes, run["context"], run["model"],
                book["sample_ids"], "confirmed", book["id"], changes, book["constraints"])
            db.execute("UPDATE runs SET codebook_id=?,round=round+1,lease_until=?,inheritance_json=? WHERE id=?",
                       (identifier, time.time() + 360, dump(list(inherited.values())), run_id))

    def correction_history(self, run_id, round_number=None):
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM corrections WHERE run_id=? ORDER BY revision", (run_id,))]
        for row in rows:
            for key in ("result", "edits", "previous"):
                row[key] = json.loads(row.pop(key + "_json"))
        return [row for row in rows if round_number is None or row["round"] == round_number]

    def corrections(self, run_id):
        rows = self.correction_history(run_id, self.run(run_id)["round"])
        return {row["voc_id"]: row for row in rows}

    def correction_snapshot(self, run_id):
        return [{"voc_id": row["voc_id"], "codebook_id": row["codebook_id"], "edits": row["edits"],
                 "result": row["result"]} for row in self.corrections(run_id).values() if row["status"] == "applied"]

    def effective_results(self, run_id):
        corrections = self.corrections(run_id)
        rows = self.results(run_id)
        for row in rows:
            correction = corrections.get(row["voc_id"])
            row["source"] = "AI 분류"
            if correction:
                row["source"] = "사용자 수정" if correction["kind"] != "inherited" else "수정값 승계"
                row["correction_id"] = correction["id"]
                if correction["status"] == "needs_review":
                    row.update(status="review", result=None, error=correction["reason"], source="승계 검토 필요")
                else:
                    row.update(status="success", result=correction["result"], error="")
        return rows

    def insert_correction(self, db, run, voc_id, result, edits, previous, reason, kind="manual", status="applied"):
        revision = db.execute("SELECT result_revision FROM runs WHERE id=?", (run["id"],)).fetchone()[0] + 1
        identifier = uuid4().hex
        db.execute("INSERT INTO corrections VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            identifier, run["id"], run["round"], voc_id, revision, run["codebook_id"], kind, status,
            dump(result), dump(edits), dump(previous), reason, datetime.now(timezone.utc).isoformat(),
        ))
        db.execute("UPDATE runs SET result_revision=? WHERE id=?", (revision, run["id"]))
        return identifier

    def extend_limit(self, run_id):
        with self.connect() as db:
            db.execute("UPDATE runs SET round_limit=round_limit+2 WHERE id=? AND status='needs_review' AND lease_until=0", (run_id,))

    def save_group(self, run_id, name, code_ids, sentiment="전체"):
        from src.grouping import SENTIMENTS

        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError("묶음 이름을 1~80자로 입력해주세요.")
        if sentiment not in SENTIMENTS:
            raise ValueError("허용된 감성을 선택해주세요.")
        run = self.run(run_id)
        if run["status"] != "completed":
            raise ValueError("완료된 분석의 묶음만 저장할 수 있습니다.")
        codes = self.codebook(run["codebook_id"])["codes"]
        selected = set(code_ids)
        if not selected or selected - {code.id for code in codes}:
            raise ValueError("현재 분류 기준표에서 하나 이상의 분류를 선택해주세요.")
        identifier = uuid4().hex
        with self.connect() as db:
            try:
                db.execute("INSERT INTO result_groups VALUES (?,?,?,?,?,?,?)", (
                    identifier, run_id, run["codebook_id"], name,
                    dump([code.id for code in codes if code.id in selected]), sentiment,
                    datetime.now(timezone.utc).isoformat(),
                ))
            except sqlite3.IntegrityError:
                raise ValueError("같은 이름의 묶음이 있습니다. 다른 이름으로 저장해주세요.") from None
        return identifier

    def list_groups(self, run_id):
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM result_groups WHERE run_id=? ORDER BY created_at DESC", (run_id,))]
        for row in rows:
            row["code_ids"] = json.loads(row.pop("code_ids_json"))
        return rows
