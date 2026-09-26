"""Deterministic edge cases. These fixtures are not live model evidence."""
import math

import pytest

from monitoring import compute_monitor, js_divergence, ks_distance, percentile, summarize


def row(seq, quality=.95, category=None, status="completed"):
    return {"id": f"test-{seq}", "seq": seq, "quality": quality, "status": status,
            "category": category or ["Returns", "Shipping", "Billing", "Account", "Warranty"][(seq - 1) % 5],
            "metrics": {m: quality for m in ("groundedness", "relevance", "policy")},
            "generator_ms": seq * 100, "eval_ms": seq * 10}


def reference(rows):
    return {"request_ids": [r["id"] for r in rows]}


def test_empty_and_minimum_sample_gate():
    empty = compute_monitor([], None)
    assert empty["quality_status"] == "no_reference"
    assert empty["current"]["quality"] is None
    baseline = [row(i) for i in range(1, 21)]
    incomplete = baseline + [row(i, .1) for i in range(21, 40)]
    result = compute_monitor(incomplete, reference(baseline))
    assert result["quality_status"] == "warming_up"
    assert result["current"]["count"] == 19
    complete = compute_monitor(incomplete + [row(40, .1)], reference(baseline))
    assert complete["quality_status"] == "alert"


@pytest.mark.parametrize("score", [None, math.nan, math.inf, -1, 1.1, True, ".9"])
def test_invalid_scores_are_never_samples_or_zeroes(score):
    baseline = [row(i) for i in range(1, 21)]
    result = compute_monitor(baseline + [row(21, score)], reference(baseline))
    assert result["current"]["count"] == 0
    assert result["current"]["quality"] is None
    assert result["quality_ks"] is None


def test_window_uses_request_sequence_and_excludes_reference_and_errors():
    baseline = [row(i) for i in range(11, 31)]
    rows = [row(i) for i in range(1, 60)] + [row(60, .01, status="failed")]
    rows.reverse()  # Completion/arrival order must not define the request window.
    result = compute_monitor(rows, reference(baseline))
    assert result["current_ids"] == [f"test-{i}" for i in range(40, 60)]
    assert set(result["current_ids"]).isdisjoint(reference(baseline)["request_ids"])


@pytest.mark.parametrize("current,expected", [(.871, "stable"), (.87, "watch"), (.801, "watch"), (.8, "alert"), (.79, "alert"), (.99, "stable")])
def test_quality_threshold_boundaries(current, expected):
    baseline = [row(i) for i in range(1, 21)]
    current_rows = [row(i, current) for i in range(21, 41)]
    assert compute_monitor(baseline + current_rows, reference(baseline))["quality_status"] == expected


def test_category_shift_is_separate_from_quality():
    baseline = [row(i) for i in range(1, 21)]
    result = compute_monitor(baseline + [row(i, category="Returns") for i in range(21, 41)], reference(baseline))
    assert result["quality_status"] == "stable"
    assert result["mix_status"] == "alert"
    assert result["mix_js"] == pytest.approx(.6099865470109875)


def test_duplicate_or_missing_reference_ids_do_not_inflate_sample_count():
    baseline = [row(i) for i in range(1, 20)]
    ref = reference(baseline)
    ref["request_ids"] += ["test-1", "missing"]
    result = compute_monitor(baseline + [row(i) for i in range(21, 41)], ref)
    assert result["reference"]["count"] == 19
    assert result["quality_status"] == "warming_up"


def test_distribution_and_latency_conventions():
    assert js_divergence(["a"], ["a"]) == 0
    assert js_divergence(["a"], ["b"]) == 1
    assert js_divergence([], ["a"]) is None
    assert ks_distance([0, 0], [1, 1]) == 1
    assert percentile([100, 200, 300, 400], .95) == pytest.approx(385)
    assert summarize([row(1, .8), row(2, .799)])['pass_rate'] == .5
