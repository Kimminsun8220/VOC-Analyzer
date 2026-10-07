"""공개 승인된 발표 자료를 클라우드 DB에 한 번만 추가한다."""

import json
from pathlib import Path

DEMO_PATH = Path(__file__).resolve().parents[1] / "data" / "public_demo.json"
TABLES = ("datasets", "codebooks", "runs", "results", "result_groups", "corrections")


def seed_public_demo(store, path=DEMO_PATH):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("지원하지 않는 데모 자료 형식입니다.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS demo_imports (name TEXT PRIMARY KEY)")
        if db.execute("SELECT 1 FROM demo_imports WHERE name='public-demo-v1'").fetchone():
            return False
        for dataset in payload["tables"]["datasets"]:
            identifier = dataset["id"]
            # Existing local or cloud edits always win, including incomplete imports.
            if db.execute("SELECT 1 FROM datasets WHERE id=?", (identifier,)).fetchone():
                continue
            books = {r["id"] for r in payload["tables"]["codebooks"] if r["dataset_id"] == identifier}
            runs = {r["id"] for r in payload["tables"]["runs"] if r["dataset_id"] == identifier}
            for table in TABLES:
                allowed = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                for row in payload["tables"][table]:
                    selected = (row["id"] == identifier if table == "datasets" else
                                row["id"] in books if table == "codebooks" else
                                row["id"] in runs if table == "runs" else row["run_id"] in runs)
                    if not selected:
                        continue
                    if not set(row) <= allowed:
                        raise ValueError("데모 자료의 필드가 저장 구조와 맞지 않습니다.")
                    columns = list(row)
                    names = ",".join(f'"{name}"' for name in columns)
                    marks = ",".join("?" for _ in columns)
                    db.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", [row[c] for c in columns])
        db.execute("INSERT INTO demo_imports VALUES ('public-demo-v1')")
    return True


def prepare_cloud_demo(store):
    from src.config import ENV_PATH

    if ENV_PATH.exists():
        return
    seed_public_demo(store)
