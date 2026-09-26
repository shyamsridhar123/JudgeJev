# JudgeJev team walkthrough

Allow 12–15 minutes. Open the public demo, then keep a configured local lab available for a fresh call. The public site works without credentials; fresh calls require your key and may take time or fail.

## 1. Set the expectations (one minute)

**Say:** “We are evaluating a fictional store's support assistant. The generator answers tickets; Jev judges those answers through DeepEval. This public site contains 17 saved native evaluations. It does not call a provider. We can inspect every request and response, and later make a fresh call locally.”

Point to **Recorded evidence** and the **17 scores reconciled** indicator. Explain that the digest checks file consistency and the independent audit checks arithmetic; neither establishes human accuracy or a provider signature.

## 2. Start with a failure (two minutes)

Open **Inspect a judgment → False guarantee**.

**Say:** “The customer asks about standard shipping. The answer claims it checked tracking and guarantees tomorrow. The policy says 3–5 business days and no tracking access.”

Show the **15.1% FAIL** result. Expand the policy and expected behavior. Inspect the criteria: an answer can be relevant while inventing policy or claiming an action it never performed.

Select **Correct answer**. Show **95.5% PASS**. Raise the pass threshold above 95.5% and watch it fail. Reset the rule.

**Say:** “A threshold changes our decision; it does not improve the answer or ask Jev again. Lowering the threshold until everything passes hides the problem.”

## 3. Go inside DeepEval and Jev (three minutes)

Select **Three primitives**. This capture used native strict mode. Turn strict mode off, inspect the weighted result, and adjust the weights.

- **Noul:** the probability that a proposition is true.
- **Score:** an expected ordinal level, normalized by the highest level. The provider's rounded histogram can differ slightly from its separately returned expectation.
- **Choice:** named outcomes with local credits. Null-credit outcomes allow “not applicable.”

**Say:** “The rubric is the contract. DeepEval chooses the fields sent to Jev and translates the returned typed answers. Our local weights and threshold determine the decision.”

Explain strict mode precisely: Noul requires at least 0.5, Score requires the highest level to be the most likely, and Choice requires the most likely applicable option to have full credit. All applicable questions must pass; the effective threshold is 1. Strict does not require every probability to equal 1.

Expand **Follow the evidence**: exact request, raw response, breakdown, and runnable native code. The request includes selected test-case fields and question instructions. It does not contain your API key.

Select **Not applicable**. Show the native score of 1 alongside the app's **N/A**.

**Say:** “There are zero applicable criteria. Counting this as a high-quality pass would inflate the dashboard. Our gate excludes it.”

## 4. Diagnose retrieval separately (one minute)

Select **RAG diagnosis**. Expand authoritative and retrieved policy.

**Say:** “The answer follows an obsolete return policy. Agreement with retrieval is 87%, while correctness against the authoritative reference is 1%. Faithfulness by itself is not enough.”

These are custom Jev questions over DeepEval test-case fields. They are not the separate built-in RAG metric classes.

## 5. Make a release decision (two minutes)

Open **Gate a release**. The five paired cases share input, authoritative policy, rubric, and judge version. The generator was given either current or obsolete policy.

Show the reference mean of **95.1%**, candidate mean of **25.8%**, **0/5 candidate passes**, and **69.3 percentage point** mean drop. Inspect a candidate to see its actual text.

Toggle **Simulate one missing candidate**. The gate becomes **INCOMPLETE**. Even permissive release thresholds cannot make missing evidence eligible. Restore the candidate.

**Say:** “The native metric evaluates a case. Our application combines cases into a release rule. A small synthetic dataset illustrates the mechanism; it does not certify a production release.”

## 6. Explain drift without pretending it is live (two minutes)

Open **Explore drift**, keep the five-event window, and press **Play sequence**.

- Events 1–4: warm-up; the partial mean is descriptive.
- Event 5: the complete faulty window triggers a quality alert.
- Events 6–10: recorded reference answers replace the faulty window.
- Event 10: the reused reference cohort returns the mean to baseline.

**Say:** “These scores are real, but their order is constructed for teaching. Recovery reuses reference calls and is not a fresh experiment.”

Try the one-event and three-event windows. Explain the tradeoff between early response and unstable signals; the demo is not an estimate of alert quality.

Choose **Ticket mix shift**. Play again. The quality signal remains stable while category divergence rises. JSD describes mix change; the KS value describes score-distribution distance. Neither is a p-value or proof of cause.

## 7. Finish with a fresh call (two minutes)

Open the local lab at `http://127.0.0.1:8780/lab`, load the shipping bad control, and click **Evaluate with Jev**. Inspect the newly returned record and raw evidence. Do not substitute the public capture if the provider is unavailable.

Alternatively run:

```bash
python scripts/evaluate_example.py --case shipping-bad --output data/team-demo.json
```

Expect a failed quality decision and exit status 1. The actual score may differ from the recorded sample. If a good and bad control do not separate, investigate selected fields, question wording, and the returned evidence before adjusting thresholds.

**Close with:** “For our workload we would replace the fictional fixtures with approved cases, compare judge decisions to held-out human labels, version the references and rubrics, and define our own gate and monitoring rules. We now have a concrete, inspectable starting point.”
