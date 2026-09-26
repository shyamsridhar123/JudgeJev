"""A persistent learning lab, isolated from the live monitor's reference cohort."""
from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import json
import threading
import uuid
from statistics import mean

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from fixtures import POLICY, POLICY_VERSION, TICKETS, text_hash
from lab_engine import PRESETS, canonical, dataset_summary, evaluate_case, evaluate_dataset, python_example, replay
from lab_schema import CaseAction, DatasetAction, DriftAction, ReplayAction, Rubric
from lab_examples import controls
from monitoring import at_least, js_divergence, ks_distance, scored
from providers import JEV_MODEL, ProviderError
from storage import Store, utcnow
from verify_lab import verify

router = APIRouter(prefix='/api/lab')


def uid(prefix):
    return f'{prefix}-{uuid.uuid4().hex[:12]}'


def brief(row):
    return {k: row[k] for k in ('id', 'job_id', 'kind', 'status', 'created_at', 'finished_at', 'ticket_id', 'variant', 'scenario',
                                'input', 'quality', 'passed', 'eval_ms', 'generator_ms', 'applicable_count', 'error', 'source') if k in row}


class LabController:
    def __init__(self, monitor, data):
        self.monitor = monitor
        self.providers = monitor.providers
        self.store = Store(data / 'lab.sqlite3')
        self.store.recover()
        self.active = None
        self.task = None
        self.stop = threading.Event()
        self.listeners = set()
        self.version = 0

    def changed(self):
        self.version += 1
        for listener in self.listeners:
            if not listener.full():
                listener.put_nowait(self.version)

    def snapshot(self):
        rows = self.store.requests()
        jobs = self.store.runs()
        recent_ids = {job['id'] for job in jobs[-30:]}
        # Dataset history is durable even after many single-case evaluations.
        visible_jobs = [job for job in jobs if job['kind'] == 'dataset' or job['id'] in recent_ids]
        return {'version': self.version, 'server_time': utcnow(), 'active': self.active,
                'connection': {'generator_model': self.providers.generator_model, 'generator_verified': self.providers.generator_verified,
                               'jev_model': JEV_MODEL, 'jev_configured': bool(self.providers.jev_key), 'jev_verified': self.providers.jev_verified},
                'versions': {p: importlib.metadata.version(p) for p in ('deepeval', 'typesafe-sdk')},
                'counts': {'generator': sum(bool(r.get('generator_response')) for r in rows),
                           'jev': sum(r.get('status') == 'completed' and bool(r.get('jev_response')) for r in rows),
                           'failed': sum(r.get('status') == 'failed' for r in rows)},
                'records': [brief(r) for r in rows[-120:]],
                'jobs': [{k: j[k] for k in ('id', 'kind', 'status', 'created_at', 'finished_at', 'phase', 'completed', 'planned', 'error', 'summary', 'record_ids', 'options') if k in j} for j in visible_jobs],
                'monitor_active': bool(self.monitor.active)}

    def export(self):
        rows = self.store.requests()
        local_ids = {r['id'] for r in rows}
        referenced = {r['generation_record_id'] for r in rows if r.get('generation_record_id')} - local_ids
        return {'schema_version': 2, 'exported_at': utcnow(), 'scope': 'Evaluation lab only; isolated from the monitoring cohort',
                'versions': self.snapshot()['versions'], 'presets': PRESETS, 'policy_version': POLICY_VERSION,
                'requests': rows, 'jobs': self.store.runs(),
                'generation_sources': [r for r in self.monitor.store.requests() if r['id'] in referenced]}

    def start(self, body):
        if self.active or self.monitor.active:
            raise HTTPException(409, 'An experiment is active. Wait for it or stop it before starting another.')
        if not self.providers.jev_key:
            raise HTTPException(409, 'Jev is disconnected. Connect it from the live monitor.')
        kind = 'dataset' if isinstance(body, DatasetAction) else body.action
        job = {'id': uid('lab'), 'kind': kind, 'status': 'running', 'created_at': utcnow(), 'phase': 'starting',
               'planned': body.count * 2 if kind == 'dataset' else 1, 'completed': 0, 'record_ids': [], 'options': body.model_dump(exclude_none=True)}
        self.active = job
        self.stop.clear()
        self.store.save_run(job)
        self.task = asyncio.create_task(self.run(job, body))
        self.changed()
        return job

    def new_record(self, job, **values):
        row = self.store.create_request({'id': uid('labreq'), 'job_id': job['id'], 'kind': job['kind'], 'created_at': utcnow(), **values})
        job['record_ids'].append(row['id'])
        self.store.save_run(job)
        self.changed()
        return row

    async def update(self, row_id, update):
        row = self.store.request(row_id)
        row.update(update)
        if update['status'] in ('completed', 'failed'):
            row['finished_at'] = utcnow()
        self.store.save_request(row)
        if update['status'] == 'completed':
            self.providers.jev_verified = True
            self.active['completed'] += 1
        self.changed()

    async def run_case(self, job, body):
        case = body.test_case.model_dump(exclude_none=True)
        row = self.new_record(job, status='generating' if body.action == 'generate' else 'evaluating', input=case['input'],
                              test_case=case, rubric=body.rubric.model_dump(exclude_none=True), scenario=body.scenario, ticket_id=body.ticket_id,
                              source='Fresh Model generation' if body.action == 'generate' else 'User-supplied or edited answer; Jev evaluation only')
        try:
            if body.action == 'generate':
                job['phase'] = 'Model is answering'
                generated = await self.providers.generate({'input': case['input']}, body.scenario)
                case['actual_output'] = generated['answer']
                case['retrieval_context'] = [generated['generation_context']]
                row.update(generated, test_case=case, status='generated', finished_at=utcnow())
            else:
                job['phase'] = 'DeepEval is calling Jev'
                fixture = next((t for t in TICKETS if t['id'] == body.ticket_id and t['input'] == case['input']), None)
                if fixture:
                    for label, text in controls([fixture])[fixture['id']].items():
                        if case['actual_output'] == text:
                            row.update(source=f'Fixed-text {"reference" if label == "good" else "policy-error"} control; fresh Jev evaluation (not Model)',
                                       control_kind=label, control_text=text)
                if body.source_record_id:
                    origin = self.store.request(body.source_record_id) or self.monitor.store.request(body.source_record_id)
                    if origin and origin.get('generator_response') and origin.get('answer') == case['actual_output'] and origin.get('input') == case['input']:
                        row.update(source='Saved Model answer; fresh Jev evaluation', generation_record_id=origin['id'],
                                   generation_response_sha256=origin['generator_response_sha256'])
                self.store.save_request(row)
                row.update(await evaluate_case(case, body.rubric, self.providers.jev_key), status='completed', finished_at=utcnow())
                self.providers.jev_verified = True
            job['completed'] = 1
        except Exception as exc:
            row.update(status='failed', error=str(exc) if isinstance(exc, ProviderError) else f'Provider operation failed ({type(exc).__name__}).', finished_at=utcnow())
            job['error'] = row['error']
        finally:
            self.store.save_request(row)
            self.changed()

    async def run_dataset(self, job, body):
        # Fixed ordering: 5 covers one question in each category, 10 and 20 add difficulty.
        job['tickets'] = TICKETS[:body.count]
        job['dataset_version'] = 'support-synthetic-v1'
        job['dataset_sha256'] = text_hash(canonical(job['tickets']))
        job['policy_version'] = POLICY_VERSION
        job['native_results'] = {}
        loop = asyncio.get_running_loop()
        goldens = [{'input': t['input'], 'expected_output': t['expected_output'], 'context': [POLICY]} for t in job['tickets']]
        job['goldens'] = goldens
        for variant, scenario in [('A', 'healthy'), ('B', body.candidate)]:
            if self.stop.is_set():
                break
            job['phase'] = f'Generating variant {variant}'
            self.store.save_run(job)
            self.changed()
            variant_rows = []
            for t in job['tickets']:
                if self.stop.is_set():
                    break
                case = {'input': t['input'], 'expected_output': t['expected_output'], 'context': [POLICY]}
                row = self.new_record(job, variant=variant, scenario=scenario, status='generating', input=t['input'], ticket_id=t['id'], category=t['category'],
                                      source='Fresh Model generation and native DeepEval dataset evaluation', rubric=body.rubric.model_dump(exclude_none=True), test_case=case)
                try:
                    generated = await self.providers.generate(t, scenario)
                    case.update(actual_output=generated['answer'], retrieval_context=[generated['generation_context']])
                    row.update(generated, test_case=case, status='generated')
                except Exception as exc:
                    row.update(status='failed', error=str(exc) if isinstance(exc, ProviderError) else f'Model failed ({type(exc).__name__}).', finished_at=utcnow())
                    self.store.save_request(row)
                    raise ProviderError('Model generation failed; the dataset gate is incomplete.') from None
                self.store.save_request(row)
                variant_rows.append(row)
                self.changed()
            if self.stop.is_set():
                break
            job['phase'] = f'Native evaluate() · variant {variant}'
            self.changed()

            def emit(record_id, update):
                asyncio.run_coroutine_threadsafe(self.update(record_id, update), loop).result(timeout=20)

            native_cases = [{**r['test_case'], 'name': r['id']} for r in variant_rows]
            job['native_results'][variant] = await asyncio.to_thread(evaluate_dataset, native_cases, goldens, body.rubric, self.providers.jev_key, job['id'] + '-' + variant, emit, self.stop)
            # Cross-check native result objects against the captured per-case evidence.
            for result in job['native_results'][variant]['test_results']:
                row = self.store.request(result['name'])
                metric = result['metrics_data'][0]
                if row is None or metric['score'] != row.get('quality') or result['success'] != row.get('passed'):
                    raise ProviderError('Native dataset results did not match captured evidence; the release gate is incomplete.')
            self.store.save_run(job)
            self.changed()

    async def run(self, job, body):
        try:
            await (self.run_dataset(job, body) if job['kind'] == 'dataset' else self.run_case(job, body))
            job['status'] = 'stopped' if self.stop.is_set() else 'completed' if job['completed'] == job['planned'] else 'failed'
        except asyncio.CancelledError:
            job['status'] = 'interrupted'
            raise
        except Exception as exc:
            job['status'] = 'stopped' if self.stop.is_set() else 'failed'
            job['error'] = str(exc) if isinstance(exc, ProviderError) else f'Experiment stopped ({type(exc).__name__}). Partial evidence is retained.'
        finally:
            job['finished_at'] = utcnow()
            if job['kind'] == 'dataset' and job.get('tickets'):
                job['summary'] = dataset_summary([self.store.request(i) for i in job['record_ids']], job)
            self.store.save_run(job)
            self.active = None
            self.changed()
            self.monitor.changed()

    async def close(self):
        self.stop.set()
        if self.task and not self.task.done():
            with contextlib.suppress(Exception):
                await self.task
        self.store.close()


def lab(request):
    return request.app.state.lab


@router.get('/state')
async def state(request: Request):
    return lab(request).snapshot()


@router.get('/catalog')
async def catalog(request: Request):
    monitor = lab(request).monitor
    return {'presets': PRESETS, 'tickets': TICKETS, 'controls': controls(TICKETS), 'policy': POLICY, 'policy_version': POLICY_VERSION,
            'trace_options': [{'id': r['id'], 'input': r['input'], 'quality': r['quality'], 'scenario': r['scenario']} for r in monitor.store.requests() if scored(r)][-100:],
            'sources': [{'label': 'DeepEval JevEval', 'url': 'https://deepeval.com/docs/metrics-jev-eval'},
                        {'label': 'DeepEval test cases', 'url': 'https://deepeval.com/docs/evaluation-test-cases'},
                        {'label': 'DeepEval datasets', 'url': 'https://deepeval.com/docs/evaluation-datasets'},
                        {'label': 'TypeSafe documentation', 'url': 'https://docs.typesafe.ai'}]}


@router.get('/events')
async def events(request: Request):
    controller = lab(request)
    async def generate():
        queue = asyncio.Queue(maxsize=1)
        controller.listeners.add(queue)
        try:
            yield 'event: state\ndata: ' + json.dumps(controller.snapshot()) + '\n\n'
            while not await request.is_disconnected():
                try:
                    await asyncio.wait_for(queue.get(), timeout=10)
                    yield 'event: state\ndata: ' + json.dumps(controller.snapshot()) + '\n\n'
                except asyncio.TimeoutError:
                    yield 'event: heartbeat\ndata: {}\n\n'
        finally:
            controller.listeners.discard(queue)
    return StreamingResponse(generate(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})


@router.post('/case')
async def start_case(body: CaseAction, request: Request):
    return lab(request).start(body)


@router.post('/dataset')
async def start_dataset(body: DatasetAction, request: Request):
    return lab(request).start(body)


@router.post('/stop')
async def stop(request: Request):
    controller = lab(request)
    controller.stop.set()
    if controller.active:
        controller.active['status'] = 'stopping'
        controller.changed()
    return {'message': 'The current provider call will finish; no further calls will start.'}


@router.get('/records/{record_id}')
async def detail(record_id: str, request: Request):
    row = lab(request).store.request(record_id)
    if row is None:
        raise HTTPException(404, 'Lab record not found.')
    return {**row, 'python': python_example(row['test_case'], Rubric(**row['rubric']))}


@router.get('/jobs/{job_id}')
async def job(job_id: str, request: Request):
    value = next((j for j in lab(request).store.runs() if j['id'] == job_id), None)
    if value is None:
        raise HTTPException(404, 'Experiment not found.')
    return value


@router.get('/import/{record_id}')
async def import_trace(record_id: str, request: Request):
    row = lab(request).monitor.store.request(record_id)
    if row is None or not row.get('answer'):
        raise HTTPException(404, 'An answered monitor trace is required.')
    return {'id': row['id'], 'ticket_id': row['ticket_id'], 'scenario': row['scenario'], 'test_case': {
        'input': row['input'], 'actual_output': row['answer'], 'expected_output': row.get('expected_output'),
        'context': [POLICY], 'retrieval_context': [row['generation_context']]}}


@router.post('/replay')
async def replay_result(body: ReplayAction, request: Request):
    row = lab(request).store.request(body.record_id)
    if row is None:
        raise HTTPException(404, 'Lab record not found.')
    try:
        return replay(row, body.weights, body.threshold, body.strict_mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


def drift_data(controller, body):
    all_rows = controller.monitor.store.requests()
    if body.source == 'proof':
        proof = controller.monitor.store.get('proof') or {}
        stages = proof.get('stages', [])
        ids = next((s['request_ids'] for s in stages if s['phase'] == 'baseline'), [])
        tail_ids = {i for s in stages if s['phase'] != 'baseline' for i in s['request_ids']}
        candidates = [r for r in all_rows if r['id'] in tail_ids]
        source_id = proof.get('run_id')
    else:
        reference = controller.monitor.store.get('baseline') or {}
        ids = reference.get('request_ids', [])
        last = max((r['seq'] for r in all_rows if r['id'] in ids), default=0)
        candidates = [r for r in all_rows if r['seq'] > last]
        source_id = reference.get('id')
    baseline = [r for r in all_rows if r['id'] in ids and scored(r)]
    timeline = [r for r in candidates if scored(r)]
    cutoff = min(len(timeline), body.cutoff if body.cutoff is not None else len(timeline))
    current = timeline[max(0, cutoff - body.window):cutoff]
    ready = len(baseline) >= 20 and len(current) == body.window
    reference_mean = mean(r['quality'] for r in baseline) if baseline else None
    current_mean = mean(r['quality'] for r in current) if current else None
    drop = reference_mean - current_mean if ready else None
    js = js_divergence([r['category'] for r in baseline], [r['category'] for r in current]) if ready else None
    status = 'insufficient' if not ready else 'alert' if at_least(drop, body.alert_drop) else 'watch' if at_least(drop, body.watch_drop) else 'stable'
    first_alert = None
    rolling = []
    for n in range(1, len(timeline) + 1):
        if n >= body.window and len(baseline) >= 20:
            value = mean(r['quality'] for r in timeline[n-body.window:n])
            rolling.append({'index': n, 'quality': value})
            if first_alert is None and n <= cutoff and at_least(reference_mean - value, body.alert_drop):
                first_alert = {'index': n, 'request_id': timeline[n-1]['id'], 'finished_at': timeline[n-1]['finished_at']}
    return {'source': 'Saved real monitor evidence; no provider calls', 'source_id': source_id,
            'options': body.model_dump(), 'cutoff': cutoff, 'total': len(timeline), 'excluded_errors': len(candidates) - len(timeline),
            'baseline_ids': [r['id'] for r in baseline], 'current_ids': [r['id'] for r in current],
            'reference_count': len(baseline), 'current_count': len(current), 'reference_mean': reference_mean, 'current_mean': current_mean,
            'quality_drop': drop, 'quality_status': status, 'mix_js': js,
            'mix_status': 'insufficient' if not ready else 'alert' if at_least(js, .2) else 'watch' if at_least(js, .1) else 'stable',
            'ks_distance': ks_distance([r['quality'] for r in baseline], [r['quality'] for r in current]) if ready else None,
            'current_pass_rate': mean(float(r['quality'] >= body.threshold) for r in current) if current else None,
            'first_alert': first_alert, 'rolling': rolling,
            'timeline': [{k: r[k] for k in ('id', 'phase', 'scenario', 'category', 'quality', 'finished_at')} for r in timeline],
            'method': 'Frozen 20+ sample reference; a configurable last-N completed window. Operational thresholds, no significance test. Minimum current sample count equals the selected window in this replay only.'}


@router.post('/drift')
async def drift(body: DriftAction, request: Request):
    return drift_data(lab(request), body)


@router.get('/export')
async def export(request: Request):
    controller = lab(request)
    return JSONResponse(controller.export(),
                        headers={'Content-Disposition': 'attachment; filename="generator-jev-evaluation-lab.json"'})


@router.get('/verify')
async def verify_evidence(request: Request):
    try:
        return verify(lab(request).export())
    except (ValueError, KeyError, TypeError) as exc:
        return {'status': 'failed', 'verified_at': utcnow(),
                'error': str(exc) if isinstance(exc, ValueError) else f'Evidence structure was incomplete ({type(exc).__name__}).'}


@router.get('/code/{record_id}')
async def code(record_id: str, request: Request):
    row = lab(request).store.request(record_id)
    if not row:
        raise HTTPException(404, 'Lab record not found.')
    return PlainTextResponse(python_example(row['test_case'], Rubric(**row['rubric'])), headers={'Content-Disposition': f'attachment; filename="{record_id}.py"'})
