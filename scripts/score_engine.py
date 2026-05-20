"""Personalized ADI score engine.

Computes per-major individualized ADI scores from the baseline ADI table,
weight mappings, and the user's questionnaire answers.

The engine is deterministic and does not make any LLM calls. Contextual
inference for unknown majors (when the user picks "其他") happens in the
conversation layer and is passed in as `_session_overrides`.
"""
import argparse
import json
import math
from pathlib import Path
from typing import Any

_REFERENCES = Path(__file__).resolve().parent.parent / "references"
BASELINE_PATH = _REFERENCES / "baseline_adi.json"
WEIGHTS_PATH = _REFERENCES / "weights.json"

DIMENSION_KEYS: tuple[str, ...] = ("paths", "reach", "correct", "recover")
CLAMP_MIN = 1.0
CLAMP_MAX = 5.0

_baseline_cache: dict | None = None
_weights_cache: dict | None = None


def load_baseline(path: Path = BASELINE_PATH) -> dict:
    """Load the baseline ADI table from JSON (cached)."""
    global _baseline_cache
    if _baseline_cache is None:
        with open(path, encoding="utf-8") as f:
            _baseline_cache = json.load(f)
    return _baseline_cache


def load_weights(path: Path = WEIGHTS_PATH) -> dict:
    """Load weight mappings from JSON (cached)."""
    global _weights_cache
    if _weights_cache is None:
        with open(path, encoding="utf-8") as f:
            _weights_cache = json.load(f)
    return _weights_cache


def grade(total: float) -> str:
    """Map ADI total score to one of the four difficulty labels.

    Args:
        total: ADI total score, expected range 1-625.

    Returns:
        One of "低难", "中等", "较难", "高难".
    """
    th = load_weights()["grade_thresholds"]
    if total > th["low_difficulty"]:
        return "低难"
    if total > th["medium"]:
        return "中等"
    if total > th["high_difficulty"]:
        return "较难"
    return "高难"


def kendall_tau(seq_a: list, seq_b: list) -> float:
    """Compute Kendall tau rank correlation by pairwise counting.

    For n=3 the result is one of {-1.0, -1/3, 0, 1/3, 1.0}.

    Args:
        seq_a: First ordered sequence.
        seq_b: Second ordered sequence over the same item set.

    Returns:
        Tau in [-1.0, 1.0].

    Raises:
        ValueError: If lengths differ or item sets differ.
    """
    if len(seq_a) != len(seq_b):
        raise ValueError("Sequences must have equal length")
    if set(seq_a) != set(seq_b):
        raise ValueError("Sequences must contain the same items")
    n = len(seq_a)
    if n < 2:
        return 1.0
    if len(set(seq_a)) != n or len(set(seq_b)) != n:
        raise ValueError("Sequences must not contain duplicate items")
    idx_b = {item: i for i, item in enumerate(seq_b)}
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            if idx_b[seq_a[i]] < idx_b[seq_a[j]]:
                concordant += 1
            else:
                discordant += 1
    total_pairs = n * (n - 1) // 2
    return (concordant - discordant) / total_pairs


def _clamp(value: float) -> float:
    """Clamp `value` to [CLAMP_MIN, CLAMP_MAX]."""
    return max(CLAMP_MIN, min(CLAMP_MAX, value))


def _resolve_baseline(
    major_name: str,
    baseline: dict,
    session_overrides: dict,
) -> tuple[dict, str]:
    """Resolve baseline ADI for a major.

    Lookup order: _aliases (single-hop) -> main majors -> _user_additions
    -> _session_overrides. The alias step rewrites the lookup key but
    preserves the source label of the resolved entry.

    Returns:
        Tuple (baseline_dict, source_label).

    Raises:
        ValueError: If the major is not found in any source.
    """
    aliases = baseline.get("_aliases", {}) or {}
    canonical = aliases.get(major_name, major_name)
    if isinstance(canonical, str) and canonical in baseline.get("majors", {}):
        return baseline["majors"][canonical], "main"
    user_add = baseline.get("_user_additions", {}) or {}
    if canonical in user_add:
        return user_add[canonical], "user_addition"
    if canonical in session_overrides:
        return session_overrides[canonical], "session_override"
    if major_name in session_overrides:
        return session_overrides[major_name], "session_override"
    raise ValueError(
        f"基础 ADI 缺失：未在 _aliases、主表、_user_additions、_session_overrides "
        f"中找到 <{major_name}>"
    )


def _collect_dim_deltas(
    answers: dict, weights: dict, dimension: str,
) -> list[tuple[str, float]]:
    """Collect every (question_id, delta) contribution to a dimension."""
    contributions: list[tuple[str, float]] = []
    for qid, answer_map in weights["trait_to_dim"].items():
        answer = answers.get(qid)
        if answer is None:
            continue
        deltas = answer_map.get(answer, {})
        if dimension in deltas:
            contributions.append((qid, deltas[dimension]))
    return contributions


def _resolve_sensitivity(weights: dict, level: str) -> dict:
    """Resolve sensitivity multipliers for a level name; fall back to default.

    Returns:
        Dict with keys 'specific' (Q3/Q5/Q7 multiplier) and 'global' (Q12 multiplier).
    """
    levels = weights["resource_sensitivity_levels"]
    entry = levels.get(level)
    if not isinstance(entry, dict) or "specific" not in entry:
        entry = levels.get("default", {"specific": 1.0, "global": 1.0})
    return {
        "specific": float(entry.get("specific", 1.0)),
        "global": float(entry.get("global", 1.0)),
    }


_ABILITY_SCORE_MAP = {"A": 4, "B": 3, "C": 2, "D": 1}


def ability_index(answers: dict, weights: dict) -> str:
    """Derive ability level from Q8-Q11 average.

    Q8/Q9 = learning ability + difficulty tolerance (reach proxy).
    Q10/Q11 = trial-error + adjustment (correct proxy).
    Together they serve as a stand-in for "AI usage capability"
    -- the ability to learn new tools and adapt them to leverage.

    Args:
        answers: Questionnaire answers keyed by question id.
        weights: Loaded weights dict (must contain ability_index_thresholds).

    Returns:
        One of 'high', 'mid', 'low', 'very_low'.
    """
    qids = ("Q08", "Q09", "Q10", "Q11")
    scores = [_ABILITY_SCORE_MAP.get(answers.get(q, "B"), 3) for q in qids]
    avg = sum(scores) / len(scores)
    thresholds = weights.get("ability_index_thresholds", {})
    for level in ("high", "mid", "low", "very_low"):
        if avg >= thresholds.get(level, 0):
            return level
    return "very_low"


def _resolve_ai_impact(
    weights: dict, ai_impact_level: str, ability_level: str, dimension: str,
) -> float:
    """Look up the additive delta for a major's AI impact + user ability + dimension.

    Returns 0.0 for unknown combinations or dimensions outside the configured set
    (reach/paths only; correct/recover are unaffected by AI impact).
    """
    matrix = weights.get("ai_impact_levels", {})
    impact_entry = matrix.get(ai_impact_level)
    if not isinstance(impact_entry, dict):
        impact_entry = matrix.get("neutral", {})
    ability_entry = impact_entry.get(ability_level)
    if not isinstance(ability_entry, dict):
        return 0.0
    value = ability_entry.get(dimension, 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def compute_dimension(
    major_name: str,
    dimension: str,
    base_score: int,
    answers: dict,
    resource_key: str,
    weights: dict,
    sensitivity_level: str = "default",
    ai_impact_level: str = "neutral",
    ability_level: str = "mid",
) -> dict:
    """Compute one dimension's adjusted score with contributor trace.

    Pipeline:
      base × Π(trait_to_dim deltas) × resource (all dims, tiered) × ai_impact
            (reach/paths/correct) × state_global → clamp(1, 5)

    Resource signals (Q3/5/7 specific + Q12 global) fold into all four
    dimensions (v3.16), scaled by per-major resource_sensitivity AND by
    resource_dim_weights (recover 1.0 / reach·paths 0.5 / correct 0.25) —
    resources help "safety net" most, "landing + breadth" next, "skill pivot"
    least. The reach, paths and correct dimensions fold in AI impact scaled by
    the (ai_impact_level × ability_level) 4×4 matrix — this is where the
    polarization effect lives (boost majors lift high-ability users but hurt
    low-ability ones; threatened majors hurt everyone). correct only polarizes
    in boost fields (AI as pivot-accelerator); recover is AI-immune.

    Risk appetite (Q1+Q18) does NOT enter ADI scoring; it only affects
    tie-break ordering and report wording downstream.

    Args:
        major_name: Diagnostic only.
        dimension: One of DIMENSION_KEYS.
        base_score: Baseline ADI score 1-5 for this dimension.
        answers: Questionnaire answers keyed by question id.
        resource_key: Per-major resource answer A/B/C (Q3/Q5/Q7).
        weights: Loaded weights dict.
        sensitivity_level: One of 'low'/'default'/'high'/'decisive'.
        ai_impact_level: One of 'boost'/'neutral'/'disrupted'/'threatened'.
        ability_level: One of 'high'/'mid'/'low'/'very_low', from ability_index().

    Returns:
        Dict with keys 'base', 'multiplier', 'adjusted', 'contributors'.
    """
    multiplier = 1.0
    contrib_records: list[dict[str, Any]] = []
    for qid, delta in _collect_dim_deltas(answers, weights, dimension):
        multiplier *= 1 + delta
        contrib_records.append(
            {"source": qid, "delta": delta, "answer": answers.get(qid)}
        )
    dim_w = weights.get("resource_dim_weights", {}).get(
        dimension, 1.0 if dimension == "recover" else 0.0
    )
    if dim_w:
        sens = _resolve_sensitivity(weights, sensitivity_level)
        base_specific = weights["resource_to_recover"].get(resource_key, 0.0)
        r_delta = base_specific * sens["specific"] * dim_w
        if r_delta != 0 or dimension == "recover":
            multiplier *= 1 + r_delta
            contrib_records.append({
                "source": "resource",
                "delta": round(r_delta, 4),
                "answer": resource_key,
                "sensitivity_factor": sens["specific"],
                "dim_weight": dim_w,
            })
        base_global = weights["global_resource_to_recover"].get(answers.get("Q12", "B"), 0.0)
        q12_delta = base_global * sens["global"] * dim_w
        if q12_delta != 0 or dimension == "recover":
            multiplier *= 1 + q12_delta
            contrib_records.append({
                "source": "Q12",
                "delta": round(q12_delta, 4),
                "answer": answers.get("Q12"),
                "sensitivity_factor": sens["global"],
                "dim_weight": dim_w,
            })
    if dimension in ("reach", "paths", "correct"):
        ai_delta = _resolve_ai_impact(weights, ai_impact_level, ability_level, dimension)
        if ai_delta != 0:
            multiplier *= 1 + ai_delta
            contrib_records.append({
                "source": "ai_impact",
                "delta": round(ai_delta, 4),
                "impact_level": ai_impact_level,
                "ability_level": ability_level,
            })
    state_delta = weights["state_global"].get(answers.get("Q15", "B"), 0.0)
    if state_delta != 0:
        multiplier *= 1 + state_delta
        contrib_records.append(
            {"source": "Q15", "delta": state_delta, "answer": answers.get("Q15")}
        )
    adjusted = _clamp(base_score * multiplier)
    return {
        "base": base_score,
        "multiplier": round(multiplier, 4),
        "adjusted": round(adjusted, 3),
        "contributors": contrib_records,
    }


def _find_top_lift(dim_results: dict) -> dict | None:
    """Find the single largest positive contributor across all dimensions."""
    best: dict | None = None
    for dim, data in dim_results.items():
        for c in data["contributors"]:
            if c["delta"] > 0 and (best is None or c["delta"] > best["delta"]):
                best = {**c, "dimension": dim}
    return best


def _find_top_drag(dim_results: dict) -> dict | None:
    """Find the single largest negative contributor across all dimensions."""
    worst: dict | None = None
    for dim, data in dim_results.items():
        for c in data["contributors"]:
            if c["delta"] < 0 and (worst is None or c["delta"] < worst["delta"]):
                worst = {**c, "dimension": dim}
    return worst


def derive_risk_appetite(answers: dict, weights: dict) -> str:
    """Map Q1+Q18 answers to a risk appetite level.

    Six possible outputs (v1.7+): strong_averse / averse / neutral / seeking /
    strong_seeking / contradiction. 'contradiction' fires on AC/CA pairs.

    Defaults Q01 and Q18 to 'B' when missing so the function is safe to call
    before the questionnaire finishes. Final 'neutral' fallback only fires
    on misconfigured weights.json.

    Args:
        answers: Questionnaire answers keyed by question id.
        weights: Loaded weights dict (must contain q1_q18_to_appetite).

    Returns:
        Appetite level string.
    """
    q1 = answers.get("Q01") or "B"
    q18 = answers.get("Q18") or "B"
    return weights.get("q1_q18_to_appetite", {}).get(q1 + q18, "neutral")


# Legacy alias for internal call sites.
_resolve_risk_appetite = derive_risk_appetite


def _resolve_tie_break_weights(
    appetite: str, weights: dict,
) -> dict[str, float] | None:
    """Look up the 4-dim weight vector for tie-break scoring.

    Returns None when the appetite has no row in the matrix — this
    includes 'contradiction' (intentionally absent) and any unknown level.
    """
    matrix = weights.get("appetite_tie_break_weights", {})
    entry = matrix.get(appetite)
    if not isinstance(entry, dict):
        return None
    return {dim: float(entry.get(dim, 0.0)) for dim in DIMENSION_KEYS}


def _compute_tie_break_score(major_result: dict, weights_vec: dict[str, float]) -> float:
    """Weighted sum over 4 dimensions: score = Σ(w[dim] × adjusted[dim])."""
    dims = major_result["dimensions"]
    return sum(weights_vec.get(dim, 0.0) * dims[dim]["adjusted"] for dim in DIMENSION_KEYS)


def _band_index(total: float, pct: float) -> int:
    """Bucket a total into log-spaced relative bands of width ~pct.

    Two totals within roughly `pct` relative distance land in the same band
    (modulo boundary effects), making them eligible for personal_fit reordering.
    """
    if total <= 0 or pct <= 0:
        return 0
    return math.floor(math.log(total) / math.log(1 + pct))


def _appetite_sort_key(major_result: dict, band_pct: float) -> tuple:
    """Build rank key: objective band (by total) first, personal_fit within band.

    v3.16: appetite no longer fires only on exact ties. Majors whose totals
    fall in the same ~pct-wide relative band are ordered by personal_fit
    (appetite-weighted Σ); across bands, pure total dominates. The displayed
    total/grade are unaffected — appetite only reorders near-equal majors.
    For contradiction / missing config, personal_fit is 0 so a same-band group
    keeps stable input order.
    """
    total = major_result["total"]
    pf = major_result.get("personal_fit", 0.0)
    return (-_band_index(total, band_pct), -pf)


def _appetite_effect(
    majors_result: dict, algorithm_rank: list[str], band_pct: float,
) -> tuple[bool, str | None, str | None]:
    """Check whether the appetite tie-break actually reordered the majors.

    Re-sorts on the objective band alone (personal_fit neutralized) while keeping
    the same stable input-order fallback as ``algorithm_rank``. The only difference
    between the two orderings is the personal_fit key, so a mismatch isolates the
    risk appetite's real effect on the ranking — letting the report avoid claiming
    a tie-break "decided" the order when it was in fact a no-op.

    Args:
        majors_result: Per-major result dicts keyed by name (insertion == input order).
        algorithm_rank: Final appetite-aware ranking produced by ``compute_all``.
        band_pct: Relative band width used by ``_band_index``.

    Returns:
        Tuple ``(changed, promoted, demoted)``. When ``changed`` is True, ``promoted``
        is the major appetite pushed up and ``demoted`` the one it leapfrogged at the
        first differing position; both are None when the ranking was unchanged.
    """
    input_order = list(majors_result.keys())
    baseline = sorted(
        input_order,
        key=lambda n: -_band_index(majors_result[n]["total"], band_pct),
    )
    if baseline == algorithm_rank:
        return False, None, None
    for promoted, demoted in zip(algorithm_rank, baseline):
        if promoted != demoted:
            return True, promoted, demoted
    return True, None, None


def _apply_admission_blend(adi_total: float, admission_score: float | None,
                            weights: dict) -> tuple[float, float]:
    """Combine raw ADI with admission_score → final.

    Returns:
        (final_score, blend_factor). blend_factor=1 when no admission_score provided.
    """
    if admission_score is None:
        return adi_total, 1.0
    blend = weights.get("admission_blend", {"min_factor": 0.7, "range": 0.3})
    min_factor = float(blend.get("min_factor", 0.7))
    rng = float(blend.get("range", 0.3))
    factor = min_factor + rng * max(0.0, min(1.0, admission_score))
    return round(adi_total * factor, 2), round(factor, 4)


def compute_major(
    major_name: str,
    resource_key: str,
    answers: dict,
    baseline: dict,
    weights: dict,
    session_overrides: dict,
    admission_score: float | None = None,
) -> dict:
    """Compute personalized ADI for a single major."""
    base_data, source = _resolve_baseline(major_name, baseline, session_overrides)
    sensitivity_level = base_data.get("resource_sensitivity", "default")
    ai_impact_level = base_data.get("ai_impact", "neutral")
    ability_level = ability_index(answers, weights)
    appetite = _resolve_risk_appetite(answers, weights)
    dim_results = {
        dim: compute_dimension(
            major_name, dim, int(base_data[dim]), answers, resource_key, weights,
            sensitivity_level, ai_impact_level, ability_level,
        )
        for dim in DIMENSION_KEYS
    }
    adi_total = 1.0
    for dim in DIMENSION_KEYS:
        adi_total *= dim_results[dim]["adjusted"]
    adi_total = round(adi_total, 2)
    final_total, blend_factor = _apply_admission_blend(adi_total, admission_score, weights)
    bottleneck = min(DIMENSION_KEYS, key=lambda d: dim_results[d]["adjusted"])
    tie_weights = _resolve_tie_break_weights(appetite, weights)
    personal_fit = (
        round(_compute_tie_break_score({"dimensions": dim_results}, tie_weights), 3)
        if tie_weights else 0.0
    )
    return {
        "name": major_name,
        "source": source,
        "resource_sensitivity": sensitivity_level,
        "ai_impact": ai_impact_level,
        "ability_level": ability_level,
        "risk_appetite": appetite,
        "personal_fit": personal_fit,
        "admission_score": admission_score,
        "admission_blend_factor": blend_factor,
        "adi_total": adi_total,
        "dimensions": dim_results,
        "total": final_total,
        "grade": grade(final_total),
        "bottleneck": bottleneck,
        "top_lift": _find_top_lift(dim_results),
        "top_drag": _find_top_drag(dim_results),
        "rationale": base_data.get("rationale"),
    }


_EXTRAS_DEFAULT_N = 3


def compute_extras(
    input_data: dict,
    excluded: set[str],
    baseline: dict,
    weights: dict,
    n: int = _EXTRAS_DEFAULT_N,
) -> tuple[list[dict], str | None]:
    """Compute top-N extra major recommendations excluding the user's selection.

    v2.0: Candidate pool is built one of two ways:

    1. If `input_data["_admission_pool"]` is provided (推荐分支 ran admission_
       recommender and dumped the full ranked output), candidates = entries
       with category in {strong, consider} excluding `excluded`. Each carries
       its admission_score.
    2. Fallback (直选分支): candidates = all baseline majors (excluding
       `excluded`), no admission_score, no eligibility check. Returns a
       warning string so the report can flag "未做选科合规校验".

    Each candidate is run through compute_major with resource_key="C" (default,
    since user only provides resource answers for selected majors), then sorted
    by final total desc.

    Returns:
        (extras_list, warning) — warning is None when admission_pool is used.
    """
    admission_pool = input_data.get("_admission_pool") or []
    answers = input_data["answers"]
    session_overrides = input_data.get("_session_overrides", {}) or {}

    if admission_pool:
        candidates = [
            (entry["name"], entry.get("score"))
            for entry in admission_pool
            if entry.get("name") not in excluded
            and entry.get("category") in ("strong", "consider")
        ]
        warning: str | None = None
    else:
        all_majors = {
            **(baseline.get("majors", {}) or {}),
            **(baseline.get("_user_additions", {}) or {}),
        }
        candidates = [
            (name, None) for name in all_majors.keys()
            if name not in excluded
        ]
        warning = (
            "未走推荐分支，无成绩与选科合规校验——下方推荐仅按 ADI 总分排序，"
            "可能包含你不符合选科要求的专业。"
        )

    results: list[dict] = []
    for name, adm_score in candidates:
        try:
            res = compute_major(
                name, "C", answers, baseline, weights, session_overrides,
                admission_score=adm_score,
            )
            results.append(res)
        except ValueError as e:
            # Only swallow "major not in any baseline source" errors;
            # surface every other ValueError so real bugs are not masked.
            if "基础 ADI 缺失" in str(e):
                continue
            raise
    results.sort(key=lambda r: -r["total"])
    return results[:n], warning


def compute_all(input_data: dict) -> dict:
    """Top-level entry: compute personalized ADI for all selected majors.

    Args:
        input_data: dict with keys 'majors', 'answers', optional '_session_overrides'.

    Returns:
        Dict with keys 'majors', 'subjective_rank', 'algorithm_rank',
        'kendall_tau', 'meta', and (v2.0) 'extras' + 'extras_warning'.
    """
    baseline = load_baseline()
    weights = load_weights()
    session_overrides = input_data.get("_session_overrides", {}) or {}
    admission_scores = input_data.get("_admission_scores", {}) or {}
    answers = input_data["answers"]
    majors_input = input_data["majors"]

    majors_result: dict[str, dict] = {}
    session_inferred: list[str] = []
    for entry in majors_input:
        name = entry["name"]
        resource_key = entry.get("resource", "C")
        admission_score = admission_scores.get(name)
        result = compute_major(
            name, resource_key, answers, baseline, weights, session_overrides,
            admission_score=admission_score,
        )
        majors_result[name] = result
        if result["source"] == "session_override":
            session_inferred.append(name)

    subjective_rank = [
        e["name"] for e in sorted(majors_input, key=lambda x: x["rank"])
    ]
    appetite = _resolve_risk_appetite(answers, weights)
    tie_break_weights = _resolve_tie_break_weights(appetite, weights)
    band_pct = weights.get("appetite_rank_band", {}).get("pct", 0.05)
    algorithm_rank = sorted(
        majors_result.keys(),
        key=lambda n: _appetite_sort_key(majors_result[n], band_pct),
    )
    tau = kendall_tau(subjective_rank, algorithm_rank)
    appetite_changed, appetite_promoted, appetite_demoted = _appetite_effect(
        majors_result, algorithm_rank, band_pct,
    )

    meta = {
        "session_inferred_majors": session_inferred,
        "low_confidence": len(session_inferred) >= 2,
        "preference_path": answers.get("Q01"),
        "risk_tone": answers.get("Q18"),
        "school_vs_major": answers.get("Q16"),
        "city_emphasis": answers.get("Q17"),
        "risk_appetite": appetite,
        "appetite_contradiction": appetite == "contradiction",
        "appetite_tie_break_weights": tie_break_weights,
        "appetite_changed_order": appetite_changed,
        "appetite_promoted": appetite_promoted,
        "appetite_demoted": appetite_demoted,
        "admission_scores_used": bool(admission_scores),
    }
    excluded = {e["name"] for e in majors_input}
    extras, extras_warning = compute_extras(input_data, excluded, baseline, weights)
    return {
        "majors": majors_result,
        "subjective_rank": subjective_rank,
        "algorithm_rank": algorithm_rank,
        "kendall_tau": round(tau, 4),
        "meta": meta,
        "extras": extras,
        "extras_warning": extras_warning,
        "student_context": input_data.get("_student_profile"),
    }


def main() -> None:
    """CLI entry: read input JSON, compute, write or print result JSON."""
    parser = argparse.ArgumentParser(description="Compute personalized ADI scores")
    parser.add_argument("--input", required=True, help="Path to input JSON")
    parser.add_argument("--output", help="Path to output JSON (default: stdout)")
    args = parser.parse_args()
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    result = compute_all(data)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
