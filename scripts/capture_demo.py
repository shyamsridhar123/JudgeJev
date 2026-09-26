"""Capture NEW native Jev evaluations of allowlisted fictional controls.

Run a configured local lab first, then run this script. No key is read here.
This publishes only the synthetic cases below, never the lab's stored history.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixtures import POLICY, OUTDATED_POLICY, TICKETS
from lab_examples import GOOD, BAD
from verify_lab import Audit
from lab_schema import Case

FIELDS = (
    'test_case', 'rubric', 'status', 'input', 'quality', 'weighted_score',
    'strict_score', 'passed', 'effective_threshold', 'applicable_count',
    'breakdown', 'confidence', 'reason', 'eval_ms', 'judge_model', 'metrics',
    'jev_request', 'jev_response', 'jev_request_sha256', 'jev_response_sha256',
    'jev_usage', 'config_sha256', 'created_at', 'finished_at',
)


def capture(base_url, output):
    if urlsplit(base_url).hostname not in ('127.0.0.1', 'localhost'):
        raise SystemExit('Capture requires an explicitly configured loopback lab.')
    records = []
    with httpx.Client(base_url=base_url, timeout=60) as client:
        state = client.get('/api/lab/state').raise_for_status().json()
        if state['active'] or state['monitor_active']:
            raise SystemExit('An experiment is active. Let it finish before capture.')
        if not state['connection']['jev_configured']:
            raise SystemExit('Connect Jev in the local lab first.')
        catalog = client.get('/api/lab/catalog').raise_for_status().json()
        presets = {p['id']: p['rubric'] for p in catalog['presets']}
        plan = []
        for phase in ('reference', 'fault', 'recovery'):
            for ticket in TICKETS:
                answer = BAD[ticket['category']] if phase == 'fault' else GOOD[ticket['id']]
                plan.append((f'{phase}-{ticket["id"]}', phase, ticket, answer, 'support', POLICY))
        for preset, ticket_id, variant in (
            ('primitives', 1, 'good'), ('primitives', 11, 'bad'),
            ('escalation', 2, 'good'), ('escalation', 9, 'bad'),
            ('rag', 6, 'good'), ('rag', 6, 'bad'),
        ):
            ticket = TICKETS[ticket_id - 1]
            answer = GOOD[ticket['id']] if variant == 'good' else BAD[ticket['category']]
            context = OUTDATED_POLICY if preset == 'rag' and variant == 'bad' else POLICY
            plan.append((f'{preset}-{variant}', 'concept', ticket, answer, preset, context))

        for public_id, phase, ticket, answer, preset, retrieval in plan:
            case = dict(input=ticket['input'], actual_output=answer,
                        expected_output=ticket['expected_output'], context=[POLICY], retrieval_context=[retrieval])
            job = client.post('/api/lab/case', json=dict(action='evaluate', test_case=case,
                rubric=presets[preset], ticket_id=ticket['id'])).raise_for_status().json()
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                job = client.get('/api/lab/jobs/' + job['id']).raise_for_status().json()
                if job['status'] != 'running':
                    break
                time.sleep(.4)
            if job['status'] != 'completed' or len(job['record_ids']) != 1:
                raise RuntimeError('A real evaluation failed; no replacement score was created.')
            original = client.get('/api/lab/records/' + job['record_ids'][0]).raise_for_status().json()
            row = {k: original[k] for k in FIELDS}
            # Validate both the selected and unselected evidence before publication.
            if row['test_case'] != Case(**case).model_dump():
                raise RuntimeError('Captured case differs from the allowlisted fixture.')
            if set(row['jev_response']) - {'model', 'answers', 'usage'}:
                raise RuntimeError('Unexpected provider metadata. Inspect before publishing.')
            row.update(id=public_id, phase=phase, ticket_id=ticket['id'], category=ticket['category'],
                       preset=preset, source='Human-authored fictional control; real native DeepEval/Jev evaluation')
            Audit().evaluation(row)
            records.append(row)
            result = dict(schema_version=1, mode='recorded',
                captured_at=datetime.now(timezone.utc).isoformat(),
                provenance='Fresh native JevEval calls on human-authored fictional answers. No generated answers or private history.',
                versions=state['versions'], judge_model=state['connection']['jev_model'],
                policy=POLICY, records=records)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print(f'{len(records)}/{len(plan)} {public_id}: {row["quality"]:.4f} applicable={row["applicable_count"]}', flush=True)
    print(f'Captured {len(records)} real evaluations; no keys or original local record IDs exported.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8780')
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'synthetic-capture.json')
    args = parser.parse_args()
    capture(args.url, args.output)
