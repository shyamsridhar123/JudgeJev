"""Public native evidence is mandatory; corruption must fail closed."""
import copy
import json
from pathlib import Path

import pytest

from scripts.verify_demo import audit_bundle, verify_file
from verify_lab import canonical, digest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def evidence():
    return json.loads((ROOT / 'demo/evidence.json').read_text(encoding='utf-8'))


def test_all_public_evidence_and_manifest_reconcile():
    result = verify_file(ROOT / 'demo/evidence.json', ROOT / 'demo/manifest.json')
    assert result['status'] == 'passed'
    assert result['records'] == 17
    assert result['audit_checks'] > 2400


@pytest.mark.parametrize('defect', ['selected_field', 'raw_probability', 'score', 'choice_credit', 'threshold', 'duplicate', 'missing_pair', 'inapplicable_pair', 'private_field'])
def test_public_audit_rejects_corruption(evidence, defect):
    row = next(r for r in evidence['records'] if r['id'] == 'shipping-bad')
    if defect == 'selected_field':
        row['jev_request']['state']['test_case']['expected_output'] = 'unselected field'
        row['jev_request_sha256'] = digest(canonical(row['jev_request']))
    elif defect == 'raw_probability':
        row['jev_response']['answers']['q_0']['noul'] = .9
        row['jev_response_sha256'] = digest(canonical(row['jev_response']))
    elif defect == 'score':
        row['quality'] = .99
    elif defect == 'choice_credit':
        row = next(r for r in evidence['records'] if r['id'] == 'primitives-review')
        row['rubric']['questions'][2]['options']['Correctly routes for review'] = .1
        row['config_sha256'] = digest(canonical(row['rubric']))
    elif defect == 'threshold':
        row['passed'] = not row['passed']
    elif defect == 'duplicate':
        evidence['records'].append(copy.deepcopy(row))
    elif defect == 'missing_pair':
        evidence['pairs'][0]['candidate_id'] = 'missing'
    elif defect == 'inapplicable_pair':
        evidence['pairs'][1]['candidate_id'] = 'not-applicable'
    else:
        row['generator_endpoint'] = 'unexpected private metadata'
    with pytest.raises(ValueError, match='Evidence check failed'):
        audit_bundle(evidence)


def test_file_byte_change_invalidates_manifest(tmp_path):
    changed = tmp_path / 'evidence.json'
    changed.write_bytes((ROOT / 'demo/evidence.json').read_bytes() + b'\n')
    with pytest.raises(ValueError, match='manifest'):
        verify_file(changed, ROOT / 'demo/manifest.json')
