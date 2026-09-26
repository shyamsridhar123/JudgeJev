# What the public evidence proves

`demo/evidence.json` contains 17 curated native DeepEval/Jev evaluations captured on **September 25–26, 2026** with DeepEval 4.2.6, TypeSafe SDK 0.7.1, and `jev-1.13.0`.

Six answers are fixed, human-authored controls. Eleven are generated answers to fictional support tickets: one RAG example and ten answers from one five-pair dataset comparison. Generator identity, endpoint, authentication, original local record identifiers, and unrelated history are intentionally absent. This is not a benchmark or attestation of a generating model.

The retained raw Jev requests and responses are unchanged. The bundle also includes selected test-case fields, rubric configuration, native breakdown, score, applicability, usage, observed evaluation duration, capture date, and SHA-256 digests. The public IDs were assigned during curation. Latencies are historical observations, not live counters or service-level promises.

## Verification chain

1. Inputs and expected behavior must match the fictional fixture tickets.
2. Reference and retrieved policies must match the public policy documents. Fixed-control answers must match the public controls.
3. The Jev request must contain exactly the selected fields and typed question instructions.
4. Response version, question types, distributions, applicable outcomes, native aggregation, confidence, threshold, and recorded verdict must reconcile.
5. Each dataset pair must share the same input, reference, expected behavior, and rubric. Missing or inapplicable pairs fail verification.
6. The manifest records the exact evidence-file byte hash. Deployment runs the independent audit; the browser verifies that hash and separately recalculates all scores.

Run `python scripts/verify_demo.py`. Its standard-library implementation imports no application calculations or provider SDK. Tests intentionally alter raw probabilities, request fields, credits, scores, verdicts, coverage, and hashes to check rejection.

These checks detect inconsistency. A party able to replace the evidence, verifier, and manifest could generate a self-consistent counterfeit. There is no provider-signed attestation, unbiased sampling claim, or independent human-label validation in this repository.

## Drift playback

The quality scenario starts with the five obsolete-policy calls and follows with the five reference calls. Those same reference calls define the frozen baseline, so the final “recovery” is explicitly a reuse demonstration. The mix scenario repeatedly selects two reference records. Playback events are not independent observations, chronological production traffic, or fresh model calls.

The public page allows smaller windows for teaching. The live monitor uses fresh completed requests and defaults to a 20-record window. Both label insufficient data rather than treating an empty window as healthy. Neither demonstrates production drift-detection power or causal attribution.

## Updating public evidence

Capture fresh synthetic controls with `scripts/capture_demo.py` into the ignored `data/` directory. Review all answer text and provider payloads, select only approved synthetic examples, and explicitly map them to the public schema. Do not publish a raw lab export. After review, run `python scripts/verify_demo.py --write-manifest`, the tests, and a secret/privacy scan. Commit the evidence and regenerated manifest together. Changes to upstream native semantics require corresponding verifier tests and documentation.
