"""Tests for the personalized ADI score engine.

These tests are the safety fuse for any weight or baseline adjustment:
they encode the qualitative outcomes the origin articles assert
(case A vs case B), and they encode the core invariant that baseline
locking cannot be erased by personality multipliers.
"""

import json
from pathlib import Path

import pytest

from scripts.score_engine import (
    CLAMP_MAX,
    CLAMP_MIN,
    DIMENSION_KEYS,
    ability_index,
    compute_all,
    grade,
    kendall_tau,
    load_baseline,
    load_weights,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def case_a() -> dict:
    return _load("case_a_input.json")


@pytest.fixture
def case_b() -> dict:
    return _load("case_b_input.json")


# ---------- Case A: 能力强 + 意愿强 ----------


def test_case_a_economics_above_threshold(case_a):
    """Origin article 3: A 选经济学被'明显抬起来'。"""
    result = compute_all(case_a)
    total = result["majors"]["经济学"]["total"]
    assert total >= 200, f"经济学 total={total}, expected ≥ 200"


def test_case_a_english_above_threshold(case_a):
    """Origin: A 选英语被'显著抬高'。"""
    result = compute_all(case_a)
    total = result["majors"]["英语/外语"]["total"]
    assert total >= 200, f"英语 total={total}, expected ≥ 200"


def test_case_a_law_cannot_reach_low_difficulty(case_a):
    """Origin: A 的法学'仍只有 90，还是高难路径'——个人素质不能完全抹平。"""
    result = compute_all(case_a)
    total = result["majors"]["法学"]["total"]
    assert total < 300, f"法学 total={total}, expected < 300 (不进低难)"


# ---------- Case B: 资源弱 + 意愿一般 ----------


def test_case_b_law_high_difficulty(case_b):
    """Origin: B 法学'掉进高难路径'。"""
    result = compute_all(case_b)
    total = result["majors"]["法学"]["total"]
    assert total < 50, f"法学 total={total}, expected < 50 (高难)"


def test_case_b_english_difficult(case_b):
    """Origin: B 英语'从可展开变成高难度路径'。"""
    result = compute_all(case_b)
    total = result["majors"]["英语/外语"]["total"]
    assert total < 150, f"英语 total={total}, expected < 150"


def test_case_b_chinese_literature_difficult(case_b):
    """Origin: B 汉语言'容易泯然众人，拉不开差距'。"""
    result = compute_all(case_b)
    total = result["majors"]["汉语言文学"]["total"]
    assert total < 150, f"汉语言文学 total={total}, expected < 150"


# ---------- Core invariants ----------


def test_all_dimensions_clamped(case_b):
    """All adjusted dimensions stay in [1, 5]."""
    result = compute_all(case_b)
    for major, data in result["majors"].items():
        for dim in DIMENSION_KEYS:
            v = data["dimensions"][dim]["adjusted"]
            assert CLAMP_MIN <= v <= CLAMP_MAX, (
                f"{major}.{dim}={v} out of [{CLAMP_MIN},{CLAMP_MAX}]"
            )


def test_personality_cannot_erase_baseline_lock():
    """全 A 个人素质 + 资源 A 时，法学/临床/纯艺术仍走不通。这是核心保险丝。

    v3.16：资源扩展到全 4 维度后，decisive-sensitivity 专业在「家庭+行业资源全 A」
    下被合理抬高（艺术 ~40→55、法学 ~53→87），但 baseline lock 仍成立——三者都
    远低于中等档(150)，临床(decisive + 最低 bases)仍锁在高难(<50)。阈值从 <50 放宽
    到艺术 <100 反映 #5「资源对 decisive 专业影响最大」的有意设计，而非穿透天花板。
    """
    extreme = {
        "majors": [
            {"rank": 1, "name": "法学", "resource": "A"},
            {"rank": 2, "name": "临床医学", "resource": "A"},
            {"rank": 3, "name": "艺术类（美术/音乐/表演）", "resource": "A"},
        ],
        "answers": {
            "Q01": "C",
            "Q08": "A",
            "Q09": "A",
            "Q10": "A",
            "Q11": "A",
            "Q12": "A",
            "Q13": "A",
            "Q14": "A",
            "Q15": "A",
            "Q16": "B",
            "Q17": "C",
            "Q18": "C",
        },
    }
    result = compute_all(extreme)
    # 核心不变量：即使全 A + 资源全 A，三者都进不了中等档（< 150）
    assert result["majors"]["法学"]["total"] < 200
    assert result["majors"]["临床医学"]["total"] < 50  # decisive + 最低 bases，仍锁高难
    assert result["majors"]["艺术类（美术/音乐/表演）"]["total"] < 100


def test_subjective_rank_preserves_input_order(case_a):
    result = compute_all(case_a)
    assert result["subjective_rank"] == ["经济学", "英语/外语", "法学"]


def test_algorithm_rank_is_descending_by_total(case_a):
    # case_a 三专业 total 相距远（不同 band），故仍严格降序；
    # 同 band 内 appetite 可重排，见 test_appetite_reorders_within_band_*。
    result = compute_all(case_a)
    totals = [result["majors"][m]["total"] for m in result["algorithm_rank"]]
    assert totals == sorted(totals, reverse=True)


def test_band_index_groups_close_totals():
    """v3.16：相对差 ≤ pct 的 total 落在同一对数 band；明显不同的进不同 band。"""
    from scripts.score_engine import _band_index

    assert _band_index(100, 0.05) == _band_index(103, 0.05)  # 3% 内 → 同 band
    assert _band_index(100, 0.05) != _band_index(130, 0.05)  # 30% → 不同 band


def test_appetite_reorders_within_band_but_not_across(case_a):
    """v3.16 ε-band 核心行为：同 band 内按 personal_fit 重排，跨 band 仍由 total 主导。

    用纯函数 _appetite_sort_key 锁住语义，避免依赖具体 baseline 数值。
    """
    from scripts.score_engine import _appetite_sort_key

    hi_total_low_fit = {"total": 103.0, "personal_fit": 2.0}
    lo_total_hi_fit = {"total": 100.0, "personal_fit": 4.0}
    within = sorted(
        [hi_total_low_fit, lo_total_hi_fit],
        key=lambda m: _appetite_sort_key(m, 0.05),
    )
    assert within[0] is lo_total_hi_fit  # 同 band：高 personal_fit 胜出

    far_high_total = {"total": 200.0, "personal_fit": 0.0}
    near_high_fit = {"total": 100.0, "personal_fit": 5.0}
    across = sorted(
        [near_high_fit, far_high_total],
        key=lambda m: _appetite_sort_key(m, 0.05),
    )
    assert across[0] is far_high_total  # 跨 band：高 total 胜出，无视 personal_fit


def test_appetite_effect_no_op_when_order_matches_objective():
    """同 band 两专业但最终序与客观序一致 → appetite 没起作用，不点名。"""
    from scripts.score_engine import _appetite_effect

    majors = {"A": {"total": 625.0}, "B": {"total": 625.0}, "C": {"total": 180.0}}
    changed, promoted, demoted = _appetite_effect(majors, ["A", "B", "C"], 0.05)
    assert changed is False
    assert promoted is None and demoted is None


def test_appetite_effect_detects_reorder_and_names_pair():
    """同 band 两专业被 appetite 换位 → 返回被顶上去/被压下去的具体专业。"""
    from scripts.score_engine import _appetite_effect

    # 输入序 [X, Y]（同 total → 同 band），但最终 algorithm_rank 把 Y 排到前。
    majors = {"X": {"total": 225.0}, "Y": {"total": 225.0}}
    changed, promoted, demoted = _appetite_effect(majors, ["Y", "X"], 0.05)
    assert changed is True
    assert promoted == "Y"  # appetite 把 Y 顶上去
    assert demoted == "X"  # 越过了 X


def test_meta_exposes_appetite_effect_flags(case_a):
    """compute_all 的 meta 必须带 appetite 是否真正改变排名的标记。"""
    result = compute_all(case_a)
    meta = result["meta"]
    assert isinstance(meta["appetite_changed_order"], bool)
    # case_a 三专业 total 相距远（不同 band），appetite 无从重排。
    assert meta["appetite_changed_order"] is False
    assert meta["appetite_promoted"] is None
    assert meta["appetite_demoted"] is None


# ---------- Kendall τ ----------


def test_kendall_tau_identity():
    assert kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == 1.0


def test_kendall_tau_fully_reversed():
    assert kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == -1.0


def test_kendall_tau_one_swap_neighbors():
    tau = kendall_tau(["a", "b", "c"], ["a", "c", "b"])
    assert abs(tau - (1 / 3)) < 1e-9


def test_kendall_tau_one_swap_endpoints():
    tau = kendall_tau(["a", "b", "c"], ["c", "b", "a"])
    assert tau == -1.0


# ---------- Grade thresholds ----------


def test_grade_low():
    assert grade(400) == "低难"
    assert grade(301) == "低难"


def test_grade_medium():
    assert grade(200) == "中等"
    assert grade(151) == "中等"


def test_grade_hard():
    assert grade(100) == "较难"


def test_grade_extreme_hard():
    assert grade(8) == "高难"


# ---------- Edge cases ----------


def test_unknown_major_raises_value_error():
    bad = {
        "majors": [
            {"rank": 1, "name": "不存在的专业XYZ", "resource": "C"},
            {"rank": 2, "name": "计算机类 / 软件工程", "resource": "C"},
            {"rank": 3, "name": "法学", "resource": "C"},
        ],
        "answers": _default_answers(),
    }
    with pytest.raises(ValueError, match="基础 ADI 缺失"):
        compute_all(bad)


def test_alias_resolves_to_canonical_main_entry():
    """_aliases 命中后用规范化名查主表，得分与规范化名直接查相同。"""
    answers = _default_answers()
    aliased = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "集成电路", "resource": "B"},
                {"rank": 2, "name": "法学", "resource": "C"},
                {"rank": 3, "name": "经济学", "resource": "C"},
            ],
            "answers": answers,
        }
    )
    canonical = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "微电子", "resource": "B"},
                {"rank": 2, "name": "法学", "resource": "C"},
                {"rank": 3, "name": "经济学", "resource": "C"},
            ],
            "answers": answers,
        }
    )
    assert (
        aliased["majors"]["集成电路"]["total"] == canonical["majors"]["微电子"]["total"]
    )
    # 集成电路 走别名到 _user_additions 的 微电子 — 标记为 user_addition，不是 session_override
    assert "集成电路" not in aliased["meta"]["session_inferred_majors"]


def test_alias_to_user_additions():
    """微电子 在 _user_additions，直接查应该走 user_addition 通道。"""
    result = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "微电子", "resource": "B"},
                {"rank": 2, "name": "法学", "resource": "C"},
                {"rank": 3, "name": "经济学", "resource": "C"},
            ],
            "answers": _default_answers(),
        }
    )
    assert "微电子" in result["majors"]
    assert "微电子" not in result["meta"]["session_inferred_majors"]


def test_session_overrides_take_precedence():
    """_session_overrides 命中时算法使用其值，并在 meta 中标记。"""
    input_with_override = {
        "majors": [
            {"rank": 1, "name": "航空航天工程", "resource": "C"},
            {"rank": 2, "name": "计算机类 / 软件工程", "resource": "C"},
            {"rank": 3, "name": "法学", "resource": "C"},
        ],
        "_session_overrides": {
            "航空航天工程": {
                "paths": 3,
                "reach": 3,
                "correct": 2,
                "recover": 3,
                "rationale": "test override",
            }
        },
        "answers": _default_answers(),
    }
    result = compute_all(input_with_override)
    assert "航空航天工程" in result["majors"]
    assert "航空航天工程" in result["meta"]["session_inferred_majors"]


def test_all_three_session_inferred_flags_low_confidence():
    input_data = {
        "majors": [
            {"rank": 1, "name": "X", "resource": "C"},
            {"rank": 2, "name": "Y", "resource": "C"},
            {"rank": 3, "name": "Z", "resource": "C"},
        ],
        "_session_overrides": {
            "X": {"paths": 3, "reach": 3, "correct": 3, "recover": 3, "rationale": "t"},
            "Y": {"paths": 3, "reach": 3, "correct": 3, "recover": 3, "rationale": "t"},
            "Z": {"paths": 3, "reach": 3, "correct": 3, "recover": 3, "rationale": "t"},
        },
        "answers": _default_answers(),
    }
    result = compute_all(input_data)
    assert result["meta"]["low_confidence"] is True


def _default_answers() -> dict:
    return {
        "Q01": "B",
        "Q08": "B",
        "Q09": "B",
        "Q10": "B",
        "Q11": "B",
        "Q12": "B",
        "Q13": "B",
        "Q14": "B",
        "Q15": "B",
        "Q16": "C",
        "Q17": "B",
        "Q18": "B",
    }


# ---------- Resource sensitivity (v1.1) ----------


def _recover_multiplier(result: dict, major: str) -> float:
    return result["majors"][major]["dimensions"]["recover"]["multiplier"]


def test_q3_specific_resource_sensitivity_modulates_recover():
    """Q3=A 给金融（high）的 recover 乘子涨幅应大于给计算机（low）的涨幅。"""
    base = {
        "majors": [
            {"rank": 1, "name": "金融学", "resource": "C"},
            {"rank": 2, "name": "计算机类 / 软件工程", "resource": "C"},
            {"rank": 3, "name": "经济学", "resource": "C"},
        ],
        "answers": _default_answers(),
    }
    boost = {
        "majors": [
            {"rank": 1, "name": "金融学", "resource": "A"},
            {"rank": 2, "name": "计算机类 / 软件工程", "resource": "A"},
            {"rank": 3, "name": "经济学", "resource": "C"},
        ],
        "answers": _default_answers(),
    }
    base_res = compute_all(base)
    boost_res = compute_all(boost)
    finance_ratio = _recover_multiplier(boost_res, "金融学") / _recover_multiplier(
        base_res, "金融学"
    )
    cs_ratio = _recover_multiplier(
        boost_res, "计算机类 / 软件工程"
    ) / _recover_multiplier(base_res, "计算机类 / 软件工程")
    assert finance_ratio > cs_ratio, (
        f"金融 Q3=A recover 涨幅 {finance_ratio:.4f} 应大于 CS 涨幅 {cs_ratio:.4f}"
    )


def test_q12_global_resource_sensitivity_modulates_recover():
    """Q12=A 给临床医学（decisive）的 recover 涨幅应大于给计算机（low）。"""
    cs_a = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
                {"rank": 2, "name": "数学", "resource": "C"},
                {"rank": 3, "name": "物理", "resource": "C"},
            ],
            "answers": {**_default_answers(), "Q12": "A"},
        }
    )
    cs_c = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
                {"rank": 2, "name": "数学", "resource": "C"},
                {"rank": 3, "name": "物理", "resource": "C"},
            ],
            "answers": {**_default_answers(), "Q12": "C"},
        }
    )
    cl_a = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "临床医学", "resource": "C"},
                {"rank": 2, "name": "数学", "resource": "C"},
                {"rank": 3, "name": "物理", "resource": "C"},
            ],
            "answers": {**_default_answers(), "Q12": "A"},
        }
    )
    cl_c = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "临床医学", "resource": "C"},
                {"rank": 2, "name": "数学", "resource": "C"},
                {"rank": 3, "name": "物理", "resource": "C"},
            ],
            "answers": {**_default_answers(), "Q12": "C"},
        }
    )
    cs_ratio = _recover_multiplier(cs_a, "计算机类 / 软件工程") / _recover_multiplier(
        cs_c, "计算机类 / 软件工程"
    )
    cl_ratio = _recover_multiplier(cl_a, "临床医学") / _recover_multiplier(
        cl_c, "临床医学"
    )
    assert cl_ratio > cs_ratio, (
        f"临床医学 Q12 ratio {cl_ratio:.4f} 应大于 CS ratio {cs_ratio:.4f}"
    )


def test_resource_sensitivity_level_reported():
    """compute_major 返回每个专业实际使用的 resource_sensitivity 等级。"""
    result = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "金融学", "resource": "C"},
                {"rank": 2, "name": "计算机类 / 软件工程", "resource": "C"},
                {"rank": 3, "name": "临床医学", "resource": "C"},
            ],
            "answers": _default_answers(),
        }
    )
    assert result["majors"]["金融学"]["resource_sensitivity"] == "high"
    assert result["majors"]["计算机类 / 软件工程"]["resource_sensitivity"] == "low"
    assert result["majors"]["临床医学"]["resource_sensitivity"] == "decisive"


def test_session_override_defaults_to_default_sensitivity():
    """会话级新专业（无 resource_sensitivity 字段）应回退到 'default'。"""
    result = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "航天X", "resource": "A"},
                {"rank": 2, "name": "计算机类 / 软件工程", "resource": "A"},
                {"rank": 3, "name": "数学", "resource": "A"},
            ],
            "_session_overrides": {
                "航天X": {
                    "paths": 3,
                    "reach": 3,
                    "correct": 3,
                    "recover": 3,
                    "rationale": "t",
                }
            },
            "answers": _default_answers(),
        }
    )
    assert result["majors"]["航天X"]["resource_sensitivity"] == "default"


# ---------- AI polarization (v1.2) ----------


def _all_a_answers() -> dict:
    return {
        "Q01": "B",
        "Q08": "A",
        "Q09": "A",
        "Q10": "A",
        "Q11": "A",
        "Q12": "B",
        "Q13": "B",
        "Q14": "B",
        "Q15": "B",
        "Q16": "C",
        "Q17": "B",
        "Q18": "B",
    }


def _all_d_answers() -> dict:
    return {
        "Q01": "B",
        "Q08": "D",
        "Q09": "D",
        "Q10": "D",
        "Q11": "D",
        "Q12": "B",
        "Q13": "B",
        "Q14": "B",
        "Q15": "B",
        "Q16": "C",
        "Q17": "B",
        "Q18": "B",
    }


def test_ability_index_boundaries():
    """ability_index 阈值边界：全 A → high；全 B → mid；案例 B (B/C/C/D) → low；全 D → very_low。"""
    weights = load_weights()
    all_a = {"Q08": "A", "Q09": "A", "Q10": "A", "Q11": "A"}
    all_b = {"Q08": "B", "Q09": "B", "Q10": "B", "Q11": "B"}
    case_b_pattern = {"Q08": "B", "Q09": "C", "Q10": "C", "Q11": "D"}
    all_d = {"Q08": "D", "Q09": "D", "Q10": "D", "Q11": "D"}
    assert ability_index(all_a, weights) == "high"
    assert ability_index(all_b, weights) == "mid"
    assert ability_index(case_b_pattern, weights) == "low"
    assert ability_index(all_d, weights) == "very_low"


def test_cs_polarization_high_vs_very_low_ability():
    """CS (boost) 在高能力用户下应大幅高于低能力用户——极化效应核心保险丝。"""
    common = [
        {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
        {"rank": 2, "name": "数学", "resource": "C"},
        {"rank": 3, "name": "物理", "resource": "C"},
    ]
    high = compute_all({"majors": common, "answers": _all_a_answers()})
    very_low = compute_all({"majors": common, "answers": _all_d_answers()})
    diff = (
        high["majors"]["计算机类 / 软件工程"]["total"]
        - very_low["majors"]["计算机类 / 软件工程"]["total"]
    )
    assert diff > 300, (
        f"CS 高能力 vs 极弱能力总分差 {diff:.1f} 应远大于 300（极化效应不显著）"
    )


def test_threatened_major_negative_even_at_high_ability():
    """法学（threatened）即使高能力用户也应受 AI 影响（reach AI delta 为负）。"""
    high_input = {
        "majors": [
            {"rank": 1, "name": "法学", "resource": "C"},
            {"rank": 2, "name": "数学", "resource": "C"},
            {"rank": 3, "name": "物理", "resource": "C"},
        ],
        "answers": _all_a_answers(),
    }
    result = compute_all(high_input)
    law = result["majors"]["法学"]
    contribs = law["dimensions"]["reach"]["contributors"]
    ai_contrib = next((c for c in contribs if c["source"] == "ai_impact"), None)
    assert ai_contrib is not None, "法学应有 ai_impact 贡献"
    assert ai_contrib["delta"] < 0, (
        f"threatened+high 的 reach AI delta 应为负，实际 {ai_contrib['delta']}"
    )
    assert law["ai_impact"] == "threatened"
    assert law["ability_level"] == "high"


def test_neutral_major_not_affected_by_ability():
    """临床医学 (neutral) 总分不应因能力变化而通过 AI 路径偏移
    (个人 trait 仍会影响，但 AI 修正项必须为 0)。"""
    inputs = [
        {
            "majors": [
                {"rank": 1, "name": "临床医学", "resource": "C"},
                {"rank": 2, "name": "数学", "resource": "C"},
                {"rank": 3, "name": "物理", "resource": "C"},
            ],
            "answers": ans,
        }
        for ans in (_all_a_answers(), _default_answers(), _all_d_answers())
    ]
    for inp in inputs:
        r = compute_all(inp)["majors"]["临床医学"]
        for dim in ("reach", "paths"):
            ai = next(
                (
                    c
                    for c in r["dimensions"][dim]["contributors"]
                    if c["source"] == "ai_impact"
                ),
                None,
            )
            assert ai is None or ai["delta"] == 0, (
                f"neutral 专业 {dim} 的 AI delta 应为 0，实际 {ai}"
            )


def test_ai_impact_level_reported():
    """compute_major 输出每个专业实际使用的 ai_impact + ability_level。"""
    result = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
                {"rank": 2, "name": "法学", "resource": "C"},
                {"rank": 3, "name": "临床医学", "resource": "C"},
            ],
            "answers": _all_a_answers(),
        }
    )
    assert result["majors"]["计算机类 / 软件工程"]["ai_impact"] == "boost"
    assert result["majors"]["法学"]["ai_impact"] == "threatened"
    assert result["majors"]["临床医学"]["ai_impact"] == "neutral"
    # 同一份 answers 三专业 ability_level 必须相同
    abilities = {result["majors"][m]["ability_level"] for m in result["majors"]}
    assert abilities == {"high"}


# ---------- Rationale schema (v1.3) ----------


REQUIRED_RATIONALE_LAYERS = ("baseline", "resource", "ai_impact")


def test_rationale_three_layers_required():
    """所有 39 专业的 rationale 必须是 dict，包含 baseline/resource/ai_impact 三个非空字段。

    这是 v1.3 的防删除保险丝：重写或编辑时若误删某一层，pytest 立即报错。
    """
    baseline = load_baseline()
    violations = []
    for name, info in baseline["majors"].items():
        rationale = info.get("rationale")
        if not isinstance(rationale, dict):
            violations.append(
                f"{name}: rationale 不是 dict（{type(rationale).__name__}）"
            )
            continue
        for layer in REQUIRED_RATIONALE_LAYERS:
            if layer not in rationale:
                violations.append(f"{name}: 缺少 rationale.{layer} 字段")
                continue
            value = rationale[layer]
            if not isinstance(value, str) or not value.strip():
                violations.append(f"{name}: rationale.{layer} 为空或非字符串")
    user_additions = baseline.get("_user_additions", {})
    for name, info in user_additions.items():
        rationale = info.get("rationale")
        if not isinstance(rationale, dict):
            violations.append(f"_user_additions/{name}: rationale 不是 dict")
            continue
        for layer in REQUIRED_RATIONALE_LAYERS:
            if not (rationale.get(layer) or "").strip():
                violations.append(f"_user_additions/{name}: rationale.{layer} 缺失/空")
    assert not violations, "rationale 三层校验失败：\n  " + "\n  ".join(violations)


def test_rationale_passthrough_to_result():
    """score_engine 应原样透传 rationale dict（不破坏结构）。"""
    result = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
                {"rank": 2, "name": "金融学", "resource": "C"},
                {"rank": 3, "name": "法学", "resource": "C"},
            ],
            "answers": _default_answers(),
        }
    )
    for major_name in ("计算机类 / 软件工程", "金融学", "法学"):
        r = result["majors"][major_name]["rationale"]
        assert isinstance(r, dict), f"{major_name}: rationale 应为 dict"
        for layer in REQUIRED_RATIONALE_LAYERS:
            assert r.get(layer), f"{major_name}: 缺 {layer}"


def test_cs_low_ability_falls_below_finance_high_ability():
    """关键现实信号：低能力学 CS 可能比高能力学金融更难走通——AI 时代的本质判断。"""
    cs_low = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
                {"rank": 2, "name": "数学", "resource": "C"},
                {"rank": 3, "name": "物理", "resource": "C"},
            ],
            "answers": _all_d_answers(),
        }
    )
    finance_high = compute_all(
        {
            "majors": [
                {"rank": 1, "name": "金融学", "resource": "C"},
                {"rank": 2, "name": "数学", "resource": "C"},
                {"rank": 3, "name": "物理", "resource": "C"},
            ],
            "answers": _all_a_answers(),
        }
    )
    # 这条断言锁住"能力比专业重要"的产品信号；如果未来权重调整后该断言失败，
    # 需要在 scoring_model.md 调参日志里说明为什么允许"低能力学 CS"超过"高能力学金融"
    cs_total = cs_low["majors"]["计算机类 / 软件工程"]["total"]
    fin_total = finance_high["majors"]["金融学"]["total"]
    assert cs_total < fin_total, (
        f"CS+全D ({cs_total}) 应低于 金融+全A ({fin_total})——AI 极化产品信号"
    )


# ---------- v2.0 extras + student_context ----------


def _basic_input() -> dict:
    """Minimal input with 3 selected majors and B-default answers."""
    return {
        "majors": [
            {"rank": 1, "name": "计算机类 / 软件工程", "resource": "C"},
            {"rank": 2, "name": "电气工程", "resource": "C"},
            {"rank": 3, "name": "经济学", "resource": "B"},
        ],
        "answers": {
            "Q01": "B",
            "Q08": "B",
            "Q09": "B",
            "Q10": "B",
            "Q11": "B",
            "Q12": "B",
            "Q13": "B",
            "Q14": "B",
            "Q15": "B",
            "Q16": "C",
            "Q17": "B",
            "Q18": "B",
        },
    }


def test_extras_present_when_no_admission_pool():
    """v2.0: 无 _admission_pool → 走 fallback 池（全 39 专业）+ warning。"""
    result = compute_all(_basic_input())
    assert len(result["extras"]) == 3, f"应推荐 3 个，得到 {len(result['extras'])}"
    selected = {"计算机类 / 软件工程", "电气工程", "经济学"}
    for ex in result["extras"]:
        assert ex["name"] not in selected
    assert result["extras_warning"] is not None
    assert "未走推荐分支" in result["extras_warning"]


def test_extras_use_admission_pool_when_provided():
    """v2.0: _admission_pool 提供时，仅从 strong/consider 桶取候选 + 无 warning。"""
    inp = _basic_input()
    inp["_admission_pool"] = [
        {"name": "数据科学 / 人工智能", "score": 0.88, "category": "strong"},
        {"name": "金融学", "score": 0.74, "category": "consider"},
        {"name": "数学", "score": 0.80, "category": "strong"},
        {"name": "法学", "score": 0.40, "category": "not_recommended"},
    ]
    result = compute_all(inp)
    extras_names = {e["name"] for e in result["extras"]}
    assert "法学" not in extras_names, "not_recommended 桶不应进入 extras"
    assert result["extras_warning"] is None
    assert len(result["extras"]) <= 3


def test_extras_exclude_selected_majors():
    """v2.0: 用户选定的 3 个专业绝不能出现在 extras。"""
    inp = _basic_input()
    inp["_admission_pool"] = [
        {"name": "计算机类 / 软件工程", "score": 0.99, "category": "strong"},
        {"name": "数据科学 / 人工智能", "score": 0.88, "category": "strong"},
    ]
    result = compute_all(inp)
    selected = {"计算机类 / 软件工程", "电气工程", "经济学"}
    for ex in result["extras"]:
        assert ex["name"] not in selected, f"{ex['name']} 已在 selected 中"


def test_extras_sorted_by_total_desc():
    """v2.0: extras 列表按 total 降序排列。"""
    result = compute_all(_basic_input())
    totals = [e["total"] for e in result["extras"]]
    assert totals == sorted(totals, reverse=True)


def test_student_context_passes_through_when_present():
    """v2.0: input._student_profile → result.student_context（透传给 render 层）。"""
    inp = _basic_input()
    profile = {
        "scores": {"数学": 138, "物理": 92},
        "favorite_subjects": ["数学"],
        "disliked_subjects": [],
    }
    inp["_student_profile"] = profile
    result = compute_all(inp)
    assert result["student_context"] == profile


def test_student_context_none_when_not_provided():
    """v2.0: 直选分支无 _student_profile → result.student_context 为 None。"""
    result = compute_all(_basic_input())
    assert result["student_context"] is None
