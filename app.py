"""Local Model + DeepEval/Jev monitor. All scores come from live provider calls."""
from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fixtures import CATEGORIES, POLICY, POLICY_VERSION, SCENARIOS, TICKETS, text_hash, ticket_at
from monitoring import MIN_SAMPLES, compute_monitor, summarize
from providers import JEV_MODEL, RUBRIC, RUBRIC_VERSION, ProviderError, Providers
from storage import Store, utcnow
from lab import LabController, router as lab_router

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("JUDGEJEV_DATA_DIR") or str(ROOT / "data"))


def uid(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def compact(row):
    fields = ("id", "seq", "run_id", "phase", "scenario", "mix", "ticket_id", "category", "input", "source", "status", "created_at", "finished_at", "quality", "passed", "metrics", "generator_ms", "eval_ms", "error", "generator_model", "judge_model")
    return {k: row[k] for k in fields if k in row}


class RunInput(BaseModel):
    mode: Literal["proof", "baseline", "traffic"] = "traffic"
    scenario: Literal["healthy", "stale_policy", "missing_context"] = "healthy"
    mix: Literal["balanced", "refund_surge", "harder_questions"] = "balanced"
    count: int = Field(default=20, ge=1, le=100)
    interval_ms: int = Field(default=250, ge=0, le=5000)


class TicketInput(BaseModel):
    text: str = Field(min_length=5, max_length=3000)
    category: Literal["Returns", "Shipping", "Billing", "Account", "Warranty", "Custom"] = "Custom"
    scenario: Literal["healthy", "stale_policy", "missing_context"] = "healthy"


class Controller:
    def __init__(self, store, providers):
        self.store, self.providers = store, providers
        self.listeners = set()
        self.task = None
        self.active = None
        self.stop = asyncio.Event()
        self.version = 0
        self.last_broadcast = utcnow()

    def changed(self):
        self.version += 1
        self.last_broadcast = utcnow()
        for listener in self.listeners:
            if not listener.full():
                listener.put_nowait(self.version)

    def snapshot(self):
        rows = self.store.requests()
        baseline = self.store.get("baseline")
        controls_path = DATA / "controls.json"
        controls = json.loads(controls_path.read_text(encoding="utf-8")) if controls_path.exists() else None
        audit_path = DATA / "verification.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else None
        return {
            "version": self.version, "server_time": utcnow(), "last_update": self.last_broadcast,
            "connection": {"generator_model": self.providers.generator_model, "generator_verified": self.providers.generator_verified, "jev_model": JEV_MODEL, "jev_configured": bool(self.providers.jev_key), "jev_verified": self.providers.jev_verified},
            "requests": [compact(r) for r in rows[-240:]], "total_requests": len(rows),
            "counts": {s: sum(r["status"] == s for r in rows) for s in ("completed", "generating", "evaluating", "failed", "awaiting_key", "interrupted")},
            "active_run": self.active, "runs": self.store.runs()[-20:], "baseline": baseline,
            "monitor": compute_monitor(rows, baseline), "overall": summarize(rows),
            "events": self.store.events(), "proof": self.store.get("proof"), "controls": controls, "audit": audit,
            "config": {"rubric": RUBRIC, "rubric_version": RUBRIC_VERSION, "policy_version": POLICY_VERSION, "policy_sha256": text_hash(POLICY), "scenarios": [{"value": k, "label": v["label"]} for k, v in SCENARIOS.items()], "categories": CATEGORIES, "versions": {p: importlib.metadata.version(p) for p in ("deepeval", "typesafe-sdk")}},
        }

    async def process(self, ticket, scenario, run_id, phase, mix):
        row = self.store.create_request({"id": uid("req"), "run_id": run_id, "phase": phase, "scenario": scenario, "mix": mix, "ticket_id": ticket["id"], "category": ticket["category"], "input": ticket["input"], "expected_output": ticket.get("expected_output"), "source": ticket["source"], "status": "generating", "created_at": utcnow()})
        self.changed()
        try:
            row.update(await self.providers.generate(ticket, scenario))
            row.update(status="evaluating", generated_at=utcnow())
            self.store.save_request(row)
            self.changed()
            if not self.providers.jev_key:
                raise ProviderError("Jev is not connected. The Model answer is saved, with no score assigned.")
            row.update(await self.providers.evaluate(ticket, row["answer"]))
            row.update(status="completed", finished_at=utcnow())
        except asyncio.CancelledError:
            row.update(status="interrupted", finished_at=utcnow(), error="The server stopped during this request.")
            self.store.save_request(row)
            self.changed()
            raise
        except ProviderError as exc:
            row.update(status="failed" if self.providers.jev_key else "awaiting_key", error=str(exc), finished_at=utcnow())
            self.store.event("request_failed", request_id=row["id"], message=str(exc))
        except Exception as exc:
            row.update(status="failed", error=f"Request failed ({type(exc).__name__}). No score was substituted.", finished_at=utcnow())
            self.store.event("request_failed", request_id=row["id"], message=row["error"])
        self.store.save_request(row)
        if row["status"] == "completed":
            monitor = compute_monitor(self.store.requests(), self.store.get("baseline"))
            previous = self.store.get("last_drift_status")
            current = {"quality": monitor["quality_status"], "mix": monitor["mix_status"]}
            if previous != current:
                self.store.event("drift_state", request_id=row["id"], status=current, quality_delta=monitor["quality_delta"], mix_js=monitor["mix_js"], current_count=monitor["current"]["count"])
                self.store.set("last_drift_status", current)
        self.changed()
        return row

    def begin(self, options, ticket=None):
        if getattr(self, 'lab', None) and self.lab.active:
            raise HTTPException(409, "An evaluation lab experiment is active. Wait for it to finish.")
        if self.task and not self.task.done():
            raise HTTPException(409, "A run is already active. Stop it or wait for completion.")
        if not self.providers.jev_key:
            raise HTTPException(409, "Connect Jev before starting evaluated traffic.")
        if options.mode == "baseline" and (options.scenario != "healthy" or options.mix != "balanced" or options.count < MIN_SAMPLES):
            raise HTTPException(422, "A reference requires at least 20 balanced tickets using the current policy.")
        self.stop = asyncio.Event()
        run = {"id": uid("run"), "status": "running", "mode": options.mode, "created_at": utcnow(), "planned": 60 if options.mode == "proof" else options.count, "completed": 0, "failed": 0, "phase": "starting", "options": options.model_dump(), "stages": []}
        self.active = run
        self.store.save_run(run)
        self.store.event("run_started", run_id=run["id"], mode=options.mode, planned=run["planned"])
        self.task = asyncio.create_task(self.run(run, options, ticket))
        self.changed()
        return run

    async def run(self, run, options, custom_ticket=None):
        try:
            plans = [("baseline", "healthy", "balanced", 20), ("fault", "stale_policy", "balanced", 20), ("recovery", "healthy", "balanced", 20)] if options.mode == "proof" else [("baseline" if options.mode == "baseline" else "traffic", options.scenario, options.mix, options.count)]
            for phase, scenario, mix, count in plans:
                if self.stop.is_set():
                    break
                run.update(phase=phase, scenario=scenario, phase_completed=0, phase_planned=count)
                self.store.event("phase_started", run_id=run["id"], phase=phase, scenario=scenario, mix=mix)
                self.store.save_run(run)
                self.changed()
                phase_rows = []
                # Two bounded in-flight requests keep metrics moving without unbounded API usage.
                for offset in range(0, count, 2):
                    if self.stop.is_set():
                        break
                    batch = await asyncio.gather(*(self.process(custom_ticket or ticket_at(i, mix), scenario, run["id"], phase, mix) for i in range(offset, min(count, offset + 2))))
                    phase_rows.extend(batch)
                    run["completed"] += sum(r["status"] == "completed" for r in batch)
                    run["failed"] += sum(r["status"] != "completed" for r in batch)
                    run["phase_completed"] = len(phase_rows)
                    self.store.save_run(run)
                    self.changed()
                    if any(r["status"] != "completed" for r in batch):
                        run["error"] = "A provider call failed. This run stopped and retained its partial evidence."
                        break
                    if options.interval_ms and not self.stop.is_set():
                        try:
                            await asyncio.wait_for(self.stop.wait(), timeout=options.interval_ms / 1000)
                        except asyncio.TimeoutError:
                            pass
                good = [r for r in phase_rows if r["status"] == "completed"]
                stage = {"phase": phase, "scenario": scenario, "request_ids": [r["id"] for r in phase_rows], "summary": summarize(phase_rows), "ended_at": utcnow()}
                if phase == "baseline" and len(good) == count and not self.stop.is_set():
                    baseline = {"id": uid("ref"), "run_id": run["id"], "created_at": utcnow(), "request_ids": [r["id"] for r in good], "policy_version": POLICY_VERSION, "rubric_version": RUBRIC_VERSION, "model": self.providers.generator_model, "judge": JEV_MODEL, "summary": summarize(good)}
                    self.store.set("baseline", baseline)
                    self.store.event("baseline_frozen", baseline_id=baseline["id"], count=len(good), quality=baseline["summary"]["quality"])
                stage["monitor"] = compute_monitor(self.store.requests(), self.store.get("baseline"))
                run["stages"].append(stage)
                self.store.save_run(run)
                self.changed()
                if len(good) != count or self.stop.is_set():
                    break
            run["status"] = "stopped" if self.stop.is_set() else "failed" if run["failed"] or run["completed"] != run["planned"] else "completed"
            run["ended_at"] = utcnow()
            if options.mode == "proof":
                stages = {s["phase"]: s for s in run["stages"]}
                passed = run["status"] == "completed" and stages.get("fault", {}).get("monitor", {}).get("quality_status") == "alert" and stages.get("recovery", {}).get("monitor", {}).get("quality_status") == "stable"
                proof = {"run_id": run["id"], "status": "passed" if passed else "incomplete" if run["status"] != "completed" else "not_demonstrated", "completed_at": utcnow(), "stages": run["stages"], "claim": "Observed quality alert after wrong-policy calls, followed by recovery on the same 20 synthetic tickets." if passed else "The full fault-and-recovery criterion has not been demonstrated.", "counts": {"generator_and_jev_completed": run["completed"], "failed": run["failed"]}}
                self.store.set("proof", proof)
            self.store.save_run(run)
            self.store.event("run_finished", run_id=run["id"], status=run["status"], completed=run["completed"], failed=run["failed"])
            if options.mode == "proof":
                (DATA / f"proof-{run['id']}.json").write_text(json.dumps(self.export(), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        except asyncio.CancelledError:
            run.update(status="interrupted", ended_at=utcnow())
            self.store.save_run(run)
            raise
        except Exception as exc:
            run.update(status="failed", error=f"Run stopped ({type(exc).__name__}). Existing evidence is retained.", ended_at=utcnow())
            self.store.save_run(run)
            self.store.event("run_failed", run_id=run["id"], message=run["error"])
        finally:
            self.active = None
            self.changed()

    def export(self):
        state = self.snapshot()
        return {"schema_version": 1, "exported_at": utcnow(), "source": "Real Model and TypeSafe API calls on synthetic or user-entered support tickets", "config": state["config"], "baseline": state["baseline"], "monitor": state["monitor"], "proof": state["proof"], "controls": state["controls"], "runs": self.store.runs(), "requests": self.store.requests(), "events": self.store.events(10000)}


@asynccontextmanager
async def lifespan(app):
    DATA.mkdir(parents=True, exist_ok=True)
    store = Store(DATA / "monitor.sqlite3")
    store.recover()
    controller = Controller(store, Providers())
    app.state.controller = controller
    app.state.lab = LabController(controller, DATA)
    controller.lab = app.state.lab
    yield
    await app.state.lab.close()
    if controller.task and not controller.task.done():
        controller.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await controller.task
    await controller.providers.close()
    store.close()


app = FastAPI(title="JudgeJev · live evaluation", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(lab_router)


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Host validation also prevents DNS rebinding from reaching local controls.
    if request.url.hostname not in ("127.0.0.1", "localhost", "testserver"):
        return JSONResponse({"detail": "This monitor is local only."}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Use the local dashboard to change a run."}, status_code=403)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"detail": "Expected JSON."}, status_code=415)
    response = await call_next(request)
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'self'"})
    return response


def ctl(request):
    return request.app.state.controller


@app.get("/api/state")
async def state(request: Request):
    return ctl(request).snapshot()


@app.get("/api/events")
async def stream(request: Request):
    controller = ctl(request)
    async def events():
        listener = asyncio.Queue(maxsize=1)
        controller.listeners.add(listener)
        try:
            yield "event: state\ndata: " + json.dumps(controller.snapshot()) + "\n\n"
            while not await request.is_disconnected():
                try:
                    await asyncio.wait_for(listener.get(), timeout=10)
                    yield "event: state\ndata: " + json.dumps(controller.snapshot()) + "\n\n"
                except asyncio.TimeoutError:
                    yield "event: heartbeat\ndata: " + json.dumps({"server_time": utcnow()}) + "\n\n"
        finally:
            controller.listeners.discard(listener)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.post("/api/run")
async def start_run(options: RunInput, request: Request):
    return ctl(request).begin(options)


@app.post("/api/stop")
async def stop_run(request: Request):
    controller = ctl(request)
    controller.stop.set()
    if controller.active:
        controller.active["status"] = "stopping"
        controller.changed()
    return {"message": "Finishing the in-flight requests; no further requests will start."}


@app.post("/api/ticket")
async def send_ticket(ticket: TicketInput, request: Request):
    value = {"id": uid("custom"), "category": ticket.category, "input": ticket.text, "source": "user-entered ticket"}
    return ctl(request).begin(RunInput(count=1, scenario=ticket.scenario, interval_ms=0), value)


@app.get("/api/requests/{request_id}")
async def detail(request_id: str, request: Request):
    row = ctl(request).store.request(request_id)
    if not row:
        raise HTTPException(404, "Request not found.")
    return row


@app.get("/api/export")
async def export(request: Request):
    return JSONResponse(ctl(request).export(), headers={"Content-Disposition": 'attachment; filename="generator-jev-evidence.json"'})


@app.get("/api/verification")
async def verification_report():
    path = DATA / "verification.json"
    if not path.exists():
        raise HTTPException(404, "Run verify_evidence.py on an exported proof to create the independent report.")
    return FileResponse(path, media_type="application/json", filename="generator-jev-verification.json")


@app.post("/api/connect")
async def connect(request: Request):
    controller = ctl(request)
    if controller.active or getattr(controller, 'lab', None) and controller.lab.active:
        raise HTTPException(409, "Wait for the active run to finish before changing the connection.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise HTTPException(413, "Key input is too long.")
    try:
        key = json.loads(body).get("api_key", "")
    except (ValueError, AttributeError):
        raise HTTPException(400, "Expected an API key.") from None
    if not isinstance(key, str) or not 16 <= len(key) <= 4096 or any(c.isspace() for c in key):
        raise HTTPException(400, "Enter a valid TypeSafe API key.")
    try:
        result = await controller.providers.evaluate(TICKETS[1], "Standard shipping takes 3–5 business days. An exact arrival date cannot be guaranteed.", key=key)
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from None
    controller.providers.jev_key = key
    controller.store.event("jev_connected", model=JEV_MODEL, eval_ms=result["eval_ms"], quality=result["quality"])
    controller.changed()
    if getattr(controller, 'lab', None):
        controller.lab.changed()
    return {"connected": True, "model": JEV_MODEL, "quality": result["quality"], "eval_ms": result["eval_ms"]}


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/lab")
async def evaluation_lab():
    return FileResponse(ROOT / "static" / "lab.html")


(ROOT / "static").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
