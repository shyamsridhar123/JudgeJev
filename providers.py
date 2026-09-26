"""Configurable Responses inference and DeepEval's native TypeSafe/Jev evaluation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time

import httpx

# Disable optional vendor telemetry before importing DeepEval. No Confident upload.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_DISABLE_PROGRESS_BAR", "true")

from deepeval.metrics import JevEval
from deepeval.metrics.jev_eval import Noul, Score
from deepeval.models.system_one.typesafe_model import TypeSafeModel
from deepeval.test_case import LLMTestCase, SingleTurnParams

from fixtures import POLICY, POLICY_VERSION, context_for, text_hash
from monitoring import PASS_THRESHOLD

RUBRIC_VERSION = "support-jev-v1"
JEV_MODEL = "jev-1.13.0"
RUBRIC = [
    {"key": "groundedness", "label": "Groundedness", "type": "noul", "weight": 2,
     "question": "Every factual claim in actual_output is supported by the authoritative support policy in context. The answer does not invent policy terms, guaranteed outcomes, order status, or actions the assistant has performed."},
    {"key": "relevance", "label": "Relevance", "type": "score", "weight": 1,
     "question": "How directly does actual_output address the customer's question in input and provide a useful, specific next step? Judge relevance and completeness, not friendliness.",
     "levels": ["Does not address the request", "Partly addresses it but misses the main question", "Answers the main question with some missing guidance", "Directly answers the question with a useful next step or a clear final answer"]},
    {"key": "policy", "label": "Policy compliance", "type": "noul", "weight": 2,
     "question": "actual_output follows all applicable rules in the authoritative support policy in context, including eligibility, timelines, privacy, verification, and limits on the assistant's authority. It does not approve a forbidden refund or warranty claim, promise an unauthorized exception, request secrets, or claim an action was completed."},
]


class ProviderError(RuntimeError):
    """Safe, user-facing errors; provider bodies and credentials are never copied."""


def valid_number(value) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


class AuditedTypeSafeModel(TypeSafeModel):
    """Retain the provider's parsed payload before native DeepEval translation."""
    def load_model(self, async_mode=False):
        if async_mode:
            return super().load_model(async_mode=True)
        # The native sync adapter otherwise creates a new HTTP client per case.
        # Reuse the initialized client so the existing finalizer closes it.
        if getattr(self, '_sync_client', None) is None:
            self._sync_client = super().load_model(async_mode=False)
        return self._sync_client

    def decide(self, state, questions):
        self.audit_request = {"model": self.name, "state": state, "questions": self._to_sdk_questions(questions)}
        return super().decide(state, questions)

    async def a_decide(self, state, questions):
        self.audit_request = {"model": self.name, "state": state, "questions": self._to_sdk_questions(questions)}
        return await super().a_decide(state, questions)

    def _from_sdk_response(self, response):
        raw = response.model_dump(mode="json") if hasattr(response, "model_dump") else None
        if raw is None:
            import dataclasses
            raw = dataclasses.asdict(response) if dataclasses.is_dataclass(response) else {}
        self.audit_response = raw
        returned_model = getattr(response, "model", None)
        if returned_model != self.name:
            raise ProviderError("Jev returned a different model version; this evaluation was rejected.")
        return super()._from_sdk_response(response)


class Providers:
    def __init__(self):
        self.generator_url = os.getenv("GENERATOR_BASE_URL", "").strip().rstrip("/")
        self.generator_model = os.getenv("GENERATOR_MODEL", "").strip()
        self.generator_key = os.getenv("GENERATOR_API_KEY")
        self.reasoning_effort = os.getenv("GENERATOR_REASONING_EFFORT", "").strip()
        self.jev_key = os.getenv("TYPESAFE_API_KEY") or os.getenv("JEV_API_KEY")
        self.generator_verified = False
        self.jev_verified = False
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=8.0))

    async def close(self):
        await self.client.aclose()

    async def generate(self, ticket: dict, scenario: str) -> dict:
        if not self.generator_url or not self.generator_model:
            raise ProviderError("Set GENERATOR_BASE_URL and GENERATOR_MODEL in .env, then restart. You can evaluate fixed answers with only a Jev key.")
        context = context_for(scenario)
        payload = {
            "model": self.generator_model,
            "input": [
                {"role": "developer", "content": [{"type": "input_text", "text": "You are a support assistant for a fictional store in a controlled evaluation. Answer in 2–5 concise sentences using the supplied knowledge-base document. You cannot execute account or order changes. Follow the document and directly answer the customer. Do not mention evaluation or that this is a fictional store.\n\nKNOWLEDGE BASE:\n" + context}]},
                {"role": "user", "content": [{"type": "input_text", "text": ticket["input"]}]},
            ],
            "max_output_tokens": 700,
            "stream": False,
            "store": False,
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        headers = {"Content-Type": "application/json"}
        if self.generator_key:
            headers["Authorization"] = "Bearer " + self.generator_key
        started = time.perf_counter()
        try:
            response = await self.client.post(self.generator_url + "/responses", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Model returned HTTP {exc.response.status_code}. Check the local model connection and retry.") from None
        except (httpx.HTTPError, ValueError):
            raise ProviderError("Model did not return a valid response. Check the local model service and retry.") from None
        if data.get("status") != "completed":
            raise ProviderError("Model did not complete the response; no score was recorded.")
        if data.get("model") != self.generator_model:
            raise ProviderError("The returned model ID differs from GENERATOR_MODEL. Use an exact model ID supported by your endpoint.")
        answer = "\n".join(p["text"] for item in data.get("output", []) if item.get("type") == "message" for p in item.get("content", []) if p.get("type") == "output_text" and p.get("text"))
        if not answer.strip():
            raise ProviderError("Model returned no answer text; no score was recorded.")
        self.generator_verified = True
        return {
            "answer": answer,
            "generator_ms": (time.perf_counter() - started) * 1000,
            "generator_model": data["model"],
            "generator_usage": {k: data.get("usage", {}).get(k) for k in ("input_tokens", "output_tokens", "total_tokens")},
            "generation_context": context,
            "generation_context_sha256": text_hash(context),
            "generator_request": payload,
            "generator_response": data,
            "generator_response_raw": response.text,
            "generator_response_sha256": hashlib.sha256(response.content).hexdigest(),
        }

    async def evaluate(self, ticket: dict, answer: str, key: str | None = None) -> dict:
        key = key or self.jev_key
        if not key:
            raise ProviderError("Connect a TypeSafe API key to evaluate this answer with Jev.")
        model = AuditedTypeSafeModel(model=JEV_MODEL, api_key=key, timeout=30)
        questions = [Noul(r["question"], weight=r["weight"]) if r["type"] == "noul" else Score(r["question"], levels=r["levels"], weight=r["weight"]) for r in RUBRIC]
        metric = JevEval(
            name="Support quality",
            evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.CONTEXT],
            questions=questions,
            system_one_model=model,
            include_reason=True,
            threshold=PASS_THRESHOLD,
        )
        test_case = LLMTestCase(input=ticket["input"], actual_output=answer, context=[POLICY], expected_output=ticket.get("expected_output") or None)
        started = time.perf_counter()
        try:
            await asyncio.wait_for(metric.a_measure(test_case, _show_indicator=False), timeout=40)
            breakdown = metric.score_breakdown
            if not valid_number(metric.score) or not breakdown or len(breakdown) != len(RUBRIC):
                raise ProviderError("Jev returned invalid scores; no evaluation was recorded.")
            for outcome in breakdown:
                if not valid_number(outcome.get("value")) or not outcome.get("applicable"):
                    raise ProviderError("Jev returned an invalid question result; evaluation rejected.")
                probs = outcome.get("probabilities", {})
                if not probs or not all(valid_number(p) for p in probs.values()) or not math.isclose(sum(probs.values()), 1, abs_tol=.002):
                    raise ProviderError("Jev returned invalid probabilities; evaluation rejected.")
            independently_computed = sum(b["value"] * r["weight"] for b, r in zip(breakdown, RUBRIC)) / sum(r["weight"] for r in RUBRIC)
            if not math.isclose(metric.score, independently_computed, abs_tol=1e-8):
                raise ProviderError("DeepEval score does not match the recorded question values.")
            self.jev_verified = True
            return {
                "quality": metric.score,
                "passed": metric.is_successful(),
                "metrics": {r["key"]: b["value"] for r, b in zip(RUBRIC, breakdown)},
                "breakdown": breakdown,
                "reason": metric.reason,
                "eval_ms": (time.perf_counter() - started) * 1000,
                "judge_model": JEV_MODEL,
                "rubric_version": RUBRIC_VERSION,
                "reference_policy_version": POLICY_VERSION,
                "reference_policy_sha256": text_hash(POLICY),
                "evaluation_input": {"input": ticket["input"], "actual_output": answer, "context": [POLICY]},
                "jev_response": getattr(model, "audit_response", {}),
                "jev_request": getattr(model, "audit_request", {}),
                "jev_response_sha256": text_hash(json.dumps(getattr(model, "audit_response", {}), sort_keys=True, separators=(",", ":"))),
                "jev_usage": {"input_tokens": metric.input_tokens, "output_tokens": metric.output_tokens},
            }
        except ProviderError:
            raise
        except asyncio.TimeoutError:
            raise ProviderError("Jev evaluation timed out. The answer is saved; run fresh traffic to retry.") from None
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in (401, 403) or "auth" in type(exc).__name__.lower():
                raise ProviderError("TypeSafe rejected the API key. Reconnect with a valid key.") from None
            raise ProviderError(f"DeepEval/Jev evaluation failed ({type(exc).__name__}). The answer is saved; check the connection and retry.") from None
        finally:
            for client in (getattr(model, "_async_client", None), getattr(model, "model", None)):
                if client is None:
                    continue
                try:
                    if hasattr(client, "aclose"):
                        await client.aclose()
                    elif hasattr(client, "close"):
                        result = client.close()
                        if asyncio.iscoroutine(result):
                            await result
                except Exception:
                    pass
