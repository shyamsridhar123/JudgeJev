"""Run one fresh native Jev evaluation of a public fictional case with your key."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', default='shipping-bad', help='Record ID from demo/evidence.json')
    parser.add_argument('--list', action='store_true', help='List available cases without importing provider SDKs.')
    parser.add_argument('--output', type=Path, help='Save the fresh result locally. Use the ignored data/ directory.')
    args = parser.parse_args()
    rows = json.loads((ROOT / 'demo/evidence.json').read_text(encoding='utf-8'))['records']
    if args.list:
        for row in rows:
            print(row['id'] + ': ' + row['title'])
        return
    row = next((r for r in rows if r['id'] == args.case), None)
    if row is None:
        parser.error('Unknown case; use --list to inspect available IDs.')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    key = os.getenv('TYPESAFE_API_KEY') or os.getenv('JEV_API_KEY')
    if not key:
        parser.exit(2, 'Set TYPESAFE_API_KEY in your environment or .env. No provider call was made.\n')
    from lab_engine import evaluate_case
    from lab_schema import Rubric
    from providers import ProviderError
    try:
        result = asyncio.run(evaluate_case(row['test_case'], Rubric(**row['rubric']), key))
    except ProviderError as exc:
        parser.exit(1, str(exc) + '\n')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'source_case': row['id'], 'test_case': row['test_case'], 'rubric': row['rubric'], **result}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    verdict = 'not applicable' if not result['applicable_count'] else 'pass' if result['passed'] else 'fail'
    print(json.dumps({'source': 'fresh native Jev evaluation', 'case': row['id'], 'verdict': verdict,
                     **{k: result[k] for k in ('quality', 'effective_threshold', 'applicable_count', 'judge_model', 'jev_usage', 'jev_request_sha256', 'jev_response_sha256', 'breakdown')}}, indent=2))
    # A failed criterion is a useful result and a failing CI exit status.
    raise SystemExit(0 if verdict == 'pass' else 1)


if __name__ == '__main__':
    main()
