from src.demo_data import seed_public_demo, prepare_cloud_demo
from src.storage import Store


def test_public_demo_restores_completed_results_and_history(tmp_path):
    store = Store(tmp_path / "demo.db")
    assert seed_public_demo(store)
    datasets = store.list_datasets()
    assert {d["name"] for d in datasets} == {"SSI", "상품평 VOC"}
    counts = {}
    for d in datasets:
        records = store.dataset(d["id"])["records"]
        counts[d["name"]] = len(records)
        assert all(r["metadata"] == {} for r in records)
        runs = store.list_runs(d["id"])
        assert len(runs) == 1 and runs[0]["status"] == "completed"
        results = store.effective_results(runs[0]["id"])
        assert {r["voc_id"] for r in results} == {r["id"] for r in records}
    assert counts == {"SSI": 20, "상품평 VOC": 50}
    assert store.active_run() is None
    with store.connect() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 4


def test_demo_does_not_overwrite_edits_or_reappear_after_deletion(tmp_path):
    store = Store(tmp_path / "demo.db")
    seed_public_demo(store)
    with store.connect() as db:
        db.execute("UPDATE datasets SET name='직접 수정한 이름' WHERE name='SSI'")
    assert seed_public_demo(store) is False
    assert any(d["name"] == "직접 수정한 이름" for d in store.list_datasets())
    with store.connect() as db:
        for table in ("corrections", "result_groups", "results", "runs"):
            db.execute(f"DELETE FROM {table}")
        db.execute("UPDATE codebooks SET parent_id=NULL")
        db.execute("DELETE FROM codebooks")
        db.execute("DELETE FROM datasets")
    assert seed_public_demo(store) is False
    assert store.list_datasets() == []


def test_local_environment_does_not_seed_cloud_demo(tmp_path, monkeypatch):
    from src import config
    local_env = tmp_path / ".env"
    local_env.write_text("GEMINI_API_KEY=example", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", local_env)
    store = Store(tmp_path / "local.db")
    prepare_cloud_demo(store)
    assert store.list_datasets() == []


def test_hosted_demo_does_not_require_ai_credentials(tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / "missing.env")
    store = Store(tmp_path / "cloud.db")
    prepare_cloud_demo(store)
    assert {d["name"] for d in store.list_datasets()} == {"SSI", "상품평 VOC"}
