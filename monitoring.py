"""Auditable monitoring math. No model calls, randomized scores, or hidden priors."""
from __future__ import annotations

import math
from collections import Counter
from statistics import mean

from fixtures import CATEGORIES

WINDOW = 20
MIN_SAMPLES = 20
PASS_THRESHOLD = 0.80
QUALITY_WATCH = 0.08
QUALITY_ALERT = 0.15
MIX_WATCH = 0.10
MIX_ALERT = 0.20
METRICS = ["groundedness", "relevance", "policy"]


def scored(row):
    value = row.get("quality")
    return row.get("status") == "completed" and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def at_least(value, threshold):
    return value >= threshold or math.isclose(value, threshold, rel_tol=0, abs_tol=1e-12)


def average(values):
    values = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    return mean(values) if values else None


def percentile(values, q):
    values = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not values:
        return None
    pos = (len(values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def js_divergence(a: list, b: list) -> float | None:
    """Jensen-Shannon divergence, base 2, bounded [0, 1]. Zero bins are exact."""
    if not a or not b:
        return None
    labels = set(a) | set(b)
    ca, cb = Counter(a), Counter(b)
    result = 0.0
    for label in labels:
        p, q = ca[label] / len(a), cb[label] / len(b)
        mid = (p + q) / 2
        if p:
            result += 0.5 * p * math.log2(p / mid)
        if q:
            result += 0.5 * q * math.log2(q / mid)
    return max(0.0, min(1.0, result))


def ks_distance(a: list[float], b: list[float]) -> float | None:
    if not a or not b:
        return None
    return max(abs(sum(v <= x for v in a) / len(a) - sum(v <= x for v in b) / len(b)) for x in set(a + b))


def summarize(rows: list[dict]) -> dict:
    valid = [r for r in rows if scored(r)]
    return {
        "count": len(valid),
        "quality": average([r["quality"] for r in valid]),
        "pass_rate": average([float(r["quality"] >= PASS_THRESHOLD) for r in valid]),
        "metrics": {m: average([r.get("metrics", {}).get(m) for r in valid]) for m in METRICS},
        "generator_p50_ms": percentile([r.get("generator_ms") for r in valid], .5),
        "generator_p95_ms": percentile([r.get("generator_ms") for r in valid], .95),
        "eval_p50_ms": percentile([r.get("eval_ms") for r in valid], .5),
        "eval_p95_ms": percentile([r.get("eval_ms") for r in valid], .95),
    }


def compute_monitor(rows: list[dict], baseline: dict | None) -> dict:
    """Use valid post-reference completions, ordered by request sequence."""
    by_id = {r["id"]: r for r in rows}
    reference = [by_id[i] for i in dict.fromkeys((baseline or {}).get("request_ids", [])) if i in by_id and scored(by_id[i])]
    reference_ids = {r["id"] for r in reference}
    last_ref_seq = max((r["seq"] for r in reference), default=0)
    completed = sorted((r for r in rows if scored(r)), key=lambda r: r["seq"])
    current = [r for r in completed if r["id"] not in reference_ids and r["seq"] > last_ref_seq][-WINDOW:] if baseline else []
    ref, cur = summarize(reference), summarize(current)
    delta = cur["quality"] - ref["quality"] if cur["quality"] is not None and ref["quality"] is not None else None
    mix = js_divergence([r["category"] for r in reference], [r["category"] for r in current])
    enough = ref["count"] >= MIN_SAMPLES and cur["count"] >= MIN_SAMPLES
    if not baseline:
        quality_status = mix_status = "no_reference"
    elif not enough:
        quality_status = mix_status = "warming_up"
    else:
        drop = max(0.0, -(delta or 0))
        quality_status = "alert" if at_least(drop, QUALITY_ALERT) else "watch" if at_least(drop, QUALITY_WATCH) else "stable"
        mix_status = "alert" if at_least(mix or 0, MIX_ALERT) else "watch" if at_least(mix or 0, MIX_WATCH) else "stable"
    return {
        "reference": ref,
        "current": cur,
        "current_ids": [r["id"] for r in current],
        "quality_delta": delta,
        "quality_status": quality_status,
        "mix_status": mix_status,
        "mix_js": mix,
        "quality_ks": ks_distance([r["quality"] for r in reference], [r["quality"] for r in current]),
        "enough_samples": enough,
        "window": WINDOW,
        "min_samples": MIN_SAMPLES,
        "thresholds": {"pass": PASS_THRESHOLD, "quality_watch": QUALITY_WATCH, "quality_alert": QUALITY_ALERT, "mix_watch": MIX_WATCH, "mix_alert": MIX_ALERT},
        "segments": [{
            "category": category,
            "reference_count": sum(r["category"] == category for r in reference),
            "current_count": sum(r["category"] == category for r in current),
            "reference_quality": average([r["quality"] for r in reference if r["category"] == category]),
            "current_quality": average([r["quality"] for r in current if r["category"] == category]),
        } for category in CATEGORIES + (["Custom"] if any(r["category"] == "Custom" for r in reference + current) else [])],
    }
