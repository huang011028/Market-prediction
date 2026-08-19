from src.core.persistent_job_store import PersistentJobStore


def test_job_store_persists_progress_and_result(tmp_path):
    path = tmp_path / "jobs.db"
    store = PersistentJobStore(path)
    store.create(job_id="j1", kind="analysis", request={"target": "000001"})
    store.update("j1", status="running", progress=42, message="Agent 执行中")

    reloaded = PersistentJobStore(path).get("j1")
    assert reloaded["status"] == "running"
    assert reloaded["progress"] == 42
    assert reloaded["request"]["target"] == "000001"

    store.update("j1", status="completed", progress=100, result={"ok": True})
    assert PersistentJobStore(path).get("j1")["result"] == {"ok": True}


def test_job_store_recovers_interrupted_jobs_with_retry_budget(tmp_path):
    store = PersistentJobStore(tmp_path / "jobs.db")
    store.create(job_id="recover", kind="analysis", request={"target": "0700"})
    store.update("recover", status="running", progress=58)

    recovered = PersistentJobStore(tmp_path / "jobs.db").recover_interrupted()
    assert [item["job_id"] for item in recovered] == ["recover"]
    assert recovered[0]["attempts"] == 1
    assert store.get("recover")["status"] == "queued"

    store.update("recover", status="running")
    store.recover_interrupted()
    store.update("recover", status="running")
    assert store.recover_interrupted() == []
    assert store.get("recover")["status"] == "failed"
