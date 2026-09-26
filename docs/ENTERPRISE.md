# Adapting JudgeJev to enterprise workloads

Start with one decision you want to improve: for example, whether a support reply follows an approved refund policy. Define what evidence the judge needs and what action a failing evaluation should cause. The included store policies and tickets are fictional teaching fixtures.

## Map your workload

| Lab element | Enterprise replacement |
| --- | --- |
| `fixtures.py` tickets | Approved examples sampled across products, languages, difficulty, and failure modes |
| `expected_output` | Human-reviewed expected behavior or reference answer |
| `context` | Versioned authoritative policy or allowed facts |
| `retrieval_context` | Documents actually retrieved for this request |
| `actual_output` | The exact response emitted by your application |
| `providers.py` generator | Your approved generation endpoint or an adapter for your application's recorded outputs |
| Jev rubric | Specific criteria aligned with business consequences |
| `dataset_summary` | Release conditions with explicit treatment of incomplete evidence |
| Rolling monitor | Adequately sampled production evaluations with a controlled reference cohort |

Do not send an expected answer to the judge merely because it exists; deliberately choose `evaluation_params`. The lab records the exact selected fields crossing that boundary. Separate model generation from evaluation so you can also score existing answers with only a Jev key.

## Validate the judge

Create a held-out set with human labels and include both positive and negative controls. Measure disagreement by slice, not just the aggregate score. Review false passes for high-consequence criteria and false failures that create operational burden. Use a calibration set to choose thresholds and a separate validation set to assess them. Judge confidence here measures decisiveness; it is not established correctness probability.

Avoid using the same judge as the sole source of its own ground truth. Version the generator, prompts, judge model, SDK, rubric, reference policy, dataset, and release rules. A policy change can change what “correct” means even when neither model changes.

## Define release and monitoring behavior

Native JevEval produces per-case results. The included release gate is application logic: all pairs must be complete and applicable, the candidate pass rate must meet a limit, and the mean drop must remain within a limit. Add critical-criterion vetoes or slice-specific gates when a mean can conceal unacceptable failures.

For monitoring, freeze a representative reference only after the planned cohort completes. Preserve request IDs and version metadata. Count provider failures separately; excluding them from quality arithmetic must not conceal an availability incident. Define minimum samples, sampling policy, alert persistence, review ownership, and recovery conditions using your own traffic. Account for dependence, repeated requests, multiple tests, delayed evaluations, and category composition before treating distances as statistical evidence.

The public demo's five-pair comparison and reconstructed playback do not validate a production drift detector. The live lab's mean-drop thresholds, JSD limits, and descriptive KS distance are inspectable examples that need workload-specific validation. Ticket-mix shift can be a change in demand without a model-quality regression.

## Provider and storage boundaries

The generator receives the user input and supplied retrieved policy. Jev receives the selected evaluation fields plus rubric instructions through TypeSafe. Calls can incur provider charges. Review provider contracts and allowed data classes before using actual customer or internal content. `store: false` in a generation request is not a substitute for contractual retention controls.

The local SQLite ledger retains prompts, answers, raw provider payloads, and evaluation evidence. It does not implement encryption, deletion schedules, tenancy, or access-control policy. `.gitignore` prevents routine Git staging but does not make the data safe to distribute. Keep sensitive records out of public exports, issue attachments, screenshots, recordings, and telemetry.

Optional DeepEval telemetry is disabled by default before importing the SDK. Native dataset execution is configured to avoid Confident uploads; pinned manager hooks are used for that purpose. Revalidate this behavior when upgrading the dependency. Provider HTTP requests are still required for fresh generation and judging.

## Shared deployment

The provided Python server is a local exploration tool. It binds to loopback and rejects non-loopback clients and untrusted browser origins. Browser-entered Jev keys are held in process memory; environment configuration is local. Do not turn this app into a shared service by simply changing its bind address or removing those checks.

For a shared deployment, add your organization's identity and tenant authorization, server-side secret manager, approved egress, request-size and concurrency limits, per-user budgets, retention/deletion policy, audited export controls, and operational ownership. Make workload data and secret handling explicit in the design. The public GitHub Pages deployment deliberately contains only the curated static demo and no credentials or live backend.

## A small first pilot

Choose one workflow and build a reviewed dataset with representative slices. Run the fixed controls, then fresh evaluations of your existing outputs. Review disagreements and missing evidence before deciding thresholds. Compare a candidate with a fixed baseline on the same held-out cases. Only after that evaluation is useful should you attach sampling and alerts to live traffic.
