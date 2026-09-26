<h1 align="center">
  <img src="docs/assets/judgejev-hero.png" width="720" alt="JudgeJev — Put your AI on trial. DeepEval + Jev. A gold judicial shield against a dark futuristic city." />
</h1>

<p align="center">
  <img src="docs/assets/judge-dredd.jpg" width="720" alt="Judge Dredd in his red-and-black helmet: I am the law. Prepare to be judged." />
</p>

<p align="center">
  <strong>An inspectable evaluation lab for DeepEval + Jev.</strong><br />
  Follow an AI support answer from its input and policy through the judge's raw response, a release gate, and a drift alert.
</p>

<p align="center">
  <a href="https://shyamsridhar123.github.io/JudgeJev/"><img src="docs/assets/open-demo.svg" width="184" height="48" alt="Open the public demo" /></a>
  <a href="#run-the-live-lab"><img src="docs/assets/run-locally.svg" width="176" height="48" alt="Run the live lab locally" /></a>
  <a href="docs/DEMO-SCRIPT.md"><img src="docs/assets/demo-script.svg" width="176" height="48" alt="Read the presenter script" /></a>
</p>

<p align="center">
  <a href="docs/ENTERPRISE.md">Enterprise adoption</a> · <a href="docs/EVIDENCE.md">Evidence provenance</a>
</p>

---

The public site replays **17 real, recorded Jev evaluations** of fictional support cases. It starts with a false shipping guarantee that scored **15.1%**, alongside a correct control that scored **95.5%**. You can inspect raw requests and responses, adjust decision rules, and see why a five-pair candidate fails a release gate.

**The hosted site makes no model calls.** Its drift sequence is reconstructed and explicitly labels reused samples. The repository includes a separate Python app that makes fresh provider calls, streams progress, and stores a local evidence ledger. Bring your own Jev key and, optionally, a generator endpoint.

## What you can explore

| Concept | Exercise |
| --- | --- |
| `LLMTestCase` and evaluation fields | Inspect exactly which input, answer, reference, and retrieved context reach the judge. |
| Native `JevEval` | Use `Noul`, ordinal `Score`, and credited `Choice` questions through DeepEval's TypeSafe adapter. |
| Weights, thresholds, strict mode | Recalculate saved probabilities without pretending a new evaluation ran. |
| Applicability | See why a native score of 1 with zero applicable criteria is displayed as N/A. |
| RAG diagnosis | Separate agreement with retrieved text from correctness against the authoritative policy. |
| `EvaluationDataset`, `Golden`, `evaluate()` | Compare current-policy and obsolete-policy answers on the same tickets in the live lab. |
| Release decisions | Require complete applicable pairs, a minimum pass rate, and a maximum mean drop. |
| Monitoring | Compare a frozen reference with rolling completed evaluations; distinguish quality change from ticket-mix change. |
| Evidence integrity | Recompute scores and check hashes; deliberately corrupt evidence in tests and watch verification fail. |

The RAG criteria here are custom Jev questions. They are not DeepEval's separate built-in faithfulness or contextual-relevancy metrics. Gate and drift rules are application logic, not a claim that DeepEval supplies an enterprise monitoring service.

## Run the live lab

Use **Python 3.12**. Node.js 22 or later is needed only for the public-demo tests.

```bash
git clone https://github.com/shyamsridhar123/JudgeJev.git
cd JudgeJev
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell, use:

```powershell
git clone https://github.com/shyamsridhar123/JudgeJev.git
cd JudgeJev
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit the ignored `.env` locally and set `TYPESAFE_API_KEY` to your own [TypeSafe](https://docs.typesafe.ai/) key. Then:

```bash
python serve.py
```

Open **http://127.0.0.1:8780/lab**. Load a fixed good or bad answer and select **Evaluate with Jev**. This route requires only the Jev key. The connection is marked verified only after a successful provider response. Provider errors retain the input and show no substitute score.

To enter the key at a hidden terminal prompt instead of a file, run `python serve.py --ask-key`. The server binds to loopback and keeps browser-entered keys in process memory. Restarting clears a browser-entered key. The app is intended for a trusted local workstation.

### Add your generator

For fresh answer generation, paired datasets, and the live monitor at **http://127.0.0.1:8780/**, configure:

```dotenv
GENERATOR_BASE_URL=
GENERATOR_MODEL=
GENERATOR_API_KEY=
GENERATOR_REASONING_EFFORT=
```

Supply a base URL that implements `POST /responses`; the app appends `/responses`. Set the **exact returned model ID**, since a different ID causes rejection. Authentication is a bearer token when `GENERATOR_API_KEY` is present. Reasoning effort is omitted unless you configure it. The adapter sends `store: false`; that field does not establish your provider's retention policy. Chat-completions-only endpoints require an adapter change in `providers.py`.

The live monitor first freezes **20 successful reference evaluations**. Current-policy, obsolete-policy, missing-context, and changed-ticket-mix traffic then use fresh calls. A 20-completion window drives quality alerts. Partial references and failed calls cannot make the monitor green. Fresh results can vary, and a fault scenario may be correctly resisted by the generator; inspect the generated answer and judge evidence to determine what happened.

### Run one case from a terminal

```bash
python scripts/evaluate_example.py --list
python scripts/evaluate_example.py --case shipping-bad --output data/fresh-shipping.json
python scripts/evaluate_example.py --case shipping-good
```

These are real API calls. The command exits 0 for an applicable pass, 1 for failure/N/A/provider error, and 2 for missing configuration. A bad control is expected to fail. The output includes the actual judge version, probabilities, hashes, and token usage. An answer that fails is a successful demonstration of evaluation, not a broken command.

## Verify without credentials

```bash
python scripts/verify_demo.py
python -m pytest -q
node --test tests/demo.test.mjs
```

The standalone verifier uses only the standard library. It checks selected evidence fields, typed rubric requests, returned probabilities, native aggregation and strict mode, applicability, pair coverage, fixture consistency, hashes, and the manifest. The Python tests use explicitly named test doubles in temporary directories. They never count as provider evidence. The browser independently verifies the whole evidence file hash and recalculates all 17 scores before showing the demo.

Hashes prove consistency with the supplied files, **not** a provider signature or accuracy against human judgments. The curated cases are teaching examples, not an unbiased benchmark. Judge confidence measures decisiveness and is not a calibrated probability of being correct.

## Preview and publish the demo

```bash
python -m http.server 8790 --bind 127.0.0.1 --directory demo
```

Open http://127.0.0.1:8790/. No build system, provider key, or backend is needed. The browser uses relative asset paths so the site works under the repository's GitHub Pages path.

The repository's **Verify and deploy Pages** workflow tests the app and evidence, then publishes only `demo/` from `main`. Pull requests run verification without deployment. Pages uses **GitHub Actions** as its source. A failed check prevents publication. In a fork, enable Pages with the same source and update the repository/demo links to your fork.

## Project layout

```text
demo/                  Public site and curated recorded evidence
static/                Live monitor and evaluation-lab frontend
app.py, lab.py         Local HTTP APIs, event streams, run orchestration
providers.py           Configurable generator and audited native TypeSafe adapter
lab_engine.py          Jev metrics, native dataset runs, replay and release rules
monitoring.py          Rolling windows, quality and mix signals
storage.py             Local SQLite evidence ledger
fixtures.py            Fictional tickets and policies
scripts/               Public audit, fresh-case CLI, synthetic capture utility
tests/                 Native semantics, failure behavior, evidence and UI math
docs/                  Walkthrough, provenance, enterprise extension guide
```

`scripts/capture_demo.py` captures a new synthetic-control experiment from a running local lab. It writes to the ignored `data/` directory. Its output is **not** automatically published or interchangeable with `demo/evidence.json`; review, curate, and audit any new public evidence first.

See [SECURITY.md](SECURITY.md) before using sensitive workloads or shared hosting. Local transcripts and raw provider outputs may contain sensitive data even though this distribution contains fictional cases. The public workflow has no provider credentials, and local ledgers, `.env` files, recordings, and exports are excluded from Git.

## Sources and license

The implementation is pinned to DeepEval **4.2.6**, TypeSafe SDK **0.7.1**, and Jev **1.13.0**. It uses pinned DeepEval manager hooks to disable Confident uploads during native dataset runs; review those hooks before upgrading.

- [DeepEval repository](https://github.com/confident-ai/deepeval)
- [Native JevEval documentation](https://deepeval.com/docs/metrics-jev-eval)
- [TypeSafe documentation](https://docs.typesafe.ai/)

MIT license for this project's code. The bundled Instrument Sans font retains its SIL Open Font License in `static/OFL.txt` and `demo/OFL.txt`. See [artwork provenance and licensing](docs/assets/ARTWORK.md) for the README images. Provider APIs and dependencies have their own terms. JudgeJev is an independent example project, not an official DeepEval or TypeSafe product.
