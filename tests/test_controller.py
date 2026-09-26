"""Orchestration tests with explicit test doubles; never written to live data/."""
import asyncio
import json

import pytest
from fastapi import HTTPException

import app as application
from app import Controller, RunInput
from providers import ProviderError
from storage import Store


class ControlledProvider:
    generator_model = "unit-test-provider"
    jev_key = "unit-test-placeholder"
    generator_verified = False
    jev_verified = False

    def __init__(self, gated=False, fail=False):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.gated, self.fail = gated, fail
        self.calls = 0

    async def generate(self, ticket, scenario):
        self.calls += 1
        self.started.set()
        if self.gated:
            await self.release.wait()
        return {"answer": f"TEST DOUBLE: {scenario}", "generator_ms": 10}

    async def evaluate(self, ticket, answer):
        if self.fail:
            raise ProviderError("Intentional unit-test failure.")
        score = .2 if "stale_policy" in answer else .95
        return {"quality": score, "passed": score >= .8, "metrics": {m: score for m in ("groundedness", "relevance", "policy")}, "eval_ms": 5}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(application, "DATA", tmp_path)
    value = Store(tmp_path / "unit-test.sqlite3")
    yield value
    value.close()


def test_proof_export_contains_terminal_state(store, tmp_path):
    async def exercise():
        controller = Controller(store, ControlledProvider())
        run = controller.begin(RunInput(mode="proof", interval_ms=0))
        await controller.task
        saved = json.loads((tmp_path / f"proof-{run['id']}.json").read_text())
        assert saved["runs"][-1]["status"] == "completed"
        assert saved["proof"]["status"] == "passed"
        assert saved["events"][-1]["kind"] == "run_finished"
        assert len(saved["requests"]) == 60
        assert controller.active is None
    asyncio.run(exercise())


def test_stop_finishes_only_inflight_calls_and_does_not_freeze_partial_reference(store):
    async def exercise():
        provider = ControlledProvider(gated=True)
        controller = Controller(store, provider)
        controller.begin(RunInput(mode="baseline", count=20, interval_ms=0))
        await provider.started.wait()
        controller.stop.set()
        provider.release.set()
        await controller.task
        assert provider.calls == 2
        assert store.runs()[-1]["status"] == "stopped"
        assert len(store.requests()) == 2
        assert all(r["status"] == "completed" for r in store.requests())
        assert store.get("baseline") is None
    asyncio.run(exercise())


def test_provider_failure_preserves_answer_without_score_or_new_reference(store):
    async def exercise():
        old_reference = {"request_ids": ["original-reference"]}
        store.set("baseline", old_reference)
        provider = ControlledProvider(fail=True)
        controller = Controller(store, provider)
        controller.begin(RunInput(mode="baseline", count=20, interval_ms=0))
        await controller.task
        assert provider.calls == 2
        assert store.runs()[-1]["status"] == "failed"
        assert store.get("baseline") == old_reference
        assert all(r["status"] == "failed" and r["answer"] and "quality" not in r for r in store.requests())
    asyncio.run(exercise())


def test_concurrent_run_rejected_and_shutdown_marks_inflight_interrupted(store):
    async def exercise():
        provider = ControlledProvider(gated=True)
        controller = Controller(store, provider)
        controller.begin(RunInput(count=20, interval_ms=0))
        await provider.started.wait()
        with pytest.raises(HTTPException) as error:
            controller.begin(RunInput())
        assert error.value.status_code == 409
        controller.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await controller.task
        assert all(r["status"] == "interrupted" and "quality" not in r for r in store.requests())
        assert store.runs()[-1]["status"] == "interrupted"
    asyncio.run(exercise())


def test_crash_recovery_retains_completed_evidence(store):
    complete = store.create_request({"id": "test-done", "status": "completed", "quality": .9})
    store.create_request({"id": "test-inflight", "status": "evaluating", "answer": "retained"})
    store.save_run({"id": "test-run", "status": "running"})
    store.recover()
    assert store.request("test-done") == complete
    assert store.request("test-inflight")["status"] == "interrupted"
    assert store.request("test-inflight")["answer"] == "retained"
    assert store.runs()[0]["status"] == "interrupted"
