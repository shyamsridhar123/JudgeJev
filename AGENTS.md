# Learn and present with JudgeJev

JudgeJev is an educational repository for trying and experimenting with evaluations using DeepEval + Jev. Help people understand a result, change one variable, and inspect the evidence. The fictional cases and example rules are learning tools, not validated production defaults.

Use this teaching workflow when someone asks for a walkthrough, a learning session, or a presentation. For ordinary coding requests, work on the requested change without starting a presentation. These instructions are plain Markdown and do not require a particular AI provider or agent framework. An assistant that does not discover `AGENTS.md` automatically can be asked to read it explicitly.

A learner can start with:

> Read AGENTS.md and guide me through JudgeJev. Help me experiment with DeepEval + Jev, explain what changes, and show the evidence behind each result.

For a team presentation:

> Read AGENTS.md and present JudgeJev to my team in 15 minutes. Walk through the public demo, demonstrate a failure, and explain what each experiment teaches. Clearly identify recorded results and any fresh calls.

## Prepare from the actual project

- Read [README.md](README.md) for setup and boundaries, [docs/EVIDENCE.md](docs/EVIDENCE.md) for provenance, and [docs/ENTERPRISE.md](docs/ENTERPRISE.md) for adapting the exercises to a real workload.
- Use [the public demo](https://shyamsridhar123.github.io/JudgeJev/) for a session without credentials. To serve the same demo from a checkout, run `python -m http.server 8790 --bind 127.0.0.1 --directory demo` and open `http://127.0.0.1:8790/`.
- With browser tools, inspect the visible page before acting and check the result after each interaction. Prefer the control labels below; IDs are supplied to help locate them. With text-only access, give the learner those actions and explain the expected result. Never claim to have clicked, observed, verified, or called a provider without doing so.
- Confirm the page reports **Recorded evidence** and **all 17 scores reconciled**. The numbers below describe the committed capture; if the checked-out evidence or visible UI differs, investigate and use the observed version instead of repeating this guide blindly.
- For exact behavior, inspect [demo/app.js](demo/app.js), [demo/math.js](demo/math.js), and [demo/evidence.json](demo/evidence.json). Fresh evaluation code is in [lab_engine.py](lab_engine.py) and [providers.py](providers.py); live drift rules are in [monitoring.py](monitoring.py).

## Teach through experiments

Default to a 12–15 minute walkthrough for a technical audience new to Jev. Respect a requested audience or duration and proceed without a setup questionnaire. In a hands-on session, let the learner predict an outcome, change one control, observe it, and explain why. In a presentation, keep moving through the examples and leave time for discussion.

Explain the roles early: the **generator** creates an answer; **Jev**, reached through TypeSafe, judges selected evidence; DeepEval's native **`JevEval`** converts typed answers into a metric; **this app** stores evidence and applies release and drift rules. The AI presenting the project is not automatically the configured generator or the Jev judge.

Keep the evidence visible. A verdict alone is not the lesson: connect the input, actual answer, reference, rubric, returned probabilities, and decision rule. The public controls recalculate saved results. Changing the answer, selected evidence, or question wording requires a fresh evaluation.

## Walkthrough and learning exercises

### 1. Establish what is running · 1 minute

Open **Inspect a judgment** (`#workbench`). Explain that the scenario is a fictional store support assistant. The public site contains **17 real, recorded native Jev evaluations** and makes no provider calls. Its drift sequence is constructed for teaching.

Show the verification indicator. File hashes and score reconciliation demonstrate consistency and arithmetic, not a provider signature or accuracy measured against human labels. The pinned implementation uses DeepEval 4.2.6, TypeSafe SDK 0.7.1, and Jev 1.13.0; check the repository before describing another version.

### 2. Compare a failure with a positive control · 2 minutes

Select **False guarantee** (`shipping-bad`, via the shortcut or `#case-select`). Expand **Authoritative policy · context** and **Expected behavior**. The answer claims tracking access and guarantees arrival tomorrow; the policy says 3–5 business days and provides no tracking access. The recorded result is **15.1% FAIL**.

Select **Correct answer** (`shipping-good`): **95.5% PASS**. Move **Pass threshold** (`#threshold`) to **96%** and observe the verdict change while the underlying probabilities stay fixed. Select **Reset to recorded configuration** (`#reset-rule`).

Learning check: a threshold changes a decision, not answer quality. A low threshold can make the known bad control pass. Do not tune away a failure to make the demo look successful.

### 3. Inspect the three primitives · 3 minutes

Select **Three primitives** (`primitives-review`). The capture used native strict mode. Uncheck **Use native strict mode** (`#strict`), change one criterion weight, and inspect the weighted result.

- **`Noul`** returns the probability that a proposition is true.
- **`Score`** uses the returned expected ordinal level divided by the highest level. The rounded histogram may differ slightly from the separately returned expectation used by the native metric.
- **`Choice`** maps named outcomes to local credits. Null-credit outcomes allow inapplicability; applicable expected credit excludes inapplicable probability mass.

In the pinned native strict mode, Noul needs at least 0.5; Score needs the highest level to be most likely; Choice needs the most likely applicable option to have full credit. All applicable criteria must pass, the effective threshold is 1, and weights have no effect. Strict mode does not require every probability to equal 1. Reset the recorded configuration after the experiment.

Expand **Follow the evidence**: inspect **Exact request sent to Jev**, **Raw Jev response**, **Rubric and local score breakdown**, and **Reproduce this case with native DeepEval**. Explain `LLMTestCase` and how `evaluation_params` determines which fields reach the judge.

Select **Not applicable** (`not-applicable`). DeepEval's native score is 1, but zero criteria apply. The app shows **N/A** and excludes the case from release decisions. Do not count it as a quality pass.

### 4. Separate retrieval agreement from correctness · 1 minute

Select **RAG diagnosis** (`rag-stale`). Expand both the authoritative and retrieved policies. The answer agrees with obsolete retrieved text (**87%**) but contradicts the authoritative reference (**1%**).

Learning check: an answer can follow its retrieved context and still be wrong. These are custom Jev questions over DeepEval fields, not DeepEval's separate built-in RAG metric classes.

### 5. Experiment with a release rule · 2 minutes

Open **Gate a release** (`#compare`). Explain the five paired cases: the generator received current or obsolete policy; the judge received the same authoritative reference. Show the **95.1% reference mean**, **25.8% candidate mean**, **0/5 candidate passes**, and **69.3 percentage point mean drop**. Inspect a candidate's actual answer.

Select **Simulate one missing candidate** (`#omit-pair`): the gate becomes **INCOMPLETE**. To test that missing evidence cannot pass, set **Minimum candidate pass rate** to 0% (`#gate-pass`) and **Maximum mean drop** to 100 pp (`#gate-drop`). It must remain incomplete. Restore 90%, 5 pp, and the missing candidate afterward.

Connect `Golden`, `EvaluationDataset`, and `evaluate()` to the local paired dataset runner in `lab_engine.py`. The hosted view replays its recorded comparison. The release gate is application logic; five synthetic pairs teach its mechanics without certifying a production release.

### 6. Explore when a drift alert fires · 2 minutes

Open **Explore drift** (`#drift`). Choose **Policy fault → reference replay**, keep the **5 events** window, select **Reset**, then **Play sequence**. Use **Playback position** (`#drift-position`) to inspect exact events if playback moves too quickly.

- Events 1–4: warm-up; the partial mean is descriptive.
- Event 5: a complete faulty window triggers the quality alert.
- Events 6–10: recorded reference results replace faulty results; at event 10 the mean returns to baseline.

State that recovery **reuses the reference calls**. Playback speed is unrelated to provider latency, and the sequence is not an independent live experiment.

Try the 1- and 3-event windows to see sensitivity change. Then choose **Ticket mix shift** and play it: quality remains stable while category divergence rises. Base-2 JSD measures category-mix change; KS here is a descriptive score-distribution distance. Neither is a p-value or proof of a cause. Reset the scenario to the policy fault, the window to 5, and playback to the start afterward.

### 7. Offer one fresh experiment · about 2 minutes plus provider time

Fresh calls are optional and require the learner's own configured provider access. If the requested session includes them, use approved fictional inputs and the local setup in the README. A fixed answer only needs a TypeSafe/Jev key; generating new answers also needs a configured Responses-compatible generator. Never print keys or request that someone paste them into a shared chat.

In `http://127.0.0.1:8780/lab`, load the shipping bad control and select **Evaluate with Jev**, then inspect the newly returned record and raw evidence. A terminal alternative is:

```bash
python scripts/evaluate_example.py --case shipping-bad --output data/team-demo.json
python scripts/evaluate_example.py --case shipping-good
```

A genuine quality failure is expected for the bad control. Exit 1 also covers N/A or a provider error, so read the returned result before claiming a successful negative-control test. Exit 0 is an applicable pass; exit 2 is missing configuration. Fresh scores may differ from the capture.

For deeper learning, pick one experiment: correct the bad answer and reevaluate; change one rubric question; or change which context reaches the judge. Record the hypothesis, changed variable, fresh response, decision, and explanation. Use a separate local case or output file so the committed teaching evidence remains reproducible.

The separate local live monitor at `/` uses fresh generator and Jev calls. It freezes **20 successful reference evaluations** and uses a **20-completion rolling window**. Explain its setup and runtime before starting a monitoring exercise; the public five-record playback does not demonstrate that a live monitor is connected.

## When the result is unexpected

- If everything passes, restore recorded thresholds and weights, select the known bad control, and inspect the selected fields, actual answer, applicability, question wording, and raw response. In fresh generation, a model may correctly resist the fault scenario; show what it actually answered.
- If evidence verification fails, report the failure and investigate it. Do not bypass the check, manufacture scores, or claim a working demo.
- If a fresh call fails or is unavailable, show that state and continue with clearly labeled recorded experiments. Never present a saved response as a new provider call.
- If asked how to know it works, show both good and bad controls, raw evidence, and an independent audit. From a configured checkout, `python scripts/verify_demo.py`, `python -m pytest -q`, and `node --test tests/demo.test.mjs` verify evidence and implementation behavior without provider credentials. Tests use test doubles and do not prove provider availability or judge accuracy.

Finish by naming what the learner changed, what the evidence showed, and one experiment they can try next. For a real workload, use approved examples and held-out human labels, review false passes and false failures, and version policies, rubrics, models, and decision rules. Keep keys, private endpoints, personal data, and local ledgers out of commits, screenshots, recordings, and public exports; follow [SECURITY.md](SECURITY.md).
