"""Major recommendation by gaokao scores + electives + preferences.

Pipeline:
  1. Hard filter (elective compliance / traditional track) → eligible/ineligible
  2. FitScore = weighted avg of normalized scores on major.key_subjects
  3. Soft filter (weak key subjects / disliked conflict) → recommended/not_recommended
  4. Rank by category then score

The output score (0-1) feeds back into the ADI engine as admission_score,
applied as a multiplier: final = ADI × (min_factor + range × admission_score).
"""
import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from scripts.score_engine import derive_risk_appetite, load_weights

_REFERENCES = Path(__file__).resolve().parent.parent / "references"
PROVINCES_PATH = _REFERENCES / "provinces.json"
ADMISSION_PATH = _REFERENCES / "majors_admission_2024.json"

_DEFAULT_SOFT_FILTER_RELATIVE = {
    "weights_threshold": 0.25,
    "relative_gap": 0.15,
    "favorite_fit_bonus": 0.05,
}


def _resolve_soft_filter_relative(weights: dict | None = None) -> dict:
    """Read soft_filter_relative config from weights.json; fall back to defaults."""
    w = weights if weights is not None else load_weights()
    cfg = w.get("soft_filter_relative", {})
    return {
        "weights_threshold": float(cfg.get("weights_threshold",
                                            _DEFAULT_SOFT_FILTER_RELATIVE["weights_threshold"])),
        "relative_gap": float(cfg.get("relative_gap",
                                       _DEFAULT_SOFT_FILTER_RELATIVE["relative_gap"])),
        "favorite_fit_bonus": float(cfg.get("favorite_fit_bonus",
                                             _DEFAULT_SOFT_FILTER_RELATIVE["favorite_fit_bonus"])),
    }

_SUBJECT_MAX = {"语文": 150, "数学": 150, "外语": 150, "理综": 300, "文综": 300}
_ELECTIVE_MAX = 100

_provinces_cache: dict | None = None
_admission_cache: dict | None = None


@dataclass
class StudentProfile:
    """Captures all info needed for admission recommendation.

    Attributes:
        province: Province name (Chinese), used to infer mode.
        mode: Gaokao mode ("3+3" / "3+1+2" / "traditional"); inferred from
            province when empty.
        track: Traditional-mode track "理" / "文"; left empty for new-gaokao modes.
        scores: Raw score per subject. Chinese/Math/English are 0-150,
            electives 0-100, combined science/humanities 0-300.
        electives: Elective subject names. 3+3 = 3 entries; 3+1+2 = 1 primary +
            2 secondary.
        favorite_subjects: User-stated favorites (1-3 entries).
        disliked_subjects: User-stated dislikes (1-3 entries).
    """
    province: str
    mode: str = ""
    track: str | None = None
    scores: dict[str, int] = field(default_factory=dict)
    electives: list[str] = field(default_factory=list)
    favorite_subjects: list[str] = field(default_factory=list)
    disliked_subjects: list[str] = field(default_factory=list)


def load_provinces(path: Path = PROVINCES_PATH) -> dict:
    """Load provinces config, cached."""
    global _provinces_cache
    if _provinces_cache is None:
        with open(path, encoding="utf-8") as f:
            _provinces_cache = json.load(f)
    return _provinces_cache


def load_admission(path: Path = ADMISSION_PATH) -> dict:
    """Load admission config, cached."""
    global _admission_cache
    if _admission_cache is None:
        with open(path, encoding="utf-8") as f:
            _admission_cache = json.load(f)
    return _admission_cache


def resolve_mode(province: str, override: str = "") -> str:
    """Return high-school exam mode for the province, allowing explicit override."""
    if override:
        return override
    pr = load_provinces().get("provinces", {}).get(province)
    return pr["mode"] if pr else ""


def _max_for(subject: str) -> int:
    """Return the maximum raw score for `subject`; defaults to elective max."""
    return _SUBJECT_MAX.get(subject, _ELECTIVE_MAX)


def _normalize_score(subject: str, score: float) -> float:
    """Normalize a raw score to [0, 1]."""
    m = _max_for(subject)
    return 0.0 if m == 0 else min(1.0, max(0.0, score / m))


def is_eligible(student: StudentProfile, admission: dict) -> tuple[bool, str]:
    """Hard filter: elective + traditional-track compliance.

    Returns:
        (eligible, reason_when_false).
    """
    if student.mode == "traditional":
        track_req = admission.get("traditional_track", "both")
        if track_req != "both" and student.track and track_req != student.track:
            return False, f"该专业仅招{track_req}科考生"
        return True, ""

    if student.mode == "3+1+2":
        primary_req = admission.get("required_primary")
        if primary_req and primary_req not in set(student.electives):
            wrong = next(
                (e for e in student.electives if e in ("物理", "历史")), None,
            )
            if wrong:
                return False, f"首选要求{primary_req}，你选了{wrong}"
            return False, f"首选要求{primary_req}，你未选"

    chosen_set = set(student.electives)
    for req in admission.get("required_electives_all", []) or []:
        if req not in chosen_set:
            return False, f"该专业要求选考{req}，你未选"

    any_groups = admission.get("required_electives_any", []) or []
    if any_groups and not any(set(g).issubset(chosen_set) for g in any_groups):
        labels = " 或 ".join(["+".join(g) for g in any_groups])
        return False, f"该专业要求{labels}（任一组）"

    return True, ""


def fit_score(student: StudentProfile, admission: dict,
              weights: dict | None = None) -> float:
    """FitScore: weighted avg of normalized scores on key_subjects.

    v1.9: every favorite_subject that overlaps with this major's key_subjects
    adds favorite_fit_bonus (default 0.05). Final score is capped at 1.0.
    Rationale: align with user's stated affinity even when raw score parity.
    """
    key_subjects = admission.get("key_subjects", {})
    if not key_subjects:
        return 0.5
    score_sum = 0.0
    weight_sum = 0.0
    for subj, w in key_subjects.items():
        raw = student.scores.get(subj)
        if raw is None:
            continue
        score_sum += w * _normalize_score(subj, raw)
        weight_sum += w
    base = 0.5 if weight_sum == 0 else score_sum / weight_sum
    cfg = _resolve_soft_filter_relative(weights)
    bonus = cfg["favorite_fit_bonus"] * sum(
        1 for fav in student.favorite_subjects if fav in key_subjects
    )
    return min(1.0, base + bonus)


def soft_filter(
    student: StudentProfile, admission: dict,
    weights: dict | None = None,
) -> tuple[bool, str]:
    """Soft filter with three layers: absolute, relative, and disliked-conflict.

    Layer 1 (absolute): soft_thresholds uses raw-score floors (Chinese/Math/
        English 0-150; electives 0-100; combined science/humanities 0-300).
        Any key_subject below threshold yields not_recommended.
    Layer 2 (v1.9 relative skew): the student's own normalized-score mean
        minus relative_gap is treated as a skew line; any key_subject with
        weight >= weights_threshold whose normalized score falls below the
        line is flagged. This keeps Layer 1-pass students with a weak core
        subject from slipping through.
    Layer 3 (disliked-conflict): when a user-disliked subject is a key
        subject with weight >= weights_threshold, the major is rejected.
    """
    cfg = _resolve_soft_filter_relative(weights)

    for subj, threshold in (admission.get("soft_thresholds") or {}).items():
        raw = student.scores.get(subj)
        if raw is None:
            continue
        if raw < threshold:
            return False, f"{subj}={raw} 低于建议阈值 {threshold}，不建议本专业"

    norm_scores = {s: _normalize_score(s, v) for s, v in student.scores.items()}
    if norm_scores:
        self_avg = sum(norm_scores.values()) / len(norm_scores)
        gap_line = self_avg - cfg["relative_gap"]
        for subj, weight in (admission.get("key_subjects") or {}).items():
            if weight < cfg["weights_threshold"]:
                continue
            student_norm = norm_scores.get(subj)
            if student_norm is None:
                continue
            if student_norm < gap_line:
                return False, (
                    f"{subj} 是你的相对短板（归一化 {student_norm:.2f} 低于"
                    f"自身均值 {self_avg:.2f} 减 {cfg['relative_gap']}），"
                    f"且本专业权重 {weight:.2f}"
                )

    for disliked in student.disliked_subjects:
        weight = (admission.get("key_subjects") or {}).get(disliked, 0)
        if weight >= cfg["weights_threshold"]:
            return False, f"你不喜欢{disliked}（专业核心，权重 {weight:.2f}）"
    return True, ""


def _categorize(score: float, eligible: bool, recommended: bool) -> str:
    """Bucket a (score, eligibility, recommendation) tuple into a category label."""
    if not eligible:
        return "ineligible"
    if not recommended:
        return "not_recommended"
    if score >= 0.75:
        return "strong"
    if score >= 0.55:
        return "consider"
    return "not_recommended"


_CATEGORY_RANK = {"strong": 0, "consider": 1, "not_recommended": 2, "ineligible": 3}


def _resolve_admission_majors(admission_data: dict) -> dict:
    """Merge `majors` + `_user_additions` into a single major lookup dict.

    v2.1: _user_additions takes precedence on key collisions (per-session
    overrides win), though normally the sets are disjoint by design.
    """
    base = admission_data.get("majors", {}) or {}
    additions = admission_data.get("_user_additions", {}) or {}
    return {**base, **additions}


def recommend(
    student: StudentProfile,
    admission_data: dict | None = None,
    top_n: int | None = None,
) -> list[dict]:
    """Run the recommendation pipeline for all majors in the admission table.

    v2.1: includes `_user_additions` (per-session inferred majors) so the
    recommender surfaces newly-added "其他" majors alongside the 39 baseline.

    Returns:
        List of dicts sorted by category then score desc. Each entry has:
        {name, score, fit, eligible, recommended, reason, category, tags}.
    """
    data = admission_data or load_admission()
    majors = _resolve_admission_majors(data)
    out: list[dict] = []
    for name, info in majors.items():
        eligible, why = is_eligible(student, info)
        fit = fit_score(student, info)
        if eligible:
            recommended, soft_why = soft_filter(student, info)
            reason = soft_why
        else:
            recommended = False
            reason = why
        out.append({
            "name": name,
            "fit": round(fit, 4),
            "score": round(fit, 4),
            "eligible": eligible,
            "recommended": recommended,
            "reason": reason,
            "category": _categorize(fit, eligible, recommended),
            "tags": info.get("tags", []),
        })
    out.sort(key=lambda r: (_CATEGORY_RANK[r["category"]], -r["score"]))
    return out[:top_n] if top_n else out


def main() -> None:
    """CLI: read student profile JSON, print ranked recommendations.

    Wraps the pipeline in a single try/except so callers get a stable
    `[gaokao-adi] ERROR: <Type>: <message>` envelope on stderr with exit
    code 2, matching run_assessment.py's contract.
    """
    parser = argparse.ArgumentParser(description="Major recommendation by gaokao scores")
    parser.add_argument("--input", required=True, help="Path to student profile JSON")
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()
    try:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
        student = StudentProfile(
            province=data.get("province", ""),
            mode=data.get("mode") or resolve_mode(data.get("province", "")),
            track=data.get("track"),
            scores=data.get("scores", {}),
            electives=data.get("electives", []),
            favorite_subjects=data.get("favorite_subjects", []),
            disliked_subjects=data.get("disliked_subjects", []),
        )
        results = recommend(student, top_n=args.top)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"[gaokao-adi] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
