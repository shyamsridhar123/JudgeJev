"""Independent lab evidence audit, using only Python's standard library.

No imports from DeepEval, the app, providers, fixtures, or its calculation code.
This verifies the recorded chain and arithmetic, not human accuracy or signatures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def average(values):
    return math.fsum(values) / len(values) if values else None


class Audit:
    def __init__(self):
        self.checks = 0

    def require(self, condition, label):
        self.checks += 1
        if not condition:
            raise ValueError('Evidence check failed: ' + label)

    def equal(self, actual, expected, label):
        if isinstance(expected, dict):
            self.require(isinstance(actual, dict), label + ' object')
            self.require(set(actual) == set(expected), label + ' exact keys')
            for key in expected:
                self.equal(actual[key], expected[key], label + '.' + str(key))
        elif isinstance(expected, (list, tuple)):
            self.require(isinstance(actual, (list, tuple)), label + ' list')
            self.require(len(actual) == len(expected), label + ' length')
            for i, (a, b) in enumerate(zip(actual, expected)):
                self.equal(a, b, label + f'[{i}]')
        elif isinstance(expected, float):
            self.require(isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(actual)
                         and math.isclose(actual, expected, abs_tol=1e-9, rel_tol=1e-10), label)
        else:
            self.require(actual == expected, label)

    def number(self, value, low, high, label):
        self.require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                     and low <= value <= high, label)

    def generation(self, row):
        tag = row['id']
        response, request = row['generator_response'], row['generator_request']
        self.equal(json.loads(row['generator_response_raw']), response, tag + ' raw Model JSON')
        self.equal(digest(row['generator_response_raw']), row['generator_response_sha256'], tag + ' raw Model bytes hash')
        self.require(isinstance(response['model'], str) and bool(response['model']), tag + ' returned generator model ID')
        self.equal(response['status'], 'completed', tag + ' completed Model response')
        self.equal(request['model'], response['model'], tag + ' requested generator model')
        self.equal(row['generator_model'], response['model'], tag + ' recorded generator model')
        answer = '\n'.join(p['text'] for item in response['output'] if item.get('type') == 'message'
                           for p in item.get('content', []) if p.get('type') == 'output_text' and p.get('text'))
        self.require(bool(answer.strip()), tag + ' nonempty generated answer')
        self.equal(row['answer'], answer, tag + ' extracted answer')
        if row.get('test_case'):
            self.equal(row['test_case']['actual_output'], answer, tag + ' generated test case output')
            self.equal(row['test_case']['retrieval_context'], [row['generation_context']], tag + ' actual retrieval evidence')
        self.equal(request['input'][1]['content'][0]['text'], row['input'], tag + ' generation input')
        self.require(request['input'][0]['content'][0]['text'].endswith(row['generation_context']), tag + ' generation context')
        self.equal(digest(row['generation_context']), row['generation_context_sha256'], tag + ' generation context hash')
        self.equal(row['generator_usage'], {k: response['usage'].get(k) for k in ('input_tokens', 'output_tokens', 'total_tokens')}, tag + ' Model token usage')
        self.number(row['generator_ms'], .001, 180000, tag + ' observed generation duration')

    def evaluation(self, row):
        tag = row['id']
        config, case = row['rubric'], row['test_case']
        response, request = row['jev_response'], row['jev_request']
        self.equal(row['config_sha256'], digest(canonical(config)), tag + ' rubric hash')
        self.equal(row['jev_request_sha256'], digest(canonical(request)), tag + ' Jev request hash')
        self.equal(row['jev_response_sha256'], digest(canonical(response)), tag + ' Jev response hash')
        self.equal(response['model'], 'jev-1.13.0', tag + ' returned Jev model')
        self.equal(row['judge_model'], response['model'], tag + ' recorded Jev model')
        self.equal(request['model'], response['model'], tag + ' requested Jev model')
        self.equal(row['input'], case['input'], tag + ' evaluated input')
        self.equal(request['state'], {'test_case': {k: case[k] for k in config['evaluation_params']}}, tag + ' exact selected evidence fields')
        self.equal(set(request['questions']), {f'q_{i}' for i in range(len(config['questions']))}, tag + ' question keys')
        self.equal(set(response['answers']), set(request['questions']), tag + ' returned answer keys')
        self.equal(len(row['breakdown']), len(config['questions']), tag + ' number of outcomes')
        expected_outcomes = []
        for i, (q, b) in enumerate(zip(config['questions'], row['breakdown'])):
            key = f'q_{i}'
            label = tag + '.' + key
            raw = response['answers'][key]
            req = {'type': q['type'], 'instructions': q['question']}
            if q['type'] == 'score':
                req['criteria'] = q['levels']
            elif q['type'] == 'choice':
                req['criteria'] = {name: None for name in q['options']}
            self.equal(request['questions'][key], req, label + ' typed rubric request')
            self.equal(raw['type'], q['type'], label + ' returned type')
            self.equal(b['question'], q['question'], label + ' question text')
            self.equal(b['weight'], float(q['weight']), label + ' weight')
            self.equal(b['type'], q['type'], label + ' question type')
            self.number(q['weight'], .1, 10, label + ' allowed weight')
            applicable = True
            if q['type'] == 'noul':
                value = raw['noul']
                self.number(value, 0, 1, label + ' Noul probability')
                probs = {'true': value, 'false': 1 - value}
                confidence = abs(2 * value - 1)
                strict_pass = value >= .5
            elif q['type'] == 'score':
                top = len(q['levels']) - 1
                self.equal(raw['legend'], {str(n): level for n, level in enumerate(q['levels'])}, label + ' ordinal legend')
                self.equal(set(raw['probabilities']), set(raw['legend']), label + ' ordinal levels returned')
                self.number(raw['score'], 0, top, label + ' returned expected ordinal level')
                probs = {q['levels'][int(k)]: p for k, p in raw['probabilities'].items()}
                value = min(1., max(0., raw['score'] / top))
                rounded_expectation = math.fsum(int(k) * p for k, p in raw['probabilities'].items())
                tolerance = .005 * sum(range(top + 1)) + .005 + 1e-9
                self.require(abs(rounded_expectation - raw['score']) <= tolerance, label + ' expected level within independent rounding bound')
                confidence = raw['confidence']
                strict_pass = max(probs, key=probs.get) == q['levels'][-1]
            else:
                self.equal(set(raw['probabilities']), set(q['options']), label + ' Choice options')
                probs = {name: raw['probabilities'].get(name, 0.) for name in q['options']}
                credits = {name: credit for name, credit in q['options'].items() if credit is not None}
                na_mass = math.fsum(probs[name] for name, credit in q['options'].items() if credit is None)
                mass = math.fsum(probs[name] for name in credits)
                applicable = na_mass < .5 and mass > 0
                value = math.fsum(probs[name] * credit for name, credit in credits.items()) / mass if applicable else None
                confidence = raw['confidence']
                strict_pass = credits[max(credits, key=lambda name: probs[name])] >= 1. if applicable else None
            for p in probs.values():
                self.number(p, 0, 1, label + ' probability in range')
            self.require(abs(math.fsum(probs.values()) - 1) <= .003000001, label + ' accepted probability mass')
            self.equal(b['probabilities'], probs, label + ' exact probabilities translated')
            self.equal(b['applicable'], applicable, label + ' applicability')
            self.equal(b['value'], value, label + ' value from raw response')
            self.equal(b['confidence'], confidence, label + ' confidence')
            self.equal(b.get('passed'), strict_pass if config['strict_mode'] and applicable else None, label + ' native strict flag')
            expected_outcomes.append({'value': value, 'weight': q['weight'], 'applicable': applicable, 'strict': strict_pass, 'confidence': confidence})
        applicable = [b for b in expected_outcomes if b['applicable']]
        normal = math.fsum(b['value'] * b['weight'] for b in applicable) / math.fsum(b['weight'] for b in applicable) if applicable else 1.
        strict = 1. if all(b['strict'] for b in applicable) else 0.
        quality = strict if config['strict_mode'] else normal
        threshold = 1. if config['strict_mode'] else config['threshold']
        self.equal(row['quality'], quality, tag + ' native score')
        self.equal(row['weighted_score'], normal, tag + ' weighted mean')
        self.equal(row['strict_score'], strict, tag + ' native strict result')
        self.equal(row['passed'], quality >= threshold, tag + ' threshold verdict')
        self.equal(row['effective_threshold'], threshold, tag + ' strict threshold override')
        self.equal(row['applicable_count'], len(applicable), tag + ' applicable criterion count')
        self.equal(row['confidence'], min(b['confidence'] for b in expected_outcomes if b['confidence'] is not None), tag + ' minimum confidence')
        self.equal(row['metrics'], {q['key']: b['value'] for q, b in zip(config['questions'], expected_outcomes)}, tag + ' named metrics')
        self.equal(row['jev_usage'], response['usage'], tag + ' Jev token usage')
        self.number(row['eval_ms'], .001, 180000, tag + ' observed evaluation duration')

    def dataset(self, job, by_id):
        tag = job['id']
        rows = [by_id[i] for i in job['record_ids']]
        self.equal(job['dataset_sha256'], digest(canonical(job['tickets'])), tag + ' fixed dataset hash')
        self.equal(job['planned'], job['options']['count'] * 2, tag + ' planned paired evaluations')
        self.equal(len(job['tickets']), job['options']['count'], tag + ' golden count')
        self.equal(job['completed'], sum(r['status'] == 'completed' for r in rows), tag + ' completed count')
        pair_count = 0
        complete_a, complete_b = [], []
        for ticket, pair in zip(job['tickets'], job['summary']['pairs']):
            matches = [r for r in rows if r['ticket_id'] == ticket['id']]
            self.require(len({r['variant'] for r in matches}) == len(matches), tag + ' unique variant per ticket')
            a = next((r for r in matches if r['variant'] == 'A'), None)
            b = next((r for r in matches if r['variant'] == 'B'), None)
            ready = bool(a and b and all(r['status'] == 'completed' and r['applicable_count'] > 0 for r in (a, b)))
            for row in matches:
                self.equal(row['rubric'], job['options']['rubric'], tag + ' shared rubric')
                self.equal(row['scenario'], 'healthy' if row['variant'] == 'A' else job['options']['candidate'], tag + ' controlled variant')
                self.equal(row['input'], ticket['input'], tag + ' shared input')
                self.equal(row['test_case']['expected_output'], ticket['expected_output'], tag + ' shared expected behavior')
                self.equal(row['test_case']['context'], job['goldens'][job['tickets'].index(ticket)]['context'], tag + ' shared reference context')
            expected = {'ticket_id': ticket['id'], 'category': ticket['category'], 'input': ticket['input'],
                        'a_id': a['id'] if a else None, 'b_id': b['id'] if b else None,
                        'a': a.get('quality') if a else None, 'b': b.get('quality') if b else None,
                        'b_passed': b.get('passed') if b else None, 'complete': ready,
                        'delta': b['quality'] - a['quality'] if ready else None}
            self.equal(pair, expected, tag + ' exact paired result')
            if ready:
                pair_count += 1
                complete_a.append(a)
                complete_b.append(b)
        summary = job['summary']
        self.equal(len(summary['pairs']), len(job['tickets']), tag + ' complete pair listing')
        a_mean = average([r['quality'] for r in complete_a])
        b_mean = average([r['quality'] for r in complete_b])
        rate = average([float(r['passed']) for r in complete_b])
        drop = a_mean - b_mean if pair_count else None
        eligible = pair_count == len(job['tickets']) and job['status'] == 'completed' and len(job.get('native_results', {})) == 2
        gate_passes = [eligible, rate >= job['options']['minimum_pass_rate'] if eligible else None,
                       drop <= job['options']['max_mean_drop'] + 1e-12 if eligible else None]
        self.equal(summary['complete_pairs'], pair_count, tag + ' usable pair count')
        self.equal(summary['a_mean'], a_mean, tag + ' reference mean')
        self.equal(summary['b_mean'], b_mean, tag + ' candidate mean')
        self.equal(summary['candidate_pass_rate'], rate, tag + ' candidate pass rate')
        self.equal(summary['mean_drop'], drop, tag + ' mean drop')
        self.equal(summary['regressions'], sum(b['quality'] - a['quality'] < -.05 for a, b in zip(complete_a, complete_b)), tag + ' regressions')
        self.equal([g['passed'] for g in summary['gates']], gate_passes, tag + ' release conditions')
        self.equal(summary['release_gate'], 'pass' if eligible and all(gate_passes) else 'block' if eligible else 'incomplete', tag + ' release verdict')
        for variant, native in job.get('native_results', {}).items():
            variant_rows = [r for r in rows if r['variant'] == variant and r['status'] == 'completed']
            self.equal({r['name'] for r in native['test_results']}, {r['id'] for r in variant_rows}, tag + ' native test coverage')
            self.equal(len(native['test_results']), len(variant_rows), tag + ' no duplicate native results')
            self.equal(native.get('confident_link'), None, tag + ' local-only native result')
            for result in native['test_results']:
                row = by_id[result['name']]
                self.equal(result['input'], row['input'], tag + ' native input')
                self.equal(result['actual_output'], row['test_case']['actual_output'], tag + ' native answer')
                self.equal(result['success'], row['passed'], tag + ' native success')
                self.equal(len(result['metrics_data']), 1, tag + ' one native metric')
                metric = result['metrics_data'][0]
                self.equal(metric['score'], row['quality'], tag + ' native metric score')
                self.equal(metric['success'], row['passed'], tag + ' native metric verdict')
                self.equal(metric['threshold'], row['effective_threshold'], tag + ' native metric threshold')
                self.equal(metric.get('error'), None, tag + ' no hidden native metric error')


def verify(data):
    audit = Audit()
    audit.equal(data['schema_version'], 2, 'lab export schema')
    rows = data['requests']
    audit.equal(len({r['id'] for r in rows}), len(rows), 'unique lab record IDs')
    by_id = {r['id']: r for r in rows}
    sources = {r['id']: r for r in data.get('generation_sources', [])}
    generations = [r for r in rows if r.get('generator_response')]
    evaluated = [r for r in rows if r['status'] == 'completed']
    audit.equal(len({r['generator_response']['id'] for r in generations}), len(generations), 'unique generated Model response IDs')
    for row in rows:
        if row.get('generator_response'):
            audit.generation(row)
        if row['status'] == 'completed':
            audit.evaluation(row)
        else:
            audit.equal(row.get('quality'), None, row['id'] + ' no score without completed evaluation')
        if row.get('generation_record_id'):
            origin = by_id.get(row['generation_record_id']) or sources.get(row['generation_record_id'])
            audit.require(bool(origin), row['id'] + ' generation source exists')
            audit.equal(row['generation_response_sha256'], origin['generator_response_sha256'], row['id'] + ' linked generation hash')
            audit.equal(row['test_case']['actual_output'], origin['answer'], row['id'] + ' linked exact answer')
            audit.equal(row['test_case']['input'], origin['input'], row['id'] + ' linked exact input')
        if row.get('control_kind'):
            audit.equal(row['test_case']['actual_output'], row['control_text'], row['id'] + ' exact fixed control')
            audit.require('Fixed-text' in row['source'] and 'not Model' in row['source'], row['id'] + ' explicit control provenance')
            audit.require(not row.get('generation_record_id') and not row.get('generator_response'), row['id'] + ' no invented control generation')
    for source in sources.values():
        audit.generation(source)
    datasets = [j for j in data['jobs'] if j['kind'] == 'dataset' and j.get('summary')]
    for job in data['jobs']:
        audit.require(all(i in by_id for i in job['record_ids']), job['id'] + ' all records retained')
        audit.equal(len(set(job['record_ids'])), len(job['record_ids']), job['id'] + ' unique record membership')
        for i in job['record_ids']:
            audit.equal(by_id[i]['job_id'], job['id'], job['id'] + ' record job link')
    for job in datasets:
        audit.dataset(job, by_id)
    report = {'status': 'passed' if evaluated else 'empty', 'verified_at': datetime.now(timezone.utc).isoformat(),
              'checks': audit.checks, 'content_sha256': digest(canonical({k: v for k, v in data.items() if k != 'exported_at'})),
              'counts': {'records': len(rows), 'generator': len(generations), 'evaluations': len(evaluated),
                         'datasets': sum(j['status'] == 'completed' for j in datasets),
                         'failed_or_stopped_datasets': sum(j['status'] != 'completed' for j in datasets)},
              'coverage': {'question_types': sorted({b['type'] for r in evaluated for b in r['breakdown']}),
                           'fresh_strict_evaluations': sum(r['rubric']['strict_mode'] for r in evaluated),
                           'all_inapplicable_evaluations': sum(r['applicable_count'] == 0 for r in evaluated)},
              'scope': 'Raw provider payloads and hashes, selected evidence fields, question semantics, aggregation, strict mode, threshold decisions, generation links, paired gates, and native EvaluationResult consistency.',
              'limitations': 'Recorded provenance and arithmetic only. Hashes are not provider signatures. No human-rated accuracy or calibration claim. Live runs may have unfinished rows; these receive no fallback scores.'}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = verify(json.loads(args.export.read_text(encoding='utf-8')))
    encoded = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(encoded, encoding='utf-8')
    print(encoded)
