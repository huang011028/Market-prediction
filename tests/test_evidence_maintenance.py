import pytest

from src.core.evidence_maintenance import (
    EvidenceMaintenanceConfig,
    EvidenceMaintenanceRunner,
)


class DummyStore:
    def __init__(self):
        self.verified = False

    def get_verification_queue_status(self):
        return {
            "total": 2,
            "verified": 1 if self.verified else 0,
            "pending": 1,
            "overdue": 0 if self.verified else 1,
        }

    def verify_all(self):
        self.verified = True
        return {"verified": 1}

    def get_recent_targets(self, limit=50):
        return []


@pytest.mark.asyncio
async def test_evidence_maintenance_verifies_due_predictions(monkeypatch):
    store = DummyStore()
    runner = EvidenceMaintenanceRunner(prediction_store=store)
    monkeypatch.setattr(runner, "_save_report", lambda report: None)

    report = await runner.run_once(EvidenceMaintenanceConfig())

    assert report.verified_count == 1
    assert report.queue_before["overdue"] == 1
    assert report.queue_after["overdue"] == 0
