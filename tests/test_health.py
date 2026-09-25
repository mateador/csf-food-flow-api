"""
Liveness (/health) and readiness (/health/ready). The uptime check and the
deploy check rely on readiness failing when the database can't be reached,
so that failure path is tested, not just the happy path.
"""
import src.app as app_module


async def test_health_is_ok(api):
    res = await api.get("/api/v1/health")

    assert res.status == 200
    assert res.json == {"status": "ok", "version": "0.1.0"}


async def test_ready_when_the_database_answers(api):
    res = await api.get("/api/v1/health/ready")

    assert res.status == 200
    assert res.json == {"status": "ok", "database": "ok"}


async def test_not_ready_when_the_database_is_unreachable(api, monkeypatch):
    class BrokenPool:
        def acquire(self, timeout=None):
            raise ConnectionRefusedError("database down")

    monkeypatch.setattr(app_module.db_client, "pool", lambda: BrokenPool())

    res = await api.get("/api/v1/health/ready")

    assert res.status == 503
    assert res.json == {"status": "unavailable", "database": "unreachable"}


async def test_readiness_needs_no_session(api):
    # The uptime check calls it signed out.
    res = await api.get("/api/v1/health/ready")

    assert res.status != 401