"""Lab contracts and edge cases. All test doubles use temporary databases only."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import lab
from lab_engine import PRESETS, dataset_summary, metric_options, replay
from lab_schema import CaseAction, DatasetAction, DriftAction, Rubric
from fixtures import POLICY, TICKETS
from providers import ProviderError
from storage import Store
from deepeval.metrics import JevEval
from deepeval.models import DeepEvalBaseSystemOneModel
from deepeval.models.system_one.schema import SystemOneAnswers, NoulAnswer, ScoreAnswer, ChoiceAnswer
from deepeval.test_case import LLMTestCase


class DecisionDouble(DeepEvalBaseSystemOneModel):
    """Only tests use this fake provider; it cannot access a real credential."""
    def __init__(self, answers):
        self.answers, self.calls = answers, 0
        super().__init__('explicit-unit-test-double')

    def load_model(self):
        return self

    def get_model_name(self):
        return self.name

    def decide(self, state, questions):
        self.calls += 1
        self.state, self.questions = copy.deepcopy(state), questions
        return self.answers, None

    async def a_decide(self, state, questions):
        return self.decide(state, questions)


def config(name='support'):
    return Rubric(**next(p['rubric'] for p in PRESETS if p['id'] == name))


def test_only_selected_fields_cross_native_evaluation_boundary():
    c = config('escalation')
    model = DecisionDouble(SystemOneAnswers(choices={'q_0': ChoiceAnswer(
        choice='Correctly routes for review', confidence=.8,
        probabilities={'Correctly routes for review':.9, 'Suggests review but promises an outcome':.02,
                       'Wrongly approves or bypasses review':.03, 'No escalation required':.05})}))
    metric = JevEval(**metric_options(c, model), async_mode=False)
    case = LLMTestCase(input='Test input', actual_output='Test answer', context=['selected reference'],
                       expected_output='must not be forwarded', retrieval_context=['must not be forwarded'])
    metric.measure(case, _show_indicator=False)
    assert model.calls == 1
    assert model.state == {'test_case': {'input':'Test input', 'actual_output':'Test answer', 'context':['selected reference']}}
    assert model.questions['q_0'].options == {k:None for k in c.questions[0].options}


@pytest.mark.parametrize('na_mass,applicable,expected', [(.49,True,.4/.51),(.5,False,1.),(.9,False,1.)])
def test_choice_applicability_boundary_is_not_quality(na_mass, applicable, expected):
    c = Rubric(name='Test applicability', questions=[{'key':'review','label':'Review','type':'choice',
        'question':'Classify test answer', 'options':{'good':1.,'bad':0.,'N/A':None}}])
    good = .4 if na_mass <= .5 else .1
    model = DecisionDouble(SystemOneAnswers(choices={'q_0': ChoiceAnswer(choice='N/A', confidence=.3,
        probabilities={'good':good,'bad':max(0.,1-good-na_mass),'N/A':na_mass})}))
    metric = JevEval(**metric_options(c, model), async_mode=False)
    metric.measure(LLMTestCase(input='Test case', actual_output='Test answer', context=['test reference']), _show_indicator=False)
    assert metric.score == pytest.approx(expected)
    assert metric.score_breakdown[0]['applicable'] is applicable
    assert metric.score_breakdown[0]['value'] is not None if applicable else metric.score_breakdown[0]['value'] is None


def test_saved_outcome_exploration_is_call_free_and_preserves_evidence():
    c = config('primitives')
    model = DecisionDouble(SystemOneAnswers(
        nouls={'q_0': NoulAnswer(probability=.6)},
        scores={'q_1':ScoreAnswer(score=2.4, probabilities={0:0.,1:0.,2:.6,3:.4},confidence=.4)},
        choices={'q_2':ChoiceAnswer(choice='No escalation required',confidence=.3,probabilities={
            'Correctly routes for review':.4,'Suggests review but promises an outcome':0.,
            'Wrongly approves or bypasses review':.1,'No escalation required':.5})}))
    metric=JevEval(**metric_options(c,model),async_mode=False)
    metric.measure(LLMTestCase(input='Test input',actual_output='Test answer',context=['test reference']),_show_indicator=False)
    row={'id':'test-record','status':'completed','quality':metric.score,'rubric':c.model_dump(exclude_none=True),'breakdown':metric.score_breakdown}
    original=copy.deepcopy(row)
    normal=replay(row,[2,1,2],.65,False)
    blocked=replay(row,[2,1,2],.9,False)
    weighted=replay(row,[1,10,10],.8,False)
    strict=replay(row,[1,10,10],.1,True)
    assert normal['quality'] == pytest.approx((2*.6+.8)/3)
    assert normal['quality'] == blocked['quality']
    assert normal['passed'] and not blocked['passed']
    assert weighted['quality'] == pytest.approx((.6+10*.8)/11)
    assert strict['quality'] == 0 and strict['effective_threshold'] == 1
    assert metric.confidence == pytest.approx(.2)
    assert model.calls == 1 and row == original


def gate_fixture():
    job={'status':'completed','tickets':TICKETS[:1], 'native_results':{'A':{},'B':{}},
         'options':{'minimum_pass_rate':1.,'max_mean_drop':.05}}
    rows=[{'id':v,'ticket_id':'ticket-01','variant':v,'status':'completed','quality':q,'passed':True,'applicable_count':1}
          for v,q in [('A',.9),('B',.85)]]
    return job,rows


def test_release_limit_is_inclusive_and_does_not_hide_missing_native_results():
    job,rows=gate_fixture()
    assert dataset_summary(rows,job)['release_gate']=='pass'
    rows[1]['quality']=.849
    assert dataset_summary(rows,job)['release_gate']=='block'
    rows[1]['quality']=.85
    del job['native_results']['B']
    assert dataset_summary(rows,job)['release_gate']=='incomplete'


@pytest.mark.parametrize('defect',['missing','error','na','stopped'])
def test_partial_or_inapplicable_pairs_never_pass_gate(defect):
    job,rows=gate_fixture()
    if defect=='missing': rows.pop()
    if defect=='error': rows[1].update(status='failed',quality=None,passed=None)
    if defect=='na': rows[1].update(quality=1.,passed=True,applicable_count=0)
    if defect=='stopped': job['status']='stopped'
    result=dataset_summary(rows,job)
    assert result['release_gate']=='incomplete'
    assert result['gates'][1]['passed'] is None


class GenerationDouble:
    jev_key='explicit-unit-test-placeholder'
    generator_model='explicit-unit-test-double'
    generator_verified=False
    jev_verified=False
    def __init__(self,fail=False):
        self.calls=0
        self.fail=fail
        self.started=asyncio.Event()
        self.release=asyncio.Event()
    async def generate(self,ticket,scenario):
        self.calls+=1
        self.started.set()
        await self.release.wait()
        if self.fail: raise ProviderError('Intentional unit-test error.')
        return {'answer':'EXPLICIT TEST DOUBLE','generation_context':'test context'}


def test_dataset_history_survives_many_later_single_case_evaluations(tmp_path):
    monitor = SimpleNamespace(providers=GenerationDouble(), active=None,
                              store=SimpleNamespace(requests=lambda: []))
    controller = lab.LabController(monitor, tmp_path)
    try:
        controller.store.save_run({'id': 'old-dataset', 'kind': 'dataset', 'status': 'completed'})
        for index in range(40):
            controller.store.save_run({'id': f'case-{index}', 'kind': 'evaluate', 'status': 'completed'})
        jobs = controller.snapshot()['jobs']
        assert jobs[0]['id'] == 'old-dataset'
        assert [job['id'] for job in jobs[1:]] == [f'case-{index}' for index in range(10, 40)]
        assert len(controller.export()['jobs']) == 41
    finally:
        controller.store.close()


def test_stop_retains_current_generation_without_judging_or_mutating_monitor(tmp_path,monkeypatch):
    async def exercise():
        provider=GenerationDouble()
        monitor_store=Store(tmp_path/'monitor.sqlite3')
        reference={'request_ids':['untouched']}
        monitor_store.set('baseline',reference)
        monitor=SimpleNamespace(providers=provider,store=monitor_store,active=None,changed=lambda:None)
        controller=lab.LabController(monitor,tmp_path/'lab')
        def forbidden(*args,**kwargs): raise AssertionError('No judge call may start after stop')
        monkeypatch.setattr(lab,'evaluate_dataset',forbidden)
        controller.start(DatasetAction(count=5,rubric=config()))
        await provider.started.wait()
        with pytest.raises(HTTPException) as error:
            controller.start(DatasetAction(count=5,rubric=config()))
        assert error.value.status_code==409
        controller.stop.set()
        provider.release.set()
        await controller.task
        assert provider.calls==1
        assert len(controller.store.requests())==1
        assert controller.store.requests()[0]['status']=='generated'
        assert controller.store.requests()[0].get('quality') is None
        assert controller.store.runs()[-1]['summary']['release_gate']=='incomplete'
        assert controller.store.runs()[-1]['status']=='stopped'
        assert monitor_store.get('baseline')==reference and monitor_store.requests()==[]
        await controller.close()
        monitor_store.close()
    asyncio.run(exercise())


def test_generation_failure_keeps_dataset_incomplete(tmp_path):
    async def exercise():
        provider=GenerationDouble(fail=True)
        provider.release.set()
        store=Store(tmp_path/'monitor.sqlite3')
        monitor=SimpleNamespace(providers=provider,store=store,active=None,changed=lambda:None)
        controller=lab.LabController(monitor,tmp_path/'lab')
        controller.start(DatasetAction(count=5,rubric=config()))
        await controller.task
        assert provider.calls==1
        row=controller.store.requests()[0]
        assert row['status']=='failed' and row.get('quality') is None
        assert controller.store.runs()[-1]['summary']['release_gate']=='incomplete'
        await controller.close()
        store.close()
    asyncio.run(exercise())


def test_selected_empty_field_is_rejected_before_any_call():
    with pytest.raises(ValidationError,match='retrieval_context is empty'):
        CaseAction(action='evaluate',rubric=config('rag'),test_case={
            'input':'Test input','actual_output':'Test answer','context':['reference'],'expected_output':'expected'})


def test_drift_uses_exact_last_n_completed_members_and_excludes_failures(tmp_path):
    store=Store(tmp_path/'monitor.sqlite3')
    reference=[]
    for n in range(20):
        rid=f'ref-{n}'
        reference.append(rid)
        store.create_request({'id':rid,'status':'completed','quality':1.,'category':'Returns'})
    store.set('baseline',{'id':'fixed','request_ids':reference})
    for n in range(1,7):
        store.create_request({'id':f'new-{n}','status':'completed','quality':.2*n/6,'category':'Returns',
            'phase':'traffic','scenario':'stale_policy','finished_at':f'time-{n}'})
        store.create_request({'id':f'error-{n}','status':'failed'})
    controller=SimpleNamespace(monitor=SimpleNamespace(store=store))
    result=lab.drift_data(controller,DriftAction(source='traffic',window=5,cutoff=6))
    assert result['current_ids']==[f'new-{n}' for n in range(2,7)]
    assert result['baseline_ids']==reference
    assert result['current_mean']==pytest.approx(sum(.2*n/6 for n in range(2,7))/5)
    assert result['mix_js']==0 and result['quality_status']=='alert'
    assert result['first_alert']['index']==5 and result['excluded_errors']==6
    short=lab.drift_data(controller,DriftAction(source='traffic',window=5,cutoff=4))
    assert short['quality_status']=='insufficient' and short['quality_drop'] is None
    assert short['first_alert'] is None
    store.close()
