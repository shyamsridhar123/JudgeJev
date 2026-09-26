"""Native DeepEval operations. Provider evidence and arithmetic stay inspectable."""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
from statistics import mean

# Set these before any DeepEval import, including standalone scripts/tests.
os.environ.setdefault('DEEPEVAL_TELEMETRY_OPT_OUT', 'YES')
os.environ.setdefault('DEEPEVAL_DISABLE_PROGRESS_BAR', 'true')
os.environ.setdefault('DEEPEVAL_NO_INSPECT_PROMPT', '1')

from deepeval import evaluate
from deepeval.dataset import EvaluationDataset, Golden
from deepeval.evaluate.configs import AsyncConfig, CacheConfig, DisplayConfig, ErrorConfig
from deepeval.metrics import JevEval
from deepeval.metrics.jev_eval import Choice, Noul, Score
from deepeval.metrics.jev_eval.questions import QuestionOutcome
from deepeval.metrics.jev_eval.utils import aggregate, aggregate_strict, mark_strict
from deepeval.test_case import LLMTestCase, SingleTurnParams
from deepeval.test_run import global_test_run_manager

from fixtures import POLICY, POLICY_VERSION, text_hash
from lab_schema import Rubric
from providers import AuditedTypeSafeModel, JEV_MODEL, ProviderError, RUBRIC, valid_number

ESCALATION = {
    'key': 'escalation', 'label': 'Escalation handling', 'type': 'choice', 'weight': 2,
    'question': 'Classify how actual_output handles the need for human review, using input and the authoritative policy in context. Select No escalation required when the customer only needs policy information and requests no approval, exception, verification bypass, or account change. For a request requiring review, select the option that best describes the answer.',
    'options': {'Correctly routes for review': 1.0, 'Suggests review but promises an outcome': .3, 'Wrongly approves or bypasses review': 0.0, 'No escalation required': None},
}

PRESETS = [
    {'id': 'support', 'description': 'The same rubric used by the live monitor. A weighted answer-quality score.',
     'rubric': {'name': 'Support quality', 'evaluation_params': ['input', 'actual_output', 'context'], 'questions': RUBRIC, 'threshold': .8, 'strict_mode': False}},
    {'id': 'rag', 'description': 'Separate agreement with retrieved text from agreement with the correct policy. These are custom Jev diagnostics.',
     'rubric': {'name': 'RAG diagnosis', 'evaluation_params': ['input', 'actual_output', 'context', 'expected_output', 'retrieval_context'], 'threshold': .8, 'strict_mode': False,
                'questions': [
                    {'key': 'faithfulness', 'label': 'Faithful to retrieved text', 'type': 'noul', 'weight': 1, 'question': 'Every factual policy claim in actual_output is supported by retrieval_context. Judge only agreement with the retrieved text, even if that text differs from context or expected_output.'},
                    {'key': 'correctness', 'label': 'Correct against the reference', 'type': 'noul', 'weight': 1, 'question': 'actual_output answers input consistently with expected_output and the authoritative policy in context. It contains no contradicting or fabricated policy claims.'},
                    {'key': 'retrieval', 'label': 'Retrieved policy is adequate', 'type': 'noul', 'weight': 1, 'question': 'retrieval_context contains the correct policy information needed to answer input, as specified in context and expected_output, without outdated or contradictory rules relevant to this question.'},
                ]}},
    {'id': 'primitives', 'description': 'One Noul, one Score, and one Choice. Inspect probability, ordinal value, credits, and applicability.',
     'rubric': {'name': 'Three Jev primitives', 'evaluation_params': ['input', 'actual_output', 'context'], 'questions': [RUBRIC[0], RUBRIC[1], ESCALATION], 'threshold': .8, 'strict_mode': False}},
    {'id': 'escalation', 'description': 'A single Choice makes not-applicable behavior easy to inspect. An information-only question may have no applicable score.',
     'rubric': {'name': 'Escalation only', 'evaluation_params': ['input', 'actual_output', 'context'], 'questions': [ESCALATION], 'threshold': .8, 'strict_mode': False}},
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def question_objects(config: Rubric):
    values = []
    for q in config.questions:
        if q.type == 'noul':
            values.append(Noul(q.question, weight=q.weight))
        elif q.type == 'score':
            values.append(Score(q.question, levels=q.levels, weight=q.weight))
        else:
            values.append(Choice(q.question, options=q.options, weight=q.weight))
    return values


def metric_options(config: Rubric, model):
    return dict(name=config.name, evaluation_params=[SingleTurnParams(p) for p in config.evaluation_params],
                questions=question_objects(config), system_one_model=model, threshold=config.threshold,
                strict_mode=config.strict_mode, include_reason=True, verbose_mode=False)


def score_evidence(metric, model, config, started):
    breakdown = metric.score_breakdown
    if not valid_number(metric.score) or not breakdown or len(breakdown) != len(config.questions):
        raise ProviderError('Jev returned an invalid result; no score was substituted.')
    for result in breakdown:
        probabilities = result.get('probabilities', {})
        if not probabilities or not all(valid_number(p) for p in probabilities.values()) or not math.isclose(sum(probabilities.values()), 1, abs_tol=.003):
            raise ProviderError('Jev returned invalid probabilities; the evaluation was rejected.')
        if result.get('applicable') and not valid_number(result.get('value')):
            raise ProviderError('Jev returned an invalid applicable score.')
    outcomes = [QuestionOutcome(**b) for b in breakdown]
    normal = aggregate(outcomes)
    strict = aggregate_strict(mark_strict(question_objects(config), outcomes))
    expected = strict if config.strict_mode else normal
    if not math.isclose(metric.score, expected, abs_tol=1e-10):
        raise ProviderError('The native DeepEval score did not reconcile with its breakdown.')
    raw = getattr(model, 'audit_response', {})
    request = getattr(model, 'audit_request', {})
    if not raw or not request:
        raise ProviderError('The provider evidence was not captured; the evaluation was rejected.')
    return {
        'quality': metric.score, 'weighted_score': normal, 'strict_score': strict,
        'passed': metric.is_successful(), 'effective_threshold': metric.threshold,
        'applicable_count': sum(b['applicable'] for b in breakdown),
        'breakdown': breakdown, 'confidence': metric.confidence, 'reason': metric.reason,
        'eval_ms': (time.perf_counter() - started) * 1000, 'judge_model': JEV_MODEL,
        'metrics': {q.key: b['value'] for q, b in zip(config.questions, breakdown)},
        'jev_request': request, 'jev_response': raw,
        'jev_request_sha256': text_hash(canonical(request)), 'jev_response_sha256': text_hash(canonical(raw)),
        'jev_usage': {'input_tokens': metric.input_tokens, 'output_tokens': metric.output_tokens},
        'config_sha256': text_hash(canonical(config.model_dump(exclude_none=True))),
        'calculation': 'Native JevEval: weighted mean over applicable questions, or native strict mode.',
    }


async def close_model(model):
    for client in (getattr(model, '_async_client', None), getattr(model, 'model', None)):
        if client is None:
            continue
        try:
            if hasattr(client, 'aclose'):
                await client.aclose()
            elif hasattr(client, 'close'):
                outcome = client.close()
                if asyncio.iscoroutine(outcome):
                    await outcome
        except Exception:
            pass


async def evaluate_case(case: dict, config: Rubric, key: str):
    model = AuditedTypeSafeModel(model=JEV_MODEL, api_key=key, timeout=30)
    metric = JevEval(**metric_options(config, model))
    started = time.perf_counter()
    try:
        await asyncio.wait_for(metric.a_measure(LLMTestCase(**case), _show_indicator=False), timeout=45)
        return score_evidence(metric, model, config, started)
    except ProviderError:
        raise
    except asyncio.TimeoutError:
        raise ProviderError('Jev timed out. The answer is retained without a score.') from None
    except Exception as exc:
        raise ProviderError(f'Native DeepEval/Jev evaluation failed ({type(exc).__name__}). No score was substituted.') from None
    finally:
        await close_model(model)


class LabStopped(RuntimeError):
    pass


def evaluate_dataset(cases, goldens, config: Rubric, key, identifier, emit, stop):
    """One real deepeval.evaluate call per variant, serial inside a worker thread.

    The callback carries only captured evidence to the main event loop. The SDK
    key never enters the callback, stored rows, or native result serialization.
    """
    model = AuditedTypeSafeModel(model=JEV_MODEL, api_key=key, timeout=30)

    class CapturedJevEval(JevEval):
        def measure(self, test_case, **kwargs):
            if stop.is_set():
                raise LabStopped('Stopped before the next Jev call.')
            emit(test_case.name, {'status': 'evaluating'})
            started = time.perf_counter()
            try:
                value = super().measure(test_case, **kwargs)
                evidence = score_evidence(self, model, config, started)
                emit(test_case.name, {'status': 'completed', **evidence})
                return value
            except Exception as exc:
                emit(test_case.name, {'status': 'failed', 'error': f'Native DeepEval/Jev failed ({type(exc).__name__}); no score was substituted.'})
                raise ProviderError('A dataset evaluation failed. Partial evidence is retained.') from None

    native_cases = [LLMTestCase(**c) for c in cases]
    dataset = EvaluationDataset(goldens=[Golden(**g) for g in goldens])
    dataset.test_cases = native_cases
    metric = CapturedJevEval(**metric_options(config, model), async_mode=False)
    try:
        # Explicit local-only run, even if a separate Confident login exists.
        # These pinned 4.2.6 manager hooks preserve genuine evaluate() execution.
        global_test_run_manager.reset()
        global_test_run_manager.create_test_run(identifier=identifier, disable_request=True)
        result = evaluate(
            test_cases=dataset.test_cases, metrics=[metric], identifier=identifier, _skip_reset=True,
            async_config=AsyncConfig(run_async=False, max_concurrent=1),
            cache_config=CacheConfig(use_cache=False, write_cache=False),
            display_config=DisplayConfig(show_indicator=False, print_results=False, verbose_mode=False, inspect_after_run=False),
            error_config=ErrorConfig(ignore_errors=False, skip_on_missing_params=False),
        )
        return result.model_dump(mode='json')
    finally:
        client = getattr(model, 'model', None)
        if client is not None and hasattr(client, 'close'):
            client.close()


def replay(record, weights, threshold, strict_mode):
    if record.get('status') != 'completed' or not record.get('breakdown'):
        raise ValueError('Choose a completed Jev evaluation.')
    config = Rubric(**record['rubric'])
    if len(weights) != len(config.questions):
        raise ValueError('Supply one weight per saved question.')
    for question, weight in zip(config.questions, weights):
        question.weight = weight
    outcomes = [QuestionOutcome(**{**b, 'weight': w}) for b, w in zip(record['breakdown'], weights)]
    marked = mark_strict(question_objects(config), outcomes)
    score = aggregate_strict(marked) if strict_mode else aggregate(outcomes)
    effective = 1.0 if strict_mode else threshold
    return {'source': 'Saved probabilities; no provider call', 'record_id': record['id'], 'quality': score,
            'passed': score >= effective, 'effective_threshold': effective, 'weights': weights,
            'strict_mode': strict_mode, 'applicable_count': sum(b.applicable for b in outcomes),
            'breakdown': [b.model_dump() for b in marked], 'original_quality': record['quality']}


def dataset_summary(records, job):
    pairs = []
    for ticket in job['tickets']:
        rows = {r['variant']: r for r in records if r.get('ticket_id') == ticket['id']}
        a, b = rows.get('A'), rows.get('B')
        ready = all(r and r.get('status') == 'completed' and r.get('applicable_count', 0) > 0 for r in (a, b))
        pairs.append({'ticket_id': ticket['id'], 'category': ticket['category'], 'input': ticket['input'],
                      'a_id': a['id'] if a else None, 'b_id': b['id'] if b else None,
                      'a': a.get('quality') if a else None, 'b': b.get('quality') if b else None,
                      'b_passed': b.get('passed') if b else None, 'complete': ready,
                      'delta': b['quality'] - a['quality'] if ready else None})
    complete = [p for p in pairs if p['complete']]
    a_mean = mean(p['a'] for p in complete) if complete else None
    b_mean = mean(p['b'] for p in complete) if complete else None
    pass_rate = mean(float(p['b_passed']) for p in complete) if complete else None
    drop = a_mean - b_mean if complete else None
    eligible = len(complete) == len(pairs) and job['status'] == 'completed' and len(job.get('native_results', {})) == 2
    gates = [
        {'name': 'Every pair is complete and applicable', 'passed': eligible, 'value': len(complete), 'limit': len(pairs)},
        {'name': 'Candidate pass rate', 'passed': pass_rate >= job['options']['minimum_pass_rate'] if eligible else None, 'value': pass_rate, 'limit': job['options']['minimum_pass_rate']},
        {'name': 'Mean quality drop', 'passed': drop <= job['options']['max_mean_drop'] + 1e-12 if eligible else None, 'value': drop, 'limit': job['options']['max_mean_drop']},
    ]
    return {'pairs': pairs, 'complete_pairs': len(complete), 'a_mean': a_mean, 'b_mean': b_mean, 'mean_drop': drop,
            'candidate_pass_rate': pass_rate, 'regressions': sum(p['delta'] < -.05 for p in complete),
            'gates': gates, 'release_gate': 'pass' if eligible and all(g['passed'] for g in gates) else 'block' if eligible else 'incomplete',
            'gate_kind': 'App-defined release rule; native DeepEval per-case results are retained separately.'}


def python_example(case, config: Rubric):
    lines = ['import asyncio', 'from deepeval.metrics import JevEval', 'from deepeval.metrics.jev_eval import Noul, Score, Choice',
             'from deepeval.models.system_one.typesafe_model import TypeSafeModel', 'from deepeval.test_case import LLMTestCase, SingleTurnParams', '',
             '# Set TYPESAFE_API_KEY in the environment. No key is embedded here.', 'case = LLMTestCase(']
    lines += [f'    {k}={v!r},' for k, v in case.items() if v is not None]
    lines += [')', '', 'metric = JevEval(', f'    name={config.name!r},',
              '    evaluation_params=[' + ', '.join('SingleTurnParams.' + p.upper() for p in config.evaluation_params) + '],',
              f'    system_one_model=TypeSafeModel(model={JEV_MODEL!r}),', '    questions=[']
    for q in config.questions:
        extra = f', levels={q.levels!r}' if q.type == 'score' else f', options={q.options!r}' if q.type == 'choice' else ''
        cls = {'noul': 'Noul', 'score': 'Score', 'choice': 'Choice'}[q.type]
        lines.append(f'        {cls}({q.question!r}{extra}, weight={q.weight!r}),')
    lines += ['    ],', f'    threshold={config.threshold!r}, strict_mode={config.strict_mode!r},', ')', '',
              'asyncio.run(metric.a_measure(case))', 'print(metric.score, metric.is_successful())', 'print(metric.score_breakdown)', '']
    return '\n'.join(lines)
