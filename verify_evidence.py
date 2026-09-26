"""Independently audit an export using only the Python standard library.

No imports from the app, providers, fixtures, or monitoring calculations.
This checks recorded provenance and arithmetic, not human-rated accuracy or
cryptographic authentication of a provider. Run against an actual API export.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

POLICY_SHA256 = "9b991c288db695be0b79b9dbfda0fc4365759b48a4f4b39ac75bc5310c074b88"
METRICS = ("groundedness", "relevance", "policy")
WEIGHTS = (2, 1, 2)
PROBABILITY_SCORE_TOLERANCE = .035  # Four probabilities and score rounded to .01.


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def timestamp(value):
    return datetime.fromisoformat(value)


def mean(values):
    return math.fsum(values) / len(values) if values else None


def quantile(values, fraction):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    rank = fraction * (len(values) - 1)
    lower, upper = math.floor(rank), math.ceil(rank)
    return values[lower] * (1 - (rank - lower)) + values[upper] * (rank - lower)


def summary(rows):
    return {"count": len(rows), "quality": mean([r["quality"] for r in rows]),
            "pass_rate": mean([float(r["quality"] >= .8) for r in rows]),
            "metrics": {m: mean([r["metrics"][m] for r in rows]) for m in METRICS},
            **{f"{field}_p{percent}_ms": quantile([r[f"{field}_ms"] for r in rows], percent / 100)
               for field in ("generator", "eval") for percent in (50, 95)}}


def js(a, b):
    if not a or not b:
        return None
    p, q = Counter(r["category"] for r in a), Counter(r["category"] for r in b)
    result = 0
    for label in p.keys() | q.keys():
        x, y = p[label] / len(a), q[label] / len(b)
        middle = (x + y) / 2
        result += (x * math.log2(x / middle) if x else 0) / 2
        result += (y * math.log2(y / middle) if y else 0) / 2
    return result


def ks(a, b):
    if not a or not b:
        return None
    left, right = [r["quality"] for r in a], [r["quality"] for r in b]
    return max(abs(sum(v <= x for v in left) / len(left) - sum(v <= x for v in right) / len(right))
               for x in set(left + right))


def signal(value, watch, alert):
    return "alert" if value >= alert - 1e-12 else "watch" if value >= watch - 1e-12 else "stable"


class Audit:
    def __init__(self):
        self.checks = 0

    def require(self, condition, name):
        self.checks += 1
        if not condition:
            raise ValueError(f"Evidence check failed: {name}")

    def equal(self, actual, expected, name):
        if isinstance(expected, dict):
            self.require(isinstance(actual, dict), name + " object")
            for key, value in expected.items():
                self.equal(actual.get(key), value, f"{name}.{key}")
        elif isinstance(expected, float):
            self.require(isinstance(actual, (int, float)) and not isinstance(actual, bool)
                         and math.isfinite(actual) and math.isclose(actual, expected, abs_tol=1e-9, rel_tol=1e-10), name)
        else:
            self.require(actual == expected, name)

    def number(self, value, lower, upper, name):
        self.require(isinstance(value, (float, int)) and not isinstance(value, bool)
                     and math.isfinite(value) and lower <= value <= upper, name)

    def evaluation(self, row, config, control=False):
        tag = row.get("id", row.get("label", "evaluation"))
        raw = row["jev_response"]
        self.equal(raw["model"], "jev-1.13.0", tag + " returned Jev model")
        answers = raw["answers"]
        self.equal(set(answers), {"q_0", "q_1", "q_2"}, tag + " raw questions")
        values = [answers["q_0"]["noul"], answers["q_1"]["score"] / 3, answers["q_2"]["noul"]]
        for i, value in enumerate(values):
            self.number(value, 0, 1, tag + f" question {i} value")
            self.equal(answers[f"q_{i}"]["type"], "score" if i == 1 else "noul", tag + " answer type")
        quality = (2 * values[0] + values[1] + 2 * values[2]) / 5
        self.equal(row["quality"], quality, tag + " raw Jev to native DeepEval score")
        self.equal(row["passed"], quality >= .8, tag + " pass decision")
        self.equal(row["metrics"], dict(zip(METRICS, values)), tag + " metric translation")
        self.equal(len(row["breakdown"]), 3, tag + " breakdown count")
        probabilities = answers["q_1"]["probabilities"]
        self.equal(set(probabilities), {"0", "1", "2", "3"}, tag + " relevance levels")
        for value in probabilities.values():
            self.number(value, 0, 1, tag + " raw probability")
        self.require(abs(sum(probabilities.values()) - 1) <= .020000001, tag + " rounded probability mass")
        expected_level = sum(int(k) * v for k, v in probabilities.items())
        self.require(abs(expected_level - answers["q_1"]["score"]) <= PROBABILITY_SCORE_TOLERANCE + 1e-9,
                     tag + " expected level agrees within provider rounding")
        for i, detail in enumerate(row["breakdown"]):
            self.equal(detail["value"], values[i], tag + f" breakdown {i} value")
            self.equal(detail["weight"], float(WEIGHTS[i]), tag + " question weight")
            self.equal(detail["applicable"], True, tag + " question applicability")
            self.equal(detail["question"], config["rubric"][i]["question"], tag + " rubric question")
            expected = {answers["q_1"]["legend"][k]: v for k, v in probabilities.items()} if i == 1 else {"true": values[i], "false": 1 - values[i]}
            self.equal(detail["probabilities"], expected, tag + " native probabilities")
        self.equal(row["judge_model"], "jev-1.13.0", tag + " recorded judge")
        self.equal(row["rubric_version"], "support-jev-v1", tag + " rubric version")
        self.equal(row["reference_policy_version"], "support-policy-v1", tag + " policy version")
        self.equal(row["reference_policy_sha256"], POLICY_SHA256, tag + " policy hash")
        self.equal(len(row["evaluation_input"]["context"]), 1, tag + " authoritative context count")
        self.equal(digest(row["evaluation_input"]["context"][0]), POLICY_SHA256, tag + " authoritative context bytes")
        self.equal(row["evaluation_input"]["actual_output"], row["answer"], tag + " actual answer evaluated")
        self.number(row["eval_ms"], .001, 120000, tag + " observed evaluation latency")
        self.equal(row["jev_usage"], raw["usage"], tag + " Jev token accounting")
        self.number(raw["usage"]["input_tokens"], 1, 1000000, tag + " Jev input tokens")
        if not control:
            request = row["jev_request"]
            self.equal(request["model"], raw["model"], tag + " requested judge")
            self.equal(request["state"]["test_case"], row["evaluation_input"], tag + " exact Jev request state")
            self.equal(row["evaluation_input"]["input"], row["input"], tag + " customer question evaluated")
            for i, rubric in enumerate(config["rubric"]):
                question = request["questions"][f"q_{i}"]
                self.equal(question["type"], rubric["type"], tag + " requested question type")
                self.equal(question["instructions"], rubric["question"], tag + " requested rubric")
                if i == 1:
                    self.equal(question["criteria"], rubric["levels"], tag + " requested relevance levels")
            self.equal(row["jev_response_sha256"], digest(json.dumps(raw, sort_keys=True, separators=(",", ":"))), tag + " parsed Jev response hash")

    def generation(self, row):
        tag = row["id"]
        raw = row["generator_response_raw"]
        response, request = row["generator_response"], row["generator_request"]
        self.equal(json.loads(raw), response, tag + " raw Model JSON")
        self.equal(digest(raw), row["generator_response_sha256"], tag + " raw Model response hash")
        self.equal(response["status"], "completed", tag + " Model completed")
        self.require(isinstance(response["model"], str) and bool(response["model"]), tag + " returned generator model ID")
        self.equal(request["model"], response["model"], tag + " requested generator model")
        self.equal(row["generator_model"], response["model"], tag + " recorded generator model")
        answer = "\n".join(part["text"] for item in response["output"] if item.get("type") == "message"
                           for part in item.get("content", []) if part.get("type") == "output_text" and part.get("text"))
        self.require(bool(answer.strip()), tag + " nonempty Model answer")
        self.equal(answer, row["answer"], tag + " extracted answer")
        self.equal(request["input"][1]["content"][0]["text"], row["input"], tag + " exact customer input")
        self.require(request["input"][0]["content"][0]["text"].endswith(row["generation_context"]), tag + " exact generation context")
        self.equal(digest(row["generation_context"]), row["generation_context_sha256"], tag + " generation context hash")
        if row["scenario"] == "healthy":
            self.equal(row["generation_context_sha256"], POLICY_SHA256, tag + " healthy policy")
        else:
            self.require(row["generation_context_sha256"] != POLICY_SHA256, tag + " changed generation context")
        self.number(row["generator_ms"], .001, 120000, tag + " observed generation latency")
        self.equal(row["generator_usage"], {k: response["usage"].get(k) for k in ("input_tokens", "output_tokens", "total_tokens")}, tag + " Model token accounting")
        self.require(timestamp(row["created_at"]) <= timestamp(row["generated_at"]) <= timestamp(row["finished_at"]), tag + " generation precedes evaluation")

    def monitor(self, actual, reference, current, name):
        self.equal(actual["reference"], summary(reference), name + " reference")
        self.equal(actual["current"], summary(current), name + " current")
        self.equal(actual["current_ids"], [r["id"] for r in current], name + " window membership")
        delta = mean([r["quality"] for r in current]) - mean([r["quality"] for r in reference]) if current and reference else None
        enough = len(reference) >= 20 and len(current) >= 20
        mix = js(reference, current)
        self.equal(actual["quality_delta"], delta, name + " quality difference")
        self.equal(actual["mix_js"], mix, name + " category Jensen-Shannon")
        self.equal(actual["quality_ks"], ks(reference, current), name + " descriptive KS")
        self.equal(actual["enough_samples"], enough, name + " minimum sample gate")
        self.equal(actual["window"], 20, name + " window size")
        self.equal(actual["min_samples"], 20, name + " minimum samples")
        self.equal(actual["thresholds"], {"pass": .8, "quality_watch": .08, "quality_alert": .15, "mix_watch": .1, "mix_alert": .2}, name + " thresholds")
        quality_status = signal(max(0, -delta), .08, .15) if enough else "warming_up"
        mix_status = signal(mix, .1, .2) if enough else "warming_up"
        self.equal(actual["quality_status"], quality_status, name + " quality signal")
        self.equal(actual["mix_status"], mix_status, name + " mix signal")
        for segment in actual["segments"]:
            left = [r for r in reference if r["category"] == segment["category"]]
            right = [r for r in current if r["category"] == segment["category"]]
            self.equal(segment, {"reference_count": len(left), "current_count": len(right), "reference_quality": mean([r["quality"] for r in left]), "current_quality": mean([r["quality"] for r in right])}, name + " category segment")


def verify(data):
    audit = Audit()
    audit.equal(data["schema_version"], 1, "export schema")
    audit.equal(data["config"]["policy_sha256"], POLICY_SHA256, "pinned canonical policy")
    audit.equal([r["weight"] for r in data["config"]["rubric"]], [2, 1, 2], "pinned score weights")
    rows = data["requests"]
    audit.equal(len({r["id"] for r in rows}), len(rows), "unique request IDs")
    audit.equal(len({r["seq"] for r in rows}), len(rows), "unique request sequence")
    completed = [r for r in rows if r["status"] == "completed"]
    audit.equal(len({r["generator_response"]["id"] for r in completed}), len(completed), "unique Model response IDs")
    for row in rows:
        if row["status"] == "completed":
            audit.generation(row)
            audit.evaluation(row, data["config"])
        else:
            audit.equal(row.get("quality"), None, row["id"] + " no substituted score on failure")
    by_id = {r["id"]: r for r in rows}
    proof = data["proof"]
    run = next(r for r in data["runs"] if r["id"] == proof["run_id"])
    audit.equal(run["status"], "completed", "proof terminal state persisted")
    audit.equal(run["completed"], 60, "60 proof calls completed")
    audit.equal(run["failed"], 0, "no failed proof calls")
    audit.equal(proof["counts"], {"generator_and_jev_completed": 60, "failed": 0}, "proof counts")
    phases = {}
    for stage in proof["stages"]:
        phase = stage["phase"]
        group = [by_id[i] for i in stage["request_ids"]]
        audit.equal(len(group), 20, phase + " sample size")
        audit.equal(len({r["ticket_id"] for r in group}), 20, phase + " unique matched tickets")
        audit.equal(Counter(r["category"] for r in group), {k: 4 for k in ("Returns", "Shipping", "Billing", "Account", "Warranty")}, phase + " balanced ticket mix")
        audit.require(all(r["status"] == "completed" and r["run_id"] == run["id"] and r["phase"] == phase for r in group), phase + " completed phase membership")
        audit.require(all(r["source"] == "synthetic fixture" for r in group), phase + " synthetic source label")
        audit.equal(stage["summary"], summary(group), phase + " independent phase aggregates")
        audit.require(all(r["scenario"] == ("stale_policy" if phase == "fault" else "healthy") for r in group), phase + " intended generation policy")
        phases[phase] = group
    audit.equal(set(phases), {"baseline", "fault", "recovery"}, "complete three-phase experiment")
    baseline = phases["baseline"]
    match = {r["ticket_id"]: (r["input"], r["category"]) for r in baseline}
    for phase in ("fault", "recovery"):
        audit.equal({r["ticket_id"]: (r["input"], r["category"]) for r in phases[phase]}, match, phase + " exact matched inputs")
    audit.require(max(r["seq"] for r in baseline) < min(r["seq"] for r in phases["fault"]), "reference precedes fault")
    audit.require(max(r["seq"] for r in phases["fault"]) < min(r["seq"] for r in phases["recovery"]), "fault precedes recovery")
    for stage in proof["stages"]:
        audit.monitor(stage["monitor"], baseline, [] if stage["phase"] == "baseline" else phases[stage["phase"]], stage["phase"] + " monitor")
    stage_summaries = {k: summary(v) for k, v in phases.items()}
    drop = stage_summaries["baseline"]["quality"] - stage_summaries["fault"]["quality"]
    recovery_delta = stage_summaries["recovery"]["quality"] - stage_summaries["baseline"]["quality"]
    audit.require(drop >= .15, "fault actually crosses the quality alert threshold")
    audit.require(recovery_delta > -.08, "restored policy actually clears quality watch")
    audit.equal(proof["status"], "passed", "claimed proof agrees with recomputed criterion")

    reference_ids = data["baseline"]["request_ids"]
    audit.equal(len(reference_ids), len(set(reference_ids)), "frozen membership has no duplicates")
    reference = [by_id[i] for i in reference_ids]
    current = sorted((r for r in completed if r["seq"] > max(x["seq"] for x in reference) and r["id"] not in reference_ids), key=lambda r: r["seq"])[-20:]
    audit.monitor(data["monitor"], reference, current, "export current monitor")

    proof_ids = {r["id"] for group in phases.values() for r in group}
    events = [e for e in data["events"] if e["kind"] == "drift_state" and e.get("request_id") in proof_ids]
    frozen_at = next(s["ended_at"] for s in proof["stages"] if s["phase"] == "baseline")
    for event in events:
        seen = sorted((r for r in completed if r["id"] in proof_ids and timestamp(r["finished_at"]) <= timestamp(event["at"]) and r["seq"] > max(x["seq"] for x in baseline)), key=lambda r: r["seq"])[-20:]
        before_freeze = timestamp(event["at"]) < timestamp(frozen_at)
        if before_freeze:
            seen = []
        delta = mean([r["quality"] for r in seen]) - stage_summaries["baseline"]["quality"] if seen else None
        mix = js(baseline, seen)
        quality_status = "no_reference" if before_freeze else "warming_up" if len(seen) < 20 else signal(max(0, -delta), .08, .15)
        mix_status = "no_reference" if before_freeze else "warming_up" if len(seen) < 20 else signal(mix, .1, .2)
        audit.equal(event["status"], {"quality": quality_status, "mix": mix_status}, "event signal replay")
        audit.equal(event["quality_delta"], delta, "event quality replay")
        audit.equal(event["mix_js"], mix, "event category replay")
        audit.equal(event["current_count"], len(seen), "event sample count replay")
    alerts = [e for e in events if e["status"]["quality"] == "alert"]
    stable = [e for e in events if e["status"]["quality"] == "stable"]
    audit.require(bool(alerts and stable), "live event journal contains alert and recovery")
    audit.require(timestamp(alerts[0]["at"]) < timestamp(stable[-1]["at"]), "recovery event follows alert")
    fault_started = next(e["at"] for e in data["events"] if e["kind"] == "phase_started" and e.get("run_id") == run["id"] and e["phase"] == "fault")
    controls = data.get("controls", {}).get("results", [])
    audit.equal(len(controls), 2, "positive and negative Jev controls")
    for row in controls:
        audit.equal(row["source"], "fixed evaluator control, not Model output", "control source label")
        audit.evaluation(row, data["config"], control=True)
    audit.require(controls[0]["quality"] >= .8 and controls[1]["quality"] < .8, "judge separates known correct and false controls")
    return {"status": "passed", "verified_at": datetime.now(timezone.utc).isoformat(), "checks": audit.checks,
            "proof_run_id": run["id"], "proof_requests": 60, "completed_requests_audited": len(completed),
            "controls_audited": len(controls), "stages": stage_summaries,
            "fault_drop_pp": drop * 100, "recovery_delta_pp": recovery_delta * 100,
            "alert_at": alerts[0]["at"], "recovered_at": stable[-1]["at"],
            "seconds_from_fault_start_to_alert": (timestamp(alerts[0]["at"]) - timestamp(fault_started)).total_seconds(),
            "control_scores": {r["label"]: r["quality"] for r in controls},
            "scope": "Recomputed from saved provider payloads and event timestamps, without importing application calculations.",
            "limitations": ["Synthetic ticket experiment; quality is judged by Jev, not certified by humans.",
                            "Operational thresholds and a 20-request window; no statistical significance or hard real-time guarantee.",
                            "Hashes check saved-payload consistency; they are not provider-signed receipts.",
                            "Jev probabilities are rounded. Expected-level cross-check allows 0.035 raw levels; native score arithmetic is checked to 1e-9."]}


def markdown(report):
    lines = ["# Live evidence verification", "", f"**{report['status'].upper()}** — {report['checks']:,} independent checks; {report['completed_requests_audited']} completed Model + Jev traces and {report['controls_audited']} fixed-text controls.", "",
             f"Proof run: `{report['proof_run_id']}`. Verified at {report['verified_at']}.", "",
             "| Phase | Fresh calls | Mean quality | Pass rate | Model p50 | Jev p50 |", "|---|---:|---:|---:|---:|---:|"]
    for name, stage in report["stages"].items():
        lines.append(f"| {name} | {stage['count']} | {stage['quality']:.2%} | {stage['pass_rate']:.0%} | {stage['generator_p50_ms']:.0f} ms | {stage['eval_p50_ms']:.0f} ms |")
    lines += ["", f"Wrong policy reduced quality by **{report['fault_drop_pp']:.2f} percentage points**. Restoring the policy finished **{report['recovery_delta_pp']:+.2f} points** from the reference.", "",
              f"The recorded alert occurred at {report['alert_at']}, {report['seconds_from_fault_start_to_alert']:.2f} seconds after fault traffic started, once 20 post-reference evaluations were available. The monitor returned to stable at {report['recovered_at']}.", "",
              "The verifier checks raw Model bytes and hashes, returned model IDs, the answer passed to Jev, the unchanged canonical judging policy and rubric, raw Jev probabilities and native weighted scores, unique responses, matched ticket membership, latency quantiles, category distributions, rolling-window membership, and live event decisions replayed from completion timestamps.", "",
              f"Input artifact SHA-256: `{report.get('artifact_sha256', '')}`", "",
              "Run again with:", "", "```powershell", f".venv/Scripts/python.exe verify_evidence.py {report.get('artifact', 'data/evidence-latest.json')}", "```", "", "## Interpretation", ""]
    lines += [f"- {item}" for item in report["limitations"]]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/verification.json"))
    options = parser.parse_args()
    raw = options.artifact.read_bytes()
    try:
        report = verify(json.loads(raw))
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        report = {"status": "failed", "error": str(exc), "artifact": str(options.artifact)}
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        raise SystemExit(1) from None
    report.update(artifact=str(options.artifact), artifact_sha256=hashlib.sha256(raw).hexdigest())
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    options.output.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
