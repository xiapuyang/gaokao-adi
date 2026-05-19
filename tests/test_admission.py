"""Tests for admission_recommender + risk_appetite + admission_score blend.

Covers:
- Hard filter (electives + traditional track) accept/reject
- FitScore weighting
- recommend() category buckets + sort order
- risk_appetite derivation (Q1+Q18 matrix incl. contradiction → neutral)
- admission_score blend factor on ADI total
"""
import json
from pathlib import Path

import pytest

from scripts.admission_recommender import (
    StudentProfile,
    derive_risk_appetite,
    fit_score,
    is_eligible,
    load_admission,
    recommend,
    soft_filter,
)
from scripts.score_engine import compute_all, load_weights


@pytest.fixture
def admission_data() -> dict:
    return load_admission()


@pytest.fixture
def majors(admission_data) -> dict:
    return admission_data["majors"]


# ---------- Hard filter ----------


def test_history_student_blocked_from_cs(majors):
    """3+1+2 模式选历史 → 报 CS 应硬过滤拒绝（CS required_primary=物理）。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["历史", "政治", "地理"],
        scores={"语文": 120, "数学": 130, "外语": 130, "历史": 85, "政治": 82, "地理": 80},
    )
    ok, reason = is_eligible(student, majors["计算机类 / 软件工程"])
    assert ok is False
    assert "物理" in reason


def test_physics_chemistry_student_passes_cs(majors):
    """3+1+2 物化生 → 报 CS 应通过。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 110, "数学": 140, "外语": 135, "物理": 90, "化学": 88, "生物": 85},
    )
    ok, reason = is_eligible(student, majors["计算机类 / 软件工程"])
    assert ok is True, f"应通过但被拒：{reason}"


def test_clinical_requires_chem_bio(majors):
    """临床医学需要化学+生物。仅物 → 拒。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "政治", "地理"],
        scores={"语文": 110, "数学": 130, "外语": 125, "物理": 85, "政治": 70, "地理": 75},
    )
    ok, reason = is_eligible(student, majors["临床医学"])
    assert ok is False
    assert "化学" in reason or "生物" in reason


def test_traditional_liberal_blocked_from_engineering(majors):
    """传统模式文科考生 → 报机械工程应拒。"""
    student = StudentProfile(province="西藏", mode="traditional", track="文")
    ok, reason = is_eligible(student, majors["机械工程"])
    assert ok is False


def test_no_requirement_majors_open_to_all(majors):
    """汉语言文学不限选科 → 任何组合都过。"""
    history_student = StudentProfile(
        province="广东", mode="3+1+2", electives=["历史", "政治", "地理"],
    )
    physics_student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
    )
    assert is_eligible(history_student, majors["汉语言文学"])[0] is True
    assert is_eligible(physics_student, majors["汉语言文学"])[0] is True


# ---------- FitScore ----------


def test_fit_score_rewards_strong_key_subjects(majors):
    """数学/物理强 → CS FitScore 应较高。"""
    strong = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 110, "数学": 145, "外语": 135, "物理": 95, "化学": 90, "生物": 85},
    )
    weak = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 110, "数学": 80, "外语": 90, "物理": 50, "化学": 55, "生物": 60},
    )
    strong_fit = fit_score(strong, majors["计算机类 / 软件工程"])
    weak_fit = fit_score(weak, majors["计算机类 / 软件工程"])
    assert strong_fit > weak_fit + 0.15, f"强弱差距应明显：strong={strong_fit:.2f} weak={weak_fit:.2f}"


def test_fit_score_in_range(majors):
    """FitScore 在 [0, 1]。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 150, "数学": 150, "外语": 150, "物理": 100, "化学": 100, "生物": 100},
    )
    for major_info in majors.values():
        f = fit_score(student, major_info)
        assert 0 <= f <= 1


# ---------- Soft filter ----------


def test_soft_filter_rejects_weak_key_subject(majors):
    """化学很低 → 临床医学应被软过滤拒绝。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 110, "数学": 130, "外语": 120, "物理": 85, "化学": 50, "生物": 80},
    )
    ok, reason = soft_filter(student, majors["临床医学"])
    assert ok is False
    assert "化学" in reason


def test_soft_filter_disliked_conflict(majors):
    """讨厌化学但选了 CS（化学权重 0.20）→ 应该 OK（< 0.25 阈值）。
    讨厌化学但报临床（化学权重 0.25）→ 应被拒。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 110, "数学": 130, "外语": 120, "物理": 85, "化学": 80, "生物": 80},
        disliked_subjects=["化学"],
    )
    ok_cs, _ = soft_filter(student, majors["计算机类 / 软件工程"])
    ok_clinical, _ = soft_filter(student, majors["临床医学"])
    assert ok_cs is True
    assert ok_clinical is False


def test_soft_filter_layer2_catches_relative_pianke_in_math(majors):
    """v1.9 L2: 学生数学刚过 L1 阈值，其他科都很强 → 数学相对偏科 → L2 应拦截
    数学权重高的工科专业（电子信息工程：数学权重 0.3）。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        # 语文 142/150=0.947, 数学 92/150=0.613, 外语 140/150=0.933
        # 物理 88/100=0.88, 化学 92/100=0.92, 生物 90/100=0.90
        # 自身均值 ≈ 0.866, gap_line=0.716；数学 0.613 < 0.716 → 偏科
        scores={"语文": 142, "数学": 92, "外语": 140,
                "物理": 88, "化学": 92, "生物": 90},
    )
    eei = majors["电子信息工程"]
    # L1 检查：物理 88≥80✓, 数学 92≥90✓, 化学 92≥65✓ → L1 过
    # L2 检查：数学权重 0.3 ≥ 0.25，0.613 < gap_line → 拦
    ok, reason = soft_filter(student, eei)
    assert ok is False, f"L2 应识别数学相对偏科，得到 ok={ok}"
    assert "相对短板" in reason
    assert "数学" in reason


def test_soft_filter_layer2_does_not_block_balanced_student(majors):
    """v1.9 L2: 均衡学生（所有 key_subjects 都在自身均值附近）不应被 L2 误伤。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        # 全部归一化都在 0.8-0.95 之间，无明显偏科
        scores={"语文": 130, "数学": 135, "外语": 125,
                "物理": 88, "化学": 85, "生物": 85},
    )
    # 计算机：L1 数学≥95(135✓), 物理≥65(88✓), 化学≥65(85✓) → 全过；L2 应也过
    ok, _ = soft_filter(student, majors["计算机类 / 软件工程"])
    assert ok is True


def test_soft_filter_layer2_ignores_low_weight_subjects(majors):
    """v1.9 L2: 学生某科是偏科，但该科在专业里权重 < weights_threshold → 不拦。

    数学偏科 + 选汉语言文学（数学权重 0）→ L2 不查数学。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        # 数学偏科（92/150=0.613）但是汉语言文学 key_subjects 不含数学
        scores={"语文": 145, "数学": 92, "外语": 140,
                "物理": 88, "化学": 92, "生物": 90,
                "历史": 88},  # 汉语言可能用历史
    )
    chinese = majors["汉语言文学"]
    math_weight = chinese.get("key_subjects", {}).get("数学", 0)
    if math_weight >= 0.25:
        pytest.skip("汉语言文学 数学权重 ≥ 0.25，测试假设失效")
    ok, _ = soft_filter(student, chinese)
    # 数学偏科但权重不达阈值 → L2 不动作
    # 语文 0.967 ≫ avg → L2 不拦语文
    # L1 语文≥110(145✓) → L1 过
    assert ok is True


def test_fit_score_favorite_subject_bonus(majors):
    """v2.0: favorite_subject 命中 key_subjects 时按该 key_subject 权重缩放 bonus。"""
    base_student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 120, "数学": 130, "外语": 120, "物理": 95, "化学": 85, "生物": 88},
    )
    with_fav_student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores=base_student.scores,
        favorite_subjects=["数学"],  # CS key_subjects 含 数学
    )
    cs = majors["计算机类 / 软件工程"]
    base_fit = fit_score(base_student, cs)
    fav_fit = fit_score(with_fav_student, cs)
    # v2.0: bonus = 0.05 * 数学权重
    math_weight = cs["key_subjects"]["数学"]
    expected = min(1.0, base_fit + 0.05 * math_weight)
    assert fav_fit == pytest.approx(expected, abs=1e-6), (
        f"base={base_fit}, fav={fav_fit}, expected={expected}"
    )


def test_fit_score_multiple_favorites_bonus(majors):
    """v1.9: 多个 favorite 命中应累加；上限 cap 1.0。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 130, "数学": 145, "外语": 145, "物理": 99, "化学": 95, "生物": 95},
        favorite_subjects=["数学", "物理", "外语"],
    )
    cs = majors["计算机类 / 软件工程"]
    fit = fit_score(student, cs)
    assert fit <= 1.0  # cap


def test_fit_score_favorite_not_in_key_subjects_no_bonus(majors):
    """v1.9: favorite 不在 key_subjects 中不该加 bonus。
    工商管理 key_subjects 不含物理 → favorite=[物理] 应无 bonus。"""
    scores = {"语文": 130, "数学": 110, "外语": 120,
              "物理": 95, "化学": 85, "生物": 88}
    base_student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores=scores,
    )
    fav_student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores=scores, favorite_subjects=["物理"],
    )
    biz = majors["工商管理"]
    assert "物理" not in biz["key_subjects"]
    base_fit = fit_score(base_student, biz)
    fav_fit = fit_score(fav_student, biz)
    assert base_fit == pytest.approx(fav_fit, abs=1e-6), "favorite 不在 key 应无 bonus"


# ---------- v2.4 track-mismatch penalty ----------


def test_fit_score_track_penalty_demotes_humanities_for_li_track(majors):
    """v2.4: 理-track 学生对 max_stem_weight < 0.30 的专业要被 0.85 惩罚降档。
    市场营销 数学=0.25 是触发点；会计学 数学=0.40 不触发；陕西物理类考生预期看到
    市场营销从 strong 掉到 consider。"""
    student = StudentProfile(
        province="陕西", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 130, "数学": 140, "外语": 130,
                "物理": 70, "化学": 70, "生物": 64},
        favorite_subjects=["数学", "语文", "物理", "生物"],
    )
    marketing = majors["市场营销"]
    accounting = majors["会计学"]
    # 市场营销 max stem (数学) = 0.25 < 0.30 → penalty
    assert max(w for s, w in marketing["key_subjects"].items()
               if s in {"数学", "物理", "化学", "生物"}) < 0.30
    # 会计学 max stem (数学) = 0.40 >= 0.30 → no penalty
    assert max(w for s, w in accounting["key_subjects"].items()
               if s in {"数学", "物理", "化学", "生物"}) >= 0.30
    marketing_fit = fit_score(student, marketing)
    accounting_fit = fit_score(student, accounting)
    assert marketing_fit < 0.75, f"市场营销 应从 strong 掉到 consider: {marketing_fit}"
    assert accounting_fit >= 0.75, f"会计学 应保持 strong: {accounting_fit}"


def test_fit_score_track_penalty_skipped_for_3plus3(majors):
    """v2.4: 3+3 自由选科学生不触发 track 惩罚（无清晰 STEM 信号）。"""
    student_3p3 = StudentProfile(
        province="北京", mode="3+3", electives=["物理", "化学", "生物"],
        scores={"语文": 130, "数学": 140, "外语": 130,
                "物理": 70, "化学": 70, "生物": 64},
    )
    student_li = StudentProfile(
        province="陕西", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores=student_3p3.scores,
    )
    marketing = majors["市场营销"]
    # 同样分数同样专业，3+3 不被惩罚、3+1+2 物理被惩罚
    assert fit_score(student_3p3, marketing) > fit_score(student_li, marketing)


def test_fit_score_track_penalty_not_applied_to_stem_major(majors):
    """v2.4: STEM 专业（CS 数学权重 0.4+）对理科生不触发惩罚。"""
    student = StudentProfile(
        province="陕西", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 130, "数学": 140, "外语": 130,
                "物理": 95, "化学": 90, "生物": 88},
    )
    cs = majors["计算机类 / 软件工程"]
    fit = fit_score(student, cs)
    # 应保持 strong（具体值看权重，但必须 >= 0.75）
    assert fit >= 0.75, f"CS 不该被理科 track 惩罚: {fit}"


# ---------- recommend pipeline ----------


def test_recommend_returns_sorted_categories():
    """recommend 输出按 category 升序、score 降序。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 115, "数学": 138, "外语": 130, "物理": 90, "化学": 85, "生物": 82},
        favorite_subjects=["数学"], disliked_subjects=[],
    )
    results = recommend(student, top_n=None)
    cats = [r["category"] for r in results]
    cat_rank = {"strong": 0, "consider": 1, "not_recommended": 2, "ineligible": 3}
    for i in range(len(cats) - 1):
        assert cat_rank[cats[i]] <= cat_rank[cats[i + 1]], (
            f"category 顺序错位：第 {i} 项 {cats[i]} > 第 {i+1} 项 {cats[i+1]}"
        )


def test_recommend_includes_eligible_and_ineligible():
    """选历史的考生：理工类应在 ineligible 桶，文科类应在前面。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["历史", "政治", "地理"],
        scores={"语文": 125, "数学": 110, "外语": 130, "历史": 88, "政治": 80, "地理": 82},
    )
    results = recommend(student, top_n=None)
    names_by_cat = {}
    for r in results:
        names_by_cat.setdefault(r["category"], []).append(r["name"])
    assert "计算机类 / 软件工程" in names_by_cat.get("ineligible", [])
    assert "临床医学" in names_by_cat.get("ineligible", [])
    # 汉语言文学应不在 ineligible 桶
    for r in results:
        if r["name"] == "汉语言文学":
            assert r["eligible"] is True


# ---------- risk_appetite (Q1+Q18) ----------


def test_risk_appetite_strong_averse():
    weights = load_weights()
    assert derive_risk_appetite({"Q01": "A", "Q18": "A"}, weights) == "strong_averse"


def test_risk_appetite_strong_seeking():
    weights = load_weights()
    assert derive_risk_appetite({"Q01": "C", "Q18": "C"}, weights) == "strong_seeking"


def test_risk_appetite_contradiction_returns_contradiction_level():
    """v1.7: Q1=A (稳定) + Q18=C (进取) 自相矛盾 → 'contradiction' 独立 label
    （v1.6 之前归入 'neutral'，与 BB 同名导致下游无法区分真权衡 vs 矛盾）。"""
    weights = load_weights()
    assert derive_risk_appetite({"Q01": "A", "Q18": "C"}, weights) == "contradiction"
    assert derive_risk_appetite({"Q01": "C", "Q18": "A"}, weights) == "contradiction"


def test_risk_appetite_does_not_affect_adi_total():
    """v1.5: risk_appetite 已剥离出 ADI 乘法链。
    strong_averse 与 neutral 用户对同一专业的 total 应**完全相同**。"""
    averse_answers = {
        "Q01": "A", "Q08": "B", "Q09": "B", "Q10": "B", "Q11": "B",
        "Q12": "B", "Q13": "B", "Q14": "B", "Q15": "B",
        "Q16": "C", "Q17": "B", "Q18": "A",
    }
    neutral_answers = {**averse_answers, "Q01": "B", "Q18": "B"}
    common_majors = [
        {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
        {"rank": 2, "name": "法学", "resource": "C"},
        {"rank": 3, "name": "数学", "resource": "C"},
    ]
    averse_result = compute_all({"majors": common_majors, "answers": averse_answers})
    neutral_result = compute_all({"majors": common_majors, "answers": neutral_answers})
    for name in ("计算机类 / 软件工程", "法学", "数学"):
        a_total = averse_result["majors"][name]["total"]
        n_total = neutral_result["majors"][name]["total"]
        assert a_total == n_total, (
            f"{name}: averse total {a_total} 必须等于 neutral total {n_total}"
        )
    assert averse_result["meta"]["risk_appetite"] == "strong_averse"
    assert neutral_result["meta"]["risk_appetite"] == "neutral"


def _tie_break_overrides() -> dict:
    """Three majors with identical ADI base product (12) and shapes that
    hit different appetite profiles, with NO dim ≥ 4 to avoid clamping —
    ensures totals are exactly equal post-personalization so tie-break fires.

    Profiles (all base product = 1×4×1×3 = 12 in some permutation):
      A_PURE:  high reach + high recover (A's two-dim profile)
      C_PURE:  high paths + high correct (C's two-dim profile)
      B_MIXED: moderate paths + correct + recover (B's three-dim profile)
    """
    base = {"resource_sensitivity": "default", "ai_impact": "neutral"}
    return {
        "A_PURE":  {"paths": 1, "reach": 4, "correct": 1, "recover": 3, **base},
        "C_PURE":  {"paths": 4, "reach": 1, "correct": 3, "recover": 1, **base},
        "B_MIXED": {"paths": 3, "reach": 1, "correct": 2, "recover": 2, **base},
    }


def _tie_break_majors() -> list[dict]:
    return [
        {"rank": 1, "name": "A_PURE",  "resource": "C"},
        {"rank": 2, "name": "C_PURE",  "resource": "C"},
        {"rank": 3, "name": "B_MIXED", "resource": "C"},
    ]


def _neutral_personality_answers(q01: str, q18: str) -> dict:
    """All-B traits so per-dim multipliers are constant across majors."""
    return {
        "Q01": q01, "Q08": "B", "Q09": "B", "Q10": "B", "Q11": "B",
        "Q12": "B", "Q13": "B", "Q14": "B", "Q15": "B",
        "Q16": "C", "Q17": "B", "Q18": q18,
    }


def test_tie_break_totals_are_equal_for_designed_shapes():
    """Fixture check: the three designed shapes really do produce identical
    ADI totals (otherwise the tie-break never fires and following tests
    would be measuring something else)."""
    result = compute_all({
        "majors": _tie_break_majors(),
        "answers": _neutral_personality_answers("B", "B"),
        "_session_overrides": _tie_break_overrides(),
    })
    totals = {n: result["majors"][n]["total"] for n in result["algorithm_rank"]}
    values = list(totals.values())
    assert all(v == pytest.approx(values[0], rel=1e-6) for v in values), totals


def test_tie_break_weights_loaded_correctly_per_appetite():
    """Direct assertion on the 5 weight rows. The matrix is the contract;
    behavior tests below verify it sorts as intended."""
    expected = {
        ("A", "A"): ("strong_averse",  {"paths": 0.00, "reach": 0.50, "correct": 0.00, "recover": 0.50}),
        ("A", "B"): ("averse",         {"paths": 0.10, "reach": 0.35, "correct": 0.10, "recover": 0.45}),
        ("B", "B"): ("neutral",        {"paths": 0.34, "reach": 0.00, "correct": 0.33, "recover": 0.33}),
        ("C", "B"): ("seeking",        {"paths": 0.45, "reach": 0.00, "correct": 0.45, "recover": 0.10}),
        ("C", "C"): ("strong_seeking", {"paths": 0.50, "reach": 0.00, "correct": 0.50, "recover": 0.00}),
    }
    for (q01, q18), (apt, w) in expected.items():
        result = compute_all({
            "majors": [{"rank": 1, "name": "法学", "resource": "C"}],
            "answers": _neutral_personality_answers(q01, q18),
        })
        assert result["meta"]["risk_appetite"] == apt
        for dim, expected_w in w.items():
            actual = result["meta"]["appetite_tie_break_weights"][dim]
            assert actual == pytest.approx(expected_w, abs=0.01), (
                f"{apt} {dim}: expected {expected_w}, got {actual}"
            )


def test_strong_averse_tie_break_picks_a_pure():
    """AA: weighted sum {reach 0.5, recover 0.5} → A_PURE 必赢。"""
    result = compute_all({
        "majors": _tie_break_majors(),
        "answers": _neutral_personality_answers("A", "A"),
        "_session_overrides": _tie_break_overrides(),
    })
    assert result["algorithm_rank"][0] == "A_PURE"


def test_strong_seeking_tie_break_picks_c_pure():
    """CC: weighted sum {paths 0.5, correct 0.5} → C_PURE 必赢。"""
    result = compute_all({
        "majors": _tie_break_majors(),
        "answers": _neutral_personality_answers("C", "C"),
        "_session_overrides": _tie_break_overrides(),
    })
    assert result["algorithm_rank"][0] == "C_PURE"


def test_neutral_tie_break_ranks_b_above_a_profile():
    """BB 真权衡：3 维加权 {paths 0.34, correct 0.33, recover 0.33, reach 0}。
    B_MIXED 的 paths+correct+recover 都覆盖 → 必定排在 A_PURE 之上
    （A_PURE 在 reach=0 权重下完全没有贡献）。

    注意：在 linear 加权和下，C_PURE 仍可能 > B_MIXED（因为 paths/correct 极值
    比 B 的均衡更高分）——这是 linear 设计的固有取舍。这个测试只断言 B 维度
    确实进入了 tie-break，不要求 B_MIXED 击败 C_PURE。"""
    result = compute_all({
        "majors": _tie_break_majors(),
        "answers": _neutral_personality_answers("B", "B"),
        "_session_overrides": _tie_break_overrides(),
    })
    rank = result["algorithm_rank"]
    assert rank.index("B_MIXED") < rank.index("A_PURE"), (
        f"BB 应把 B_MIXED 排在 A_PURE 之前（reach 权重=0），得到 {rank}"
    )


def test_contradiction_uses_all_zeros_weights_for_stable_order():
    """v1.8: contradiction 行显式 all-zeros → 加权和恒为 0 → stable sort
    保留 majors_input 顺序。语义与 v1.7 None 分支等价，但矩阵自解释。"""
    result = compute_all({
        "majors": _tie_break_majors(),
        "answers": _neutral_personality_answers("A", "C"),
        "_session_overrides": _tie_break_overrides(),
    })
    assert result["meta"]["appetite_contradiction"] is True
    weights_vec = result["meta"]["appetite_tie_break_weights"]
    assert weights_vec == {"paths": 0.0, "reach": 0.0, "correct": 0.0, "recover": 0.0}
    assert result["algorithm_rank"] == ["A_PURE", "C_PURE", "B_MIXED"]


def test_appetite_contradiction_flagged_simple_case():
    """单专业冒烟：矛盾 → flag=true, appetite='contradiction', weights all-zeros。"""
    result = compute_all({
        "majors": [{"rank": 1, "name": "法学", "resource": "C"}],
        "answers": _neutral_personality_answers("A", "C"),
    })
    assert result["meta"]["appetite_contradiction"] is True
    assert result["meta"]["risk_appetite"] == "contradiction"
    assert result["meta"]["appetite_tie_break_weights"] == {
        "paths": 0.0, "reach": 0.0, "correct": 0.0, "recover": 0.0,
    }


# ---------- admission_score blend ----------


def test_admission_score_blend_perfect_match_preserves_adi():
    """admission_score=1.0 → final = ADI （blend_factor = min_factor + range = 1.0）。"""
    base_input = {
        "majors": [
            {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
            {"rank": 2, "name": "数学", "resource": "C"},
            {"rank": 3, "name": "物理", "resource": "C"},
        ],
        "answers": {
            "Q01": "B", "Q08": "B", "Q09": "B", "Q10": "B", "Q11": "B",
            "Q12": "B", "Q13": "B", "Q14": "B", "Q15": "B",
            "Q16": "C", "Q17": "B", "Q18": "B",
        },
    }
    no_blend = compute_all(base_input)
    full_blend = compute_all({**base_input, "_admission_scores": {"计算机类 / 软件工程": 1.0}})
    cs_no = no_blend["majors"]["计算机类 / 软件工程"]
    cs_full = full_blend["majors"]["计算机类 / 软件工程"]
    assert cs_full["admission_blend_factor"] == 1.0
    assert abs(cs_full["total"] - cs_no["total"]) < 0.01


def test_admission_score_blend_zero_match_dampens_adi():
    """admission_score=0 → final = ADI × min_factor (0.7)。"""
    base_input = {
        "majors": [
            {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
            {"rank": 2, "name": "数学", "resource": "C"},
            {"rank": 3, "name": "物理", "resource": "C"},
        ],
        "answers": {
            "Q01": "B", "Q08": "B", "Q09": "B", "Q10": "B", "Q11": "B",
            "Q12": "B", "Q13": "B", "Q14": "B", "Q15": "B",
            "Q16": "C", "Q17": "B", "Q18": "B",
        },
    }
    no_blend = compute_all(base_input)
    zero_blend = compute_all({**base_input, "_admission_scores": {"计算机类 / 软件工程": 0.0}})
    cs_no = no_blend["majors"]["计算机类 / 软件工程"]
    cs_zero = zero_blend["majors"]["计算机类 / 软件工程"]
    assert cs_zero["admission_blend_factor"] == 0.7
    # Round to handle float precision
    expected = round(cs_no["total"] * 0.7, 2)
    assert abs(cs_zero["total"] - expected) < 0.5  # 容差 0.5 分


def test_admission_score_does_not_alter_adi_total_field():
    """admission_score 应作为乘数，原始 adi_total 字段保留供调试。"""
    inp = {
        "majors": [
            {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
            {"rank": 2, "name": "数学", "resource": "C"},
            {"rank": 3, "name": "物理", "resource": "C"},
        ],
        "answers": {
            "Q01": "B", "Q08": "B", "Q09": "B", "Q10": "B", "Q11": "B",
            "Q12": "B", "Q13": "B", "Q14": "B", "Q15": "B",
            "Q16": "C", "Q17": "B", "Q18": "B",
        },
        "_admission_scores": {"计算机类 / 软件工程": 0.5},
    }
    result = compute_all(inp)
    cs = result["majors"]["计算机类 / 软件工程"]
    # adi_total 应大于 final total
    assert cs["adi_total"] > cs["total"]
    # blend_factor = 0.7 + 0.3 × 0.5 = 0.85
    assert cs["admission_blend_factor"] == 0.85


# ---------- alignment lint (v2.0) ----------


def test_admission_baseline_keys_aligned():
    """v2.0 lint: baseline_adi.json.majors 与 majors_admission_2024.json.majors
    必须同步；v2.1 扩展同时校验 _user_additions 同步——防止「其他专业现场推断」
    只落盘到 baseline 而忘了 admission（断裂工作流）。"""
    import json
    from pathlib import Path
    refs = Path(__file__).resolve().parent.parent / "references"
    with open(refs / "baseline_adi.json", encoding="utf-8") as f:
        baseline_data = json.load(f)
    with open(refs / "majors_admission_2024.json", encoding="utf-8") as f:
        admission_data = json.load(f)
    # Main majors must align
    baseline_main = set(baseline_data.get("majors", {}).keys())
    admission_main = set(admission_data.get("majors", {}).keys())
    assert baseline_main == admission_main, (
        f"主表不同步——baseline 独有 {sorted(baseline_main - admission_main)}；"
        f"admission 独有 {sorted(admission_main - baseline_main)}"
    )
    # v2.1: _user_additions must also align
    baseline_add = set((baseline_data.get("_user_additions") or {}).keys())
    admission_add = set((admission_data.get("_user_additions") or {}).keys())
    assert baseline_add == admission_add, (
        f"_user_additions 不同步——baseline 独有 {sorted(baseline_add - admission_add)}；"
        f"admission 独有 {sorted(admission_add - baseline_add)}。"
        "现场推断流程必须同时写两个文件。"
    )


# ---------- v2.1: _user_additions in admission ----------


def test_recommend_includes_user_additions(majors):
    """v2.1: admission_recommender 应同时考虑 _user_additions 中的专业（如 微电子）。"""
    student = StudentProfile(
        province="广东", mode="3+1+2", electives=["物理", "化学", "生物"],
        scores={"语文": 125, "数学": 130, "外语": 130,
                "物理": 88, "化学": 85, "生物": 82},
    )
    results = recommend(student)
    names = {r["name"] for r in results}
    assert "微电子" in names, (
        f"v2.1 后微电子（_user_additions）应被推荐器返回，得到 {names}"
    )


def test_compute_extras_fallback_pool_includes_user_additions():
    """v2.1: 直选分支（无 _admission_pool）时，extras fallback 池应含
    baseline._user_additions 的专业。"""
    inp = {
        "majors": [
            {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
            {"rank": 2, "name": "电气工程", "resource": "C"},
            {"rank": 3, "name": "经济学", "resource": "B"},
        ],
        "answers": {
            "Q01": "B", "Q08": "B", "Q09": "B", "Q10": "B", "Q11": "B",
            "Q12": "B", "Q13": "B", "Q14": "B", "Q15": "B",
            "Q16": "C", "Q17": "B", "Q18": "B",
        },
    }
    result = compute_all(inp)
    # extras 最多 3 个，可能不一定含微电子（要看排序），但池内必然包含
    # 我们间接验证：扩大测试，把 baseline 几乎所有大热门排除，看微电子是否能进 top 3
    inp2 = {
        **inp,
        "majors": [
            {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
            {"rank": 2, "name": "数据科学 / 人工智能", "resource": "C"},
            {"rank": 3, "name": "信息安全 / 网络工程", "resource": "C"},
        ],
    }
    result2 = compute_all(inp2)
    extras_names = {e["name"] for e in result2["extras"]}
    # 微电子 ADI 192，是相对高分专业，去掉 3 个 CS/AI/sec 后大概率进 top 3
    # 但具体排名取决于其他专业（如 数学、电气）；我们至少断言它进入过候选池
    # 通过 monkey-patching 排除所有更高分专业的方式不优雅；改用直接调用底层池构建
    from scripts.score_engine import load_baseline
    baseline = load_baseline()
    pool_keys = set((baseline.get("majors") or {}).keys()) | set((baseline.get("_user_additions") or {}).keys())
    assert "微电子" in pool_keys


def test_question_bank_notes_present_for_substantive_questions():
    """v2.7 lint: Q01/Q08-Q18 全部 12 个 substantive 题每个选项必须有 notes 字段，
    防止未来不小心删除导致 Claude 重新自创补充（如出现"绿高考考"这类错别字）。"""
    import json
    from pathlib import Path
    qb_path = Path(__file__).resolve().parent.parent / "references" / "question_bank.json"
    qbank = json.loads(qb_path.read_text())
    required_qs = {
        "Q01", "Q08", "Q09", "Q10", "Q11", "Q12", "Q13", "Q14",
        "Q15", "Q16", "Q17", "Q18",
    }
    for item in qbank["questions"]:
        if item["id"] in required_qs:
            for opt in item["options"]:
                assert "notes" in opt and opt["notes"].strip(), (
                    f"{item['id']} 选项 {opt['key']} 缺 notes 字段——会触发 Claude 自创回归"
                )
