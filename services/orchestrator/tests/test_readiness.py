"""A2 — /readiness reports what /health cannot.

THE THREE HOLES THIS CLOSES, all of which shared one symptom: the system
looking healthy while not working.

  B5  The worker is a single asyncio.Task inside the FastAPI process
      (main.py's lifespan). If its startup raised, `worker_task` was set
      to None, `worker_loop_start_failed` was logged at WARNING, and
      /health still returned 200. Nothing consumed that warning and
      nothing else reported it, so a completely stalled orchestrator was
      indistinguishable from a working one at every automated surface.

  O1  A missing TEAMS_WEBHOOK_URL, DATABASE_URL or App Insights
      connection string each log and continue. Degrading gracefully is
      right for local dev and CI -- it is what lets `uvicorn main:app`
      run with no env at all -- but it means config-ABSENT and
      config-BROKEN look identical in a deployment where the config is
      supposed to be there.

  F6  Dead-lettered tasks emit a DeadLetterAlert nothing consumes. Its
      alerting half lives in Bicep (infra/modules/monitoring/), not here.

WHY AN EXPECTATION FLAG RATHER THAN A HARD REQUIREMENT. Making these
integrations mandatory would break the two environments that
legitimately run without them. So the DEPLOYMENT declares what it
expects: CMOS_EXPECT_TEAMS=true means "a webhook is supposed to be
configured here", and only then does its absence turn this endpoint red.
Unset means "not expected", which is exactly the pre-A2 behaviour -- the
reason this is safe to add to a running system.

WHY /health IS UNTOUCHED. A liveness probe that goes red on a dependency
outage gets the container killed and restarted, which fixes nothing and
loses in-flight work. /health answers "is the process up"; /readiness
answers "can it do its job". The first test below pins that they
disagree, because a readiness check that merely mirrors liveness closes
none of the three holes above.
"""

from __future__ import annotations

import main as orchestrator_main
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    """A TestClient that does NOT run lifespan.

    Constructing it without the context-manager form skips startup, so
    app.state.worker_task is whatever the test sets -- which is the only
    way to exercise the worker-down branch without actually breaking a
    running worker.
    """
    for expectation_var, _name, attribute in orchestrator_main._EXPECTATIONS:
        monkeypatch.delenv(expectation_var, raising=False)
        monkeypatch.delenv(attribute, raising=False)
    return TestClient(orchestrator_main.app)


class _FakeTask:
    def __init__(self, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


def _set_worker(task: object | None) -> None:
    orchestrator_main.app.state.worker_task = task


# ---------------------------------------------------------------------
# The done-criterion: worker down => readiness red, health still green
# ---------------------------------------------------------------------


def test_a_dead_worker_turns_readiness_red_while_health_stays_green(client):
    """A2's stated done-criterion, exactly.

    This is the B5 scenario: startup raised, worker_task is None. Before
    A2 the only observable difference was one WARNING line.
    """
    _set_worker(None)

    assert client.get("/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}

    response = client.get("/readiness")
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["checks"]["worker"] == "not_started"
    assert any("worker is not_started" in f for f in body["failures"])


def test_a_worker_that_exited_is_reported_as_stopped(client):
    """A long-lived loop that returned is a stall, not a success.

    run_worker_loop is supposed to run for the life of the process, so
    `done()` being true means it fell out of its loop -- which looks
    identical to a healthy process from the outside.
    """
    _set_worker(_FakeTask(done=True))

    response = client.get("/readiness")

    assert response.status_code == 503
    assert response.json()["checks"]["worker"] == "stopped"


def test_a_running_worker_with_nothing_expected_is_ready(client):
    """The default posture: no expectations declared, worker running.

    This is what CI and local dev look like, and it must be green --
    otherwise A2 would turn every existing environment red on arrival.
    """
    _set_worker(_FakeTask(done=False))

    response = client.get("/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["checks"]["worker"] == "running"
    assert body["failures"] == []


# ---------------------------------------------------------------------
# O1: absent-and-expected is an error; absent-and-unexpected is not
# ---------------------------------------------------------------------


def test_an_expected_but_absent_integration_is_a_failure(client, monkeypatch):
    _set_worker(_FakeTask(done=False))
    monkeypatch.setenv("CMOS_EXPECT_TEAMS", "true")

    response = client.get("/readiness")

    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["integrations"]["teams"] == "expected_but_absent"
    assert any("TEAMS_WEBHOOK_URL is unset" in f for f in body["failures"])


def test_an_expected_and_present_integration_is_fine(client, monkeypatch):
    _set_worker(_FakeTask(done=False))
    monkeypatch.setenv("CMOS_EXPECT_TEAMS", "true")
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://teams.invalid/hook")

    response = client.get("/readiness")

    assert response.status_code == 200
    assert response.json()["checks"]["integrations"]["teams"] == "configured"


def test_an_absent_and_unexpected_integration_is_not_a_failure(client):
    """The pre-A2 behaviour, preserved.

    Local dev and CI run with none of these set. If absence alone were an
    error, adding this endpoint would have made every such environment
    report itself broken.
    """
    _set_worker(_FakeTask(done=False))

    body = client.get("/readiness").json()

    assert body["ready"] is True
    assert body["checks"]["integrations"]["teams"] == "not_expected"


def test_a_present_but_unexpected_integration_is_still_reported(client, monkeypatch):
    """Reported as configured rather than hidden -- the endpoint is a
    description of reality, not only a list of complaints."""
    _set_worker(_FakeTask(done=False))
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://teams.invalid/hook")

    body = client.get("/readiness").json()

    assert body["ready"] is True
    assert body["checks"]["integrations"]["teams"] == "configured"


def test_expectation_flags_accept_only_affirmative_values(client, monkeypatch):
    """`CMOS_EXPECT_TEAMS=false` must not read as an expectation.

    A flag that treats any non-empty string as true would make
    `=false` mean the opposite of what it says -- the kind of thing
    nobody notices until it pages them.
    """
    _set_worker(_FakeTask(done=False))
    for value in ("false", "0", "no", ""):
        monkeypatch.setenv("CMOS_EXPECT_TEAMS", value)
        assert client.get("/readiness").status_code == 200, value

    monkeypatch.setenv("CMOS_EXPECT_TEAMS", "true")
    assert client.get("/readiness").status_code == 503


# ---------------------------------------------------------------------
# Database reachability
# ---------------------------------------------------------------------


def test_an_unreachable_database_is_a_failure(client, monkeypatch):
    _set_worker(_FakeTask(done=False))
    monkeypatch.setenv("DATABASE_URL", "postgres://nobody@127.0.0.1:1/none")

    def _boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(orchestrator_main.db, "fetch_all_task_status", _boom)

    response = client.get("/readiness")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unreachable"


def test_a_reachable_database_is_fine(client, monkeypatch):
    _set_worker(_FakeTask(done=False))
    monkeypatch.setenv("DATABASE_URL", "postgres://somebody@127.0.0.1:5432/cmos")
    monkeypatch.setattr(orchestrator_main.db, "fetch_all_task_status", lambda: [])

    response = client.get("/readiness")

    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "reachable"


def test_an_unconfigured_database_is_reported_without_being_probed(client, monkeypatch):
    """No DATABASE_URL means no connection attempt at all.

    Probing an unconfigured database would turn every local run into a
    slow timeout on a readiness check.
    """
    _set_worker(_FakeTask(done=False))
    probed = {"called": False}

    def _probe():
        probed["called"] = True
        return []

    monkeypatch.setattr(orchestrator_main.db, "fetch_all_task_status", _probe)

    body = client.get("/readiness").json()

    assert body["checks"]["database"] == "not_configured"
    assert probed["called"] is False
    assert body["ready"] is True


# ---------------------------------------------------------------------
# The two endpoints must stay different
# ---------------------------------------------------------------------


def test_health_never_reports_readiness(client):
    """Guard against the two collapsing into one.

    If /health ever starts reflecting dependency state it becomes unsafe
    as a liveness probe -- a dependency outage would get the container
    restarted in a loop. Pinned because the collapse would be a one-line
    change that looks like a tidy-up.
    """
    _set_worker(None)

    health = client.get("/health")
    readiness = client.get("/readiness")

    assert health.status_code == 200
    assert readiness.status_code == 503
    assert health.json() == {"status": "ok"}
    assert "checks" not in health.json()
