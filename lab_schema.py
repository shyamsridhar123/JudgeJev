"""Bounded, versioned inputs for the evaluation lab."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Param = Literal['input', 'actual_output', 'expected_output', 'context', 'retrieval_context']
Scenario = Literal['healthy', 'stale_policy', 'missing_context']


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)


class Question(StrictInput):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    label: str = Field(min_length=1, max_length=90)
    type: Literal['noul', 'score', 'choice']
    question: str = Field(min_length=5, max_length=2000)
    weight: float = Field(default=1, ge=.1, le=10)
    levels: list[str] | None = Field(default=None, min_length=2, max_length=10)
    options: dict[str, float | None] | None = None

    @model_validator(mode='after')
    def valid_shape(self):
        if self.type == 'score':
            if not self.levels or len(set(self.levels)) != len(self.levels) or any(not s.strip() or len(s) > 300 for s in self.levels):
                raise ValueError('Score needs 2–10 unique, nonempty levels, in worst-to-best order.')
        elif self.levels is not None:
            raise ValueError('Only Score questions use levels.')
        if self.type == 'choice':
            if not self.options or not 2 <= len(self.options) <= 8:
                raise ValueError('Choice needs 2–8 named options.')
            if any(not k.strip() or len(k) > 200 or (v is not None and not 0 <= v <= 1) for k, v in self.options.items()):
                raise ValueError('Choice credits must be 0–1 or null for not applicable.')
            if all(v is None for v in self.options.values()):
                raise ValueError('At least one Choice option must have a numeric credit.')
        elif self.options is not None:
            raise ValueError('Only Choice questions use options.')
        return self


class Rubric(StrictInput):
    name: str = Field(default='Support quality', min_length=1, max_length=90)
    evaluation_params: list[Param] = Field(default=['input', 'actual_output', 'context'], min_length=2, max_length=5)
    questions: list[Question] = Field(min_length=1, max_length=6)
    threshold: float = Field(default=.8, ge=0, le=1)
    strict_mode: bool = False

    @model_validator(mode='after')
    def unique_keys(self):
        if len({q.key for q in self.questions}) != len(self.questions):
            raise ValueError('Question keys must be unique.')
        if len(set(self.evaluation_params)) != len(self.evaluation_params):
            raise ValueError('Evaluation fields must be unique.')
        if not {'input', 'actual_output'} <= set(self.evaluation_params):
            raise ValueError('This lab requires input and actual_output in every evaluation.')
        return self


class Case(StrictInput):
    input: str = Field(min_length=5, max_length=3000)
    actual_output: str = Field(default='', max_length=10000)
    expected_output: str | None = Field(default=None, max_length=6000)
    context: list[str] | None = Field(default=None, max_length=8)
    retrieval_context: list[str] | None = Field(default=None, max_length=8)

    @model_validator(mode='after')
    def bounded_context(self):
        for chunks in (self.context, self.retrieval_context):
            if chunks is not None and any(not c.strip() or len(c) > 12000 for c in chunks):
                raise ValueError('Context chunks must be nonempty and at most 12,000 characters.')
        return self


class CaseAction(StrictInput):
    action: Literal['generate', 'evaluate']
    test_case: Case
    scenario: Scenario = 'healthy'
    rubric: Rubric
    source_record_id: str | None = Field(default=None, max_length=80)
    ticket_id: str = Field(default='custom', max_length=80)

    @model_validator(mode='after')
    def populated(self):
        if self.action == 'evaluate':
            for param in self.rubric.evaluation_params:
                if not getattr(self.test_case, param):
                    raise ValueError(f'The selected evaluation field {param} is empty.')
        return self


class DatasetAction(StrictInput):
    count: Literal[5, 10, 20] = 5
    candidate: Scenario = 'stale_policy'
    rubric: Rubric
    max_mean_drop: float = Field(default=.05, ge=0, le=1)
    minimum_pass_rate: float = Field(default=.9, ge=0, le=1)


class ReplayAction(StrictInput):
    record_id: str = Field(max_length=80)
    weights: list[float] = Field(min_length=1, max_length=6)
    threshold: float = Field(default=.8, ge=0, le=1)
    strict_mode: bool = False

    @model_validator(mode='after')
    def positive(self):
        if any(not .1 <= w <= 10 for w in self.weights):
            raise ValueError('Weights must be between 0.1 and 10.')
        return self


class DriftAction(StrictInput):
    source: Literal['proof', 'traffic'] = 'proof'
    cutoff: int | None = Field(default=None, ge=0, le=10000)
    window: Literal[5, 10, 20, 40] = 20
    threshold: float = Field(default=.8, ge=0, le=1)
    watch_drop: float = Field(default=.08, ge=.01, le=.5)
    alert_drop: float = Field(default=.15, ge=.01, le=.6)

    @model_validator(mode='after')
    def ordered(self):
        if self.alert_drop < self.watch_drop:
            raise ValueError('Alert drop must be at least the watch drop.')
        return self
