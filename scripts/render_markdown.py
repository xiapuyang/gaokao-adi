"""Render a personalized ADI result dict into a Markdown report."""
import argparse
import json
from datetime import datetime
from pathlib import Path

_REFERENCES = Path(__file__).resolve().parent.parent / "references"
QBANK_PATH = _REFERENCES / "question_bank.json"

DIMENSION_LABELS = {
    "paths": "路径数量",
    "reach": "成功可达性",
    "correct": "纠偏能力",
    "recover": "损失可控性",
}
GRADE_BADGE = {"低难": "🟢", "中等": "🟡", "较难": "🟠", "高难": "🔴"}
APPETITE_LABELS = {
    "strong_averse": "强求稳",
    "averse": "偏求稳",
    "neutral": "中立权衡",
    "seeking": "偏进取",
    "strong_seeking": "强进取",
    "contradiction": "信号矛盾",
}
TAU_INTERPRETATION = {
    1.0: "完全一致 — 你的偏好与可走通性吻合",
    1 / 3: "大体一致，有 1 处错位",
    0.0: "偏好与可走通性无明显关联",
    -1 / 3: "明显反差，建议重新审视前 2 个专业",
    -1.0: "你最想的恰恰最难走通 — 强烈建议重排",
}


def _interpret_tau(tau: float) -> str:
    """Map tau to a short text interpretation."""
    closest = min(TAU_INTERPRETATION.keys(), key=lambda k: abs(k - tau))
    return TAU_INTERPRETATION[closest]


def _load_qbank() -> dict:
    """Load the question-bank JSON used to render option labels in the report."""
    with open(QBANK_PATH, encoding="utf-8") as f:
        return json.load(f)


def _md_cell(s: str) -> str:
    """Escape characters that would break a GFM table cell."""
    return str(s).replace("|", "\\|")


def _option_label(qbank: dict, qid: str, key: str) -> str:
    """Look up the human-readable label for a question option."""
    for q in qbank["questions"]:
        if q["id"] == qid:
            for opt in q.get("options", []):
                if opt["key"] == key:
                    return opt["label"]
    return key or "?"


def _ascii_bar(value: float, max_value: float = 5.0, width: int = 10) -> str:
    """Build a unicode block-character bar to visualize a 1-5 score."""
    n = max(0, min(width, int(round(value / max_value * width))))
    return "█" * n + "░" * (width - n)


_RATIONALE_LAYER_DISPLAY = (
    ("baseline", "📝 **基础判断**"),
    ("resource", "💼 **资源敏感度依据**"),
    ("ai_impact", "🤖 **AI 影响依据**"),
)


def _format_rationale_lines(rationale: object) -> list[str]:
    """Format rationale (dict with 3 layers, or legacy string) into Markdown bullets."""
    if isinstance(rationale, dict):
        out = []
        for key, label in _RATIONALE_LAYER_DISPLAY:
            text = (rationale.get(key) or "").strip()
            if text:
                out.append(f"- {label}：{text}")
        return out
    if isinstance(rationale, str) and rationale.strip():
        return [f"- 📝 基础判断：{rationale.strip()}"]
    return []


def _format_contributor(prefix: str, c: dict, pct_fmt: str) -> str:
    """Format a top_lift / top_drag contributor into a Markdown bullet.

    Handles both trait/resource contributors (with 'answer') and ai_impact
    contributors (with 'impact_level' + 'ability_level').
    """
    pct = c["delta"] * 100
    dim_label = DIMENSION_LABELS.get(c.get("dimension", ""), c.get("dimension", "?"))
    pct_str = pct_fmt.format(pct=pct)
    if c["source"] == "ai_impact":
        tag = f"AI 影响（{c.get('impact_level', '?')}×{c.get('ability_level', '?')}）"
    elif c["source"] == "resource":
        tag = f"专业资源={c.get('answer', '?')}"
    elif c["source"] == "Q12":
        tag = f"Q12 家庭支持={c.get('answer', '?')}"
    elif c["source"] == "Q15":
        tag = f"Q15 状态={c.get('answer', '?')}"
    else:
        tag = f"{c['source']}={c.get('answer', '?')}"
    return f"- {prefix}：{tag} 把「{dim_label}」{pct_str}"


def _section_overview(meta: dict, qbank: dict) -> list[str]:
    """Render the user profile overview section."""
    lines = ["## 概览", ""]
    lines.append(f"- **路径偏好**：{_option_label(qbank, 'Q01', meta['preference_path'])}")
    lines.append(f"- **风险态度**：{_option_label(qbank, 'Q18', meta['risk_tone'])}")
    appetite = meta.get("risk_appetite", "neutral")
    appetite_label = APPETITE_LABELS.get(appetite, "中立权衡")
    if appetite == "contradiction":
        lines.append(f"- **综合风险倾向**：⚠️ {appetite_label}（Q1 与 Q18 方向相反）")
    else:
        lines.append(f"- **综合风险倾向**：{appetite_label}")
    lines.append(f"- **学校 vs 专业**：{_option_label(qbank, 'Q16', meta['school_vs_major'])}")
    lines.append(f"- **城市重视**：{_option_label(qbank, 'Q17', meta['city_emphasis'])}")
    lines.append("")
    return lines


def _section_subjective_vs_algo(result: dict) -> list[str]:
    """Render subjective rank vs algorithm rank comparison."""
    lines = ["## 主观偏好 vs 客观可走通", ""]
    lines.append("| 你的偏好序 | 专业 | ↔ | 算法序 | 专业 |")
    lines.append("|---|---|---|---|---|")
    for i, (s, a) in enumerate(zip(result["subjective_rank"], result["algorithm_rank"]), 1):
        mark = "✅" if s == a else "↔"
        lines.append(f"| {i} | {_md_cell(s)} | {mark} | {i} | {_md_cell(a)} |")
    tau = result["kendall_tau"]
    lines.append("")
    lines.append(f"一致度 Kendall τ = **{tau:+.2f}** — {_interpret_tau(tau)}")
    lines.append("")
    return lines


def _section_score_table(result: dict) -> list[str]:
    """Render the top-level score table sorted by ADI desc."""
    lines = ["## 三专业评分", ""]
    lines.append("| 排名 | 专业 | 总分 | 分档 | 主要瓶颈 |")
    lines.append("|---|---|---|---|---|")
    for i, name in enumerate(result["algorithm_rank"], 1):
        d = result["majors"][name]
        bottleneck = DIMENSION_LABELS[d["bottleneck"]]
        lines.append(
            f"| {i} | {_md_cell(name)} | **{d['total']}** | "
            f"{GRADE_BADGE[d['grade']]} {d['grade']} | {bottleneck} |"
        )
    lines.append("")
    return lines


def _section_major_card(name: str, data: dict, rank: int) -> list[str]:
    """Render a per-major analysis card."""
    lines = [
        f"### {rank}. {name} — {data['total']} {GRADE_BADGE[data['grade']]} {data['grade']}",
        "",
        "**4 维度（调整后）：**",
        "",
        "| 维度 | 基础 | 个人系数 | 调整后 | 条形 |",
        "|---|---|---|---|---|",
    ]
    for dim in ("paths", "reach", "correct", "recover"):
        dd = data["dimensions"][dim]
        label = DIMENSION_LABELS[dim]
        bar = _ascii_bar(dd["adjusted"])
        lines.append(
            f"| {label} | {dd['base']} | ×{dd['multiplier']:.2f} | {dd['adjusted']:.2f} | `{bar}` |"
        )
    lines.append("")
    if data.get("top_lift"):
        lines.append(_format_contributor("🚀 **主要拉抬**", data["top_lift"], "+{pct:.0f}%"))
    if data.get("top_drag"):
        lines.append(_format_contributor("🪨 **主要拖累**", data["top_drag"], "{pct:.0f}%"))
    lines.append(
        f"- ⚠️ **瓶颈维度**：{DIMENSION_LABELS[data['bottleneck']]}（最低的维度锁住总分上限）"
    )
    lines.extend(_format_rationale_lines(data.get("rationale")))
    if data.get("source") == "session_override":
        lines.append("- ℹ️ 本专业基础分为本次现场推断，置信度低于词典专业。")
    lines.append("")
    return lines


_APPETITE_TIE_BREAK_NARRATIVE = {
    "strong_averse": "成功可达性 + 损失可控性",
    "averse":        "损失可控性为主、兼顾成功可达性",
    "neutral":       "路径数量 + 纠偏能力 + 损失可控性（三维均衡）",
    "seeking":       "路径数量 + 纠偏能力为主",
    "strong_seeking": "路径数量 + 纠偏能力",
}


def _appetite_advice(meta: dict, result: dict) -> list[str]:
    """Advice lines driven by risk_appetite — sort-tie hint + contradiction prompt."""
    out: list[str] = []
    appetite = meta.get("risk_appetite", "neutral")
    if meta.get("appetite_contradiction"):
        out.append(
            "- ⚠️ Q1 与 Q18 方向相反（一个求稳、一个进取），自相矛盾。"
            "建议先想清楚到底是要「稳」还是要「冲」，再回头看哪几条专业更贴近真实倾向。"
        )
        return out
    weights_vec = meta.get("appetite_tie_break_weights")
    if not weights_vec:
        return out
    narrative = _APPETITE_TIE_BREAK_NARRATIVE.get(appetite, "")
    if not narrative:
        return out
    top = result["algorithm_rank"][0]
    out.append(
        f"- 基于你的{APPETITE_LABELS[appetite]}倾向，同分时算法按「{narrative}」"
        f"加权决出先后。算法第一名「{top}」就是这套权重下的最匹配选项。"
    )
    return out


def _generate_advice(result: dict) -> list[str]:
    """Generate advice based on Q1/Q16/Q17/Q18 + algorithm rank."""
    meta = result["meta"]
    tau = result["kendall_tau"]
    out: list[str] = []
    if tau < 0:
        s_top = result["subjective_rank"][0]
        pos = result["algorithm_rank"].index(s_top) + 1
        out.append(
            f"- 你最想的「{s_top}」按可走通性排在第 {pos} 位。建议把「想要」和"
            "「能走通」分开评估，至少补一下「为什么是这条而不是算法第一的那条」的理由。"
        )
    elif tau > 0.5:
        out.append(
            f"- 你的偏好与可走通性高度一致，「{result['subjective_rank'][0]}」"
            "也是算法第一名，可信度较高。"
        )
    out.extend(_appetite_advice(meta, result))
    if meta.get("school_vs_major") == "A":
        out.append(
            "- 你偏好学校优先：低难/中等专业可以放宽专业选择挑学校档次；"
            "高难专业即使在好学校也难走通，谨慎对待。"
        )
    elif meta.get("school_vs_major") == "B":
        top = result["algorithm_rank"][0]
        inferred = set(meta.get("session_inferred_majors") or [])
        if top in inferred:
            out.append(
                f"- 你偏好专业优先：算法第一名「{top}」是首选，"
                "但该专业为本次现场推断（非词典专业），"
                "建议先在词典里找相近方向做锚点参照、对照分数稳健性。"
            )
        else:
            out.append(
                f"- 你偏好专业优先：算法第一名「{top}」是首选。"
            )
    if meta.get("city_emphasis") in ("C", "D"):
        out.append("- 你重视城市平台：低难/中等专业向一线/新一线倾斜；高难专业更要靠近行业聚集地。")
    if not out:
        out.append("- 综合表现稳定，没有明显的矛盾信号。")
    return out


def render(result: dict, qbank: dict | None = None) -> str:
    """Render the full Markdown report."""
    qbank = qbank or _load_qbank()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [f"# Major-ADI 个性化测评结果（{now}）", ""]

    meta = result["meta"]
    if meta.get("low_confidence"):
        lines.append(
            "> ⚠️ **置信度警告**：本次有多个专业的基础分为现场推断（非词典专业），"
            "结论建议作为方向参考而非精确分数。"
        )
        lines.append("")
    if meta.get("session_inferred_majors"):
        majors = "、".join(meta["session_inferred_majors"])
        lines.append(f"> ℹ️ 现场推断专业：{majors}")
        lines.append("")

    lines += _section_overview(meta, qbank)
    lines += _section_subjective_vs_algo(result)
    lines += _section_score_table(result)
    lines.append("## 逐专业分析")
    lines.append("")
    for i, name in enumerate(result["algorithm_rank"], 1):
        lines += _section_major_card(name, result["majors"][name], i)
    lines.append("## 综合建议")
    lines.append("")
    lines += _generate_advice(result)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "> 算法说明见 `references/scoring_model.md`；基础分表见 `references/baseline_adi.json`。"
    )
    return "\n".join(lines)


def main() -> None:
    """CLI: read result.json -> print or save Markdown."""
    parser = argparse.ArgumentParser(description="Render ADI result as Markdown")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    with open(args.input, encoding="utf-8") as f:
        result = json.load(f)
    md = render(result)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md)
    else:
        print(md)


if __name__ == "__main__":
    main()
