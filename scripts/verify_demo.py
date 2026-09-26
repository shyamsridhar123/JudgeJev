"""Audit the public evidence with only the Python standard library; no API calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fixtures import OUTDATED_POLICY, POLICY, TICKETS
from lab_examples import BAD, GOOD
from verify_lab import Audit

ROW_FIELDS = set("id title preset ticket_id category captured_date answer_source test_case rubric status input quality weighted_score strict_score passed effective_threshold applicable_count breakdown confidence eval_ms judge_model metrics jev_request jev_response jev_request_sha256 jev_response_sha256 jev_usage config_sha256".split())


def audit_bundle(data):
    audit = Audit()
    audit.equal(data['schema_version'], 1, 'public schema version')
    audit.equal(data['mode'], 'recorded', 'public replay mode')
    audit.equal(data['policy'], POLICY, 'public fictional reference')
    rows = data['records']
    audit.require(bool(rows), 'nonempty evidence')
    audit.require(len({r['id'] for r in rows}) == len(rows), 'unique public record IDs')
    tickets = {t['id']: t for t in TICKETS}
    for row in rows:
        tag = row['id']
        audit.equal(set(row), ROW_FIELDS, tag + ' publication field allowlist')
        audit.equal(row['status'], 'completed', tag + ' completed record')
        audit.equal(set(row['jev_response']), {'model', 'usage', 'answers'}, tag + ' response field allowlist')
        audit.equal(set(row['jev_request']), {'model', 'state', 'questions'}, tag + ' request field allowlist')
        audit.require(row['ticket_id'] in tickets, tag + ' known fictional ticket')
        ticket = tickets[row['ticket_id']]
        audit.equal(row['input'], ticket['input'], tag + ' synthetic input')
        audit.equal(row['category'], ticket['category'], tag + ' synthetic category')
        audit.equal(row['test_case']['expected_output'], ticket['expected_output'], tag + ' expected behavior')
        audit.equal([c.rstrip() for c in row['test_case']['context']], [POLICY.rstrip()], tag + ' authoritative policy')
        audit.require(all(c.rstrip() in (POLICY.rstrip(), OUTDATED_POLICY.rstrip()) for c in row['test_case']['retrieval_context']), tag + ' public retrieved policy')
        audit.require(row['captured_date'] in data['captured_dates'], tag + ' capture date')
        if row['answer_source'] == 'Human-authored fictional control':
            audit.require(row['test_case']['actual_output'] in (GOOD[ticket['id']], BAD[ticket['category']]), tag + ' fixed control text')
        else:
            audit.equal(row['answer_source'], 'Recorded model answer to a fictional support ticket; generator metadata omitted', tag + ' answer provenance')
        audit.evaluation(row)
    by_id = {r['id']: r for r in rows}
    audit.equal(len(data['pairs']), 5, 'five recorded pairs')
    audit.require(len({p['ticket_id'] for p in data['pairs']}) == len(data['pairs']), 'unique pair tickets')
    for pair in data['pairs']:
        audit.require(pair['reference_id'] in by_id and pair['candidate_id'] in by_id, 'complete pair coverage')
        a, b = by_id[pair['reference_id']], by_id[pair['candidate_id']]
        audit.require(a['applicable_count'] > 0 and b['applicable_count'] > 0, 'applicable paired evidence')
        audit.equal(a['ticket_id'], pair['ticket_id'], 'reference ticket')
        audit.equal(b['ticket_id'], pair['ticket_id'], 'candidate ticket')
        audit.equal(a['category'], pair['category'], 'paired category')
        audit.equal(a['rubric'], b['rubric'], 'same paired rubric')
        for field in ('input', 'expected_output', 'context'):
            audit.equal(a['test_case'][field], b['test_case'][field], 'same paired ' + field)
        audit.equal([c.rstrip() for c in a['test_case']['retrieval_context']], [POLICY.rstrip()], 'current-policy reference')
        audit.equal([c.rstrip() for c in b['test_case']['retrieval_context']], [OUTDATED_POLICY.rstrip()], 'obsolete-policy candidate')
    replay = data['replay']
    audit.equal(replay['kind'], 'reconstructed', 'replay provenance')
    audit.equal(replay['reference_ids'], [p['reference_id'] for p in data['pairs']], 'replay reference coverage')
    audit.equal(replay['fault_ids'], [p['candidate_id'] for p in data['pairs']], 'replay fault coverage')
    audit.equal(replay['recovery_ids'], replay['reference_ids'], 'explicit reference reuse')
    return {'status': 'passed', 'records': len(rows), 'audit_checks': audit.checks}


def verify_file(path, manifest_path=None):
    raw = Path(path).read_bytes()
    result = audit_bundle(json.loads(raw))
    result['evidence_sha256'] = hashlib.sha256(raw).hexdigest()
    if manifest_path is not None:
        manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
        if manifest != {k: result[k] for k in ('records', 'audit_checks', 'evidence_sha256')}:
            raise ValueError('Evidence check failed: published manifest differs from independent audit')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-manifest', action='store_true', help='Regenerate the manifest after an intentional, reviewed evidence change.')
    args = parser.parse_args()
    evidence = ROOT / 'demo/evidence.json'
    manifest = ROOT / 'demo/manifest.json'
    try:
        result = verify_file(evidence, None if args.write_manifest else manifest)
        if args.write_manifest:
            manifest.write_text(json.dumps({k: result[k] for k in ('records', 'audit_checks', 'evidence_sha256')}, indent=2) + '\n', encoding='utf-8', newline='\n')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(1, f'Audit rejected: {exc}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
