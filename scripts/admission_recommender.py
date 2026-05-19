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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from scripts.score_engine import load_weights

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
        province: 省份名（中文），用于推断 mode。
        mode: 高考模式（"3+3" / "3+1+2" / "traditional" 等）；若空则从 province 推断。
        track: 传统模式专用 "理" / "文"；新高考模式应留空。
        scores: 各科目原始分 dict，语数外 0-150，选考 0-100，理综/文综 0-300。
        electives: 选考科目名列表。3+3 = 3 项；3+1+2 = 1 首选 + 2 再选。
        favorite_subjects / disliked_subjects: 用户偏好（1-3 项各）。
    """
    province: str
    mode: str = ""
    track: Optional[str] = None
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
        student_primary = next(
            (e for e in student.electives if e in ("物理", "历史")), None,
        )
        if primary_req and student_primary and primary_req != student_primary:
            return False, f"首选要求{primary_req}，你选了{student_primary}"

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
    """Soft filter: 3 layers — absolute weakness, relative偏科, disliked-conflict.

    Layer 1 (absolute, original): soft_thresholds 使用原始分阈值（语数外 0-150；
        选考 0-100；理综/文综 0-300）。任一 key_subject 低于阈值 → not_recommended。
    Layer 2 (v1.9 relative偏科): 学生自身归一化分数均值减 relative_gap 视作偏科线，
        任一 key_subject 权重 ≥ weights_threshold 且学生归一化分低于该线 → 偏科 →
        not_recommended。低分省/弱学生场景下 L1 都过不了时 L2 仍能给出有意义信号。
    Layer 3 (disliked): 用户明确不喜欢的科目若是 key_subject 权重 ≥ weights_threshold
        → 拦截。
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
    admission_data: Optional[dict] = None,
    top_n: Optional[int] = None,
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


def derive_risk_appetite(answers: dict, weights: dict) -> str:
    """Map (Q1, Q18) → appetite level via weights.q1_q18_to_appetite.

    Six possible outputs (v1.7+):
      strong_averse / averse / neutral / seeking / strong_seeking / contradiction

    'contradiction' fires on AC/CA pairs (stable+seeking or above-ceiling+averse).
    SKILL.md cross-validation prompts the user before producing the report.

    If Q01 or Q18 is missing from `answers` (e.g., this is called before the
    questionnaire finishes), both default to "B" → resolves to "neutral".
    Final fallback "neutral" only fires on weights.json misconfiguration.
    """
    q1 = answers.get("Q01") or "B"
    q18 = answers.get("Q18") or "B"
    return weights.get("q1_q18_to_appetite", {}).get(q1 + q18, "neutral")


def main() -> None:
    """CLI: read student profile JSON, print ranked recommendations."""
    parser = argparse.ArgumentParser(description="Major recommendation by gaokao scores")
    parser.add_argument("--input", required=True, help="Path to student profile JSON")
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()
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
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
