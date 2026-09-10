"""Dedicated worker exits safely when the database also fails during shutdown."""


def test_loop_failure_does_not_publish_database_exception_on_shutdown(monkeypatch, caplog):
    from app.scripts import run_professional_worker as script

    class Worker:
        def __init__(self, *args, **kwargs):
            pass

        def run_once(self):
            raise RuntimeError("private-worker-sentinel")

    class Publisher:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    def database_unavailable(_worker_id):
        raise RuntimeError("private-database-sentinel")

    monkeypatch.setattr(script, "ProfessionalWorker", Worker)
    monkeypatch.setattr(script, "WorkerHeartbeatPublisher", Publisher)
    monkeypatch.setattr(script, "_mark_loop_failed", database_unavailable)
    assert script.main(["--loop", "--max-consecutive-failures", "1"]) == 1
    assert "sentinel" not in caplog.text
