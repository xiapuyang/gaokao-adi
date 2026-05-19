"""Render a personalized ADI result dict into a self-contained HTML page.

Template placeholders live in `assets/report_template.html`.
Chart.js is loaded from jsDelivr; if it fails, the page degrades to tables.
"""
import argparse
import html
import json
from datetime import datetime
from pathlib import Path

from scripts.render_markdown import (
    APPETITE_LABELS,
    DIMENSION_LABELS,
    TAU_INTERPRETATION,
    _generate_advice,
    _interpret_tau,
    _load_qbank,
    _option_label,
)
from scripts.score_engine import DIMENSION_KEYS as DIM_ORDER

_BASE = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = _BASE / "assets" / "report_template.html"

GRADE_COLOR = {"低难": "#2da45e", "中等": "#d9b13a", "较难": "#e07a3a", "高难": "#c43c4d"}

_Q_SHORT_LABEL = {
    "Q08": "学习能力", "Q09": "难度承受", "Q10": "试错能力",
    "Q11": "调整能力", "Q12": "家庭支持", "Q13": "长期投入意愿",
    "Q14": "拓展习惯", "Q15": "近期状态",
}

_WHEN_PRIORITY_RULES = {
    "recover": "你更看重稳定就业 / 失败成本可控",
    "paths": "你想要更宽的方向选择空间",
    "correct": "你担心走错路想留余地纠偏",
    "reach": "你想要更容易入门的门槛",
}


def _esc(s: str) -> str:
    """HTML-escape `s` for safe interpolation into element content and attributes."""
    return html.escape(str(s), quote=True)


_RATIONALE_HTML_DISPLAY = (
    ("baseline", "📝 基础判断"),
    ("resource", "💼 资源敏感度依据"),
    ("ai_impact", "🤖 AI 影响依据"),
)


def _rationale_li(rationale: object) -> list[str]:
    """Render rationale (dict with 3 layers, or legacy string) as <li> entries."""
    if isinstance(rationale, dict):
        out = []
        for key, label in _RATIONALE_HTML_DISPLAY:
            text = (rationale.get(key) or "").strip()
            if text:
                out.append(f"<li><strong>{label}</strong>：{_esc(text)}</li>")
        return out
    if isinstance(rationale, str) and rationale.strip():
        return [f"<li>📝 基础判断：{_esc(rationale.strip())}</li>"]
    return []


def _contributor_li(c: dict, css_class: str, prefix: str, pct_fmt: str) -> str:
    """Render a top_lift / top_drag contributor as a <li> entry."""
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
    return f'<li class="{css_class}">{prefix}：{_esc(tag)} 把「{_esc(dim_label)}」{pct_str}</li>'


def _overview_chips(meta: dict, qbank: dict) -> str:
    """Render five user-profile chips, including derived risk_appetite."""
    appetite = meta.get("risk_appetite", "neutral")
    appetite_label = APPETITE_LABELS.get(appetite, "中立权衡")
    if appetite == "contradiction":
        appetite_label = f"⚠️ {appetite_label}（Q1×Q18 反向）"
    items = [
        ("路径偏好", _option_label(qbank, "Q01", meta["preference_path"])),
        ("风险态度", _option_label(qbank, "Q18", meta["risk_tone"])),
        ("综合风险倾向", appetite_label),
        ("学校 vs 专业", _option_label(qbank, "Q16", meta["school_vs_major"])),
        ("城市重视", _option_label(qbank, "Q17", meta["city_emphasis"])),
    ]
    return "".join(
        f'<span class="chip"><strong>{_esc(k)}</strong>：{_esc(v)}</span>' for k, v in items
    )


def _subjective_table_html(result: dict) -> str:
    """Render the subjective vs algorithm rank comparison table."""
    rows = []
    for i, (s, a) in enumerate(zip(result["subjective_rank"], result["algorithm_rank"]), 1):
        same = s == a
        mark = '<span class="same">✓</span>' if same else '<span class="swap">↔</span>'
        rows.append(
            f"<tr><td>{i}</td><td>{_esc(s)}</td><td>{mark}</td>"
            f"<td>{i}</td><td>{_esc(a)}</td></tr>"
        )
    return (
        '<table><thead><tr><th>你的偏好序</th><th>专业</th><th></th>'
        '<th>算法序</th><th>专业</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _score_table_html(result: dict) -> str:
    """Render the top-level score table sorted by ADI descending."""
    rows = []
    for i, name in enumerate(result["algorithm_rank"], 1):
        d = result["majors"][name]
        grade_cls = f"grade-{d['grade']}"
        rows.append(
            f"<tr><td>{i}</td><td>{_esc(name)}</td>"
            f"<td><strong>{d['total']}</strong></td>"
            f'<td class="{grade_cls}">{d["grade"]}</td>'
            f"<td>{_esc(DIMENSION_LABELS[d['bottleneck']])}</td></tr>"
        )
    return (
        '<table><thead><tr><th>排名</th><th>专业</th><th>总分</th>'
        '<th>分档</th><th>瓶颈维度</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _radar_cells_html(result: dict) -> str:
    """Render one radar canvas per major."""
    cells = []
    for i, name in enumerate(result["algorithm_rank"]):
        cells.append(
            f'<div class="radar-cell"><h4>{_esc(name)}</h4>'
            f'<canvas id="radar-{i}" height="180"></canvas></div>'
        )
    return "".join(cells)


def _contributor_tag(c: dict) -> str:
    """Build a human-readable tag for one contributor entry."""
    src = c["source"]
    ans = c.get("answer", "?")
    if src == "ai_impact":
        return f"AI 影响（{c.get('impact_level', '?')}×{c.get('ability_level', '?')}）"
    if src == "resource":
        return f"专业资源={ans}"
    if src in _Q_SHORT_LABEL:
        return f"{src} {_Q_SHORT_LABEL[src]}={ans}"
    return f"{src}={ans}"


def _phase_a_questionnaire_impact(d: dict) -> str:
    """Phase A: trace 4 维度 contributors back to specific Q answers.

    Pick top 3 positive (lifts) and top 2 negative (drags) by abs(delta),
    each shown as a colored bullet with Q-id + 维度 + percentage.
    """
    flattened = []
    for dim, dd in d["dimensions"].items():
        for c in dd.get("contributors", []):
            if c.get("delta", 0) == 0:
                continue
            flattened.append({**c, "dimension": dim})
    if not flattened:
        return ""
    lifts = sorted([c for c in flattened if c["delta"] > 0],
                    key=lambda c: -c["delta"])[:3]
    drags = sorted([c for c in flattened if c["delta"] < 0],
                    key=lambda c: c["delta"])[:2]
    items = []
    for c in lifts:
        items.append(
            f'<li class="lift">✅ {_esc(_contributor_tag(c))} → '
            f'「{_esc(DIMENSION_LABELS[c["dimension"]])}」'
            f'+{c["delta"]*100:.0f}%</li>'
        )
    for c in drags:
        items.append(
            f'<li class="drag">⚠️ {_esc(_contributor_tag(c))} → '
            f'「{_esc(DIMENSION_LABELS[c["dimension"]])}」'
            f'{c["delta"]*100:.0f}%</li>'
        )
    return (
        '<div class="impact-block"><strong>你的画像如何影响这个分</strong>'
        f'<ul>{"".join(items)}</ul></div>'
    )


def _classify_subject_status(
    subj: str, raw: float, norm: float, weight: float,
    threshold: float | None, self_avg: float, is_favorite: bool,
) -> str:
    """Return short status label for a key_subject row in Phase B."""
    parts = []
    if is_favorite:
        parts.append("⭐ 你最爱")
    if threshold is not None and raw < threshold:
        parts.append(f"🚫 低于阈值 {threshold}")
    elif norm < self_avg - 0.15 and weight >= 0.25:
        parts.append("⚠️ 相对偏科")
    elif norm >= self_avg + 0.10:
        parts.append("✅ 强项")
    elif threshold is not None:
        parts.append(f"✓ 达标（≥{threshold}）")
    return "、".join(parts) if parts else "—"


def _phase_b_subject_match(
    name: str, student_context: dict | None, admission_data: dict,
) -> str:
    """Phase B: 成绩与本专业 key_subjects 匹配表（仅推荐分支可见）。"""
    if not student_context:
        return ""
    scores = student_context.get("scores") or {}
    if not scores:
        return ""
    info = (admission_data.get("majors", {}) or {}).get(name, {})
    key_subjects = info.get("key_subjects") or {}
    if not key_subjects:
        return ""
    from scripts.admission_recommender import _normalize_score
    norm_scores = {
        s: _normalize_score(s, v) for s, v in scores.items() if v is not None
    }
    if not norm_scores:
        return ""
    self_avg = sum(norm_scores.values()) / len(norm_scores)
    soft = info.get("soft_thresholds") or {}
    favorites = set(student_context.get("favorite_subjects") or [])
    rows = []
    for subj, weight in sorted(key_subjects.items(), key=lambda x: -x[1]):
        raw = scores.get(subj)
        if raw is None:
            continue
        norm = norm_scores.get(subj, 0.0)
        status = _classify_subject_status(
            subj, raw, norm, weight, soft.get(subj), self_avg,
            subj in favorites,
        )
        rows.append(
            f"<tr><td>{_esc(subj)}</td><td>{weight:.2f}</td>"
            f"<td>{raw}</td><td>{norm:.2f}</td>"
            f"<td>{_esc(status)}</td></tr>"
        )
    if not rows:
        return ""
    return (
        '<div class="match-block"><strong>成绩与本专业 key_subjects 匹配</strong>'
        '<table><thead><tr><th>科目</th><th>权重</th><th>原始分</th>'
        '<th>归一化</th><th>说明</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def _phase_c_summary(d: dict, rank: int) -> str:
    """Phase C: 一段话总结（模板，规则拼装）。"""
    parts = [
        f"在你的三个候选里排第 {rank}，ADI 总分 {d['total']}（{d['grade']}）。"
    ]
    if d.get("top_lift"):
        c = d["top_lift"]
        parts.append(
            f"最大拉抬来自「{DIMENSION_LABELS[c['dimension']]}」"
            f"（{c['delta']*100:+.0f}%）。"
        )
    parts.append(
        f"瓶颈在「{DIMENSION_LABELS[d['bottleneck']]}」，"
        f"决定了上限。"
    )
    adm = d.get("admission_score")
    if adm is not None:
        if adm >= 0.75:
            parts.append(f"招生匹配度 {adm:.0%}，属于强匹配。")
        elif adm >= 0.55:
            parts.append(f"招生匹配度 {adm:.0%}，可考虑。")
        else:
            parts.append(f"招生匹配度 {adm:.0%}，偏低。")
    return (
        '<div class="summary-block"><strong>一句话总结</strong>'
        f'<p>{_esc(" ".join(parts))}</p></div>'
    )


def _load_admission_data() -> dict:
    """Lazy load admission data for Phase B; empty dict on missing/malformed file."""
    try:
        from scripts.admission_recommender import load_admission
        return load_admission()
    except (FileNotFoundError, json.JSONDecodeError):
        return {"majors": {}}


def _major_cards_html(result: dict) -> str:
    """Render the per-major detailed analysis cards with Phase A/B/C subsections."""
    student_context = result.get("student_context")
    admission_data = _load_admission_data() if student_context else {"majors": {}}
    cards = []
    for i, name in enumerate(result["algorithm_rank"], 1):
        d = result["majors"][name]
        rows = []
        for dim in DIM_ORDER:
            dd = d["dimensions"][dim]
            rows.append(
                f"<tr><td>{_esc(DIMENSION_LABELS[dim])}</td>"
                f"<td>{dd['base']}</td><td>×{dd['multiplier']:.2f}</td>"
                f"<td><strong>{dd['adjusted']:.2f}</strong></td></tr>"
            )
        notes = []
        if d.get("top_lift"):
            notes.append(_contributor_li(d["top_lift"], "lift", "🚀 主要拉抬", "抬升 +{pct:.0f}%"))
        if d.get("top_drag"):
            notes.append(_contributor_li(d["top_drag"], "drag", "🪨 主要拖累", "压低 {pct:.0f}%"))
        notes.append(
            f"<li>⚠️ 瓶颈维度：<strong>{_esc(DIMENSION_LABELS[d['bottleneck']])}</strong>"
            "（最低维度锁住总分上限）</li>"
        )
        notes.extend(_rationale_li(d.get("rationale")))
        if d.get("source") == "session_override":
            notes.append("<li>ℹ️ 此专业基础分为本次现场推断，置信度低于词典专业。</li>")
        cards.append(
            f'<div class="card">'
            f'<h3>{i}. {_esc(name)} — {d["total"]} '
            f'<span class="grade-{d["grade"]}">{d["grade"]}</span></h3>'
            '<table><thead><tr><th>维度</th><th>基础</th>'
            f"<th>个人系数</th><th>调整后</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
            f"<ul>{''.join(notes)}</ul>"
            f"{_phase_a_questionnaire_impact(d)}"
            f"{_phase_b_subject_match(name, student_context, admission_data)}"
            f"{_phase_c_summary(d, i)}"
            "</div>"
        )
    return "".join(cards)


def _extra_reason_template(ex: dict, top: dict | None) -> str:
    """Phase 4-3 #1: 一句"为什么推荐"。"""
    parts = []
    if top is not None and top["total"] > 0:
        diff_pct = (ex["total"] - top["total"]) / top["total"] * 100
        if abs(diff_pct) < 5:
            parts.append(f"ADI 与你的最强候选「{top['name']}」基本持平（{ex['total']} vs {top['total']}）")
        elif diff_pct > 0:
            parts.append(f"ADI {ex['total']} 高于你最强候选「{top['name']}」{abs(diff_pct):.0f}%")
        else:
            parts.append(f"ADI {ex['total']} 略低于你最强候选「{top['name']}」{abs(diff_pct):.0f}%，但匹配度值得关注")
    else:
        parts.append(f"ADI 总分 {ex['total']}（{ex['grade']}）")
    score = ex.get("admission_score")
    if score is not None and score >= 0.75:
        parts.append(f"招生匹配度 {score:.0%}（强桶）")
    return "，".join(parts) + "。"


def _extra_compare_template(ex: dict, top: dict) -> str:
    """Phase 4-3 #2: 与最强候选的维度对比。"""
    diffs = [
        (dim, ex["dimensions"][dim]["adjusted"] - top["dimensions"][dim]["adjusted"])
        for dim in DIM_ORDER
    ]
    sig = sorted([(d, v) for d, v in diffs if abs(v) >= 0.3],
                  key=lambda x: -abs(x[1]))[:2]
    if not sig:
        return f"4 维度与「{top['name']}」基本一致"
    parts = []
    for dim, val in sig:
        direction = "高" if val > 0 else "低"
        parts.append(f"{DIMENSION_LABELS[dim]} {direction} {abs(val):.2f}")
    return f"和「{top['name']}」相比：{'、'.join(parts)}"


def _extra_when_priority_template(ex: dict, top: dict | None) -> str:
    """Phase 4-3 #3: 何时优先这个推荐。"""
    if top is None:
        adj = {d: ex["dimensions"][d]["adjusted"] for d in DIM_ORDER}
        top_dim = max(adj, key=lambda d: adj[d])
        hint = _WHEN_PRIORITY_RULES.get(top_dim, f"你看重{DIMENSION_LABELS.get(top_dim, '')}")
        return f"如果{hint}，可优先考虑"
    diffs = {
        d: ex["dimensions"][d]["adjusted"] - top["dimensions"][d]["adjusted"]
        for d in DIM_ORDER
    }
    best_dim, best_diff = max(diffs.items(), key=lambda x: x[1])
    if best_diff < 0.2:
        return "在 ADI 综合得分上是同档优质选项，可作为备选参考"
    hint = _WHEN_PRIORITY_RULES.get(best_dim, f"你看重{DIMENSION_LABELS.get(best_dim, '')}")
    return f"如果{hint}，本专业比「{top['name']}」更对路"


def _extras_section_html(result: dict) -> str:
    """v2.0: HTML 末尾"额外推荐"章节——3 张推荐卡。"""
    extras = result.get("extras") or []
    if not extras:
        return ""
    top_selected_name = result["algorithm_rank"][0] if result.get("algorithm_rank") else None
    top_selected = result["majors"].get(top_selected_name) if top_selected_name else None
    warning = result.get("extras_warning")
    cards = []
    for i, ex in enumerate(extras, 1):
        why = _extra_reason_template(ex, top_selected)
        compare = _extra_compare_template(ex, top_selected) if top_selected else ""
        when = _extra_when_priority_template(ex, top_selected)
        compare_html = (
            f'<p><strong>与你最强候选对比：</strong>{_esc(compare)}</p>'
            if compare else ""
        )
        cards.append(
            f'<div class="card extra-card">'
            f'<h3>#{i} {_esc(ex["name"])} — {ex["total"]} '
            f'<span class="grade-{ex["grade"]}">{ex["grade"]}</span></h3>'
            f'<p><strong>为什么推荐：</strong>{_esc(why)}</p>'
            f'{compare_html}'
            f'<p><strong>何时优先考虑：</strong>{_esc(when)}</p>'
            '</div>'
        )
    warn_html = f'<div class="warn">⚠️ {_esc(warning)}</div>' if warning else ''
    return (
        '<h2>额外推荐（你未选但值得考虑）</h2>'
        f'{warn_html}{"".join(cards)}'
    )


def _advice_html(result: dict) -> str:
    """Render the advice bullet list (sourced from _generate_advice)."""
    items = _generate_advice(result)
    return "".join(f"<li>{_esc(it.lstrip('- '))}</li>" for it in items)


def _confidence_warning(meta: dict) -> str:
    """Render the optional low-confidence and session-inferred warning banners."""
    parts = []
    if meta.get("low_confidence"):
        parts.append(
            '<div class="warn">⚠️ <strong>置信度警告</strong>：本次有多个专业的基础分为'
            "现场推断（非词典专业），结论建议作为方向参考而非精确分数。</div>"
        )
    if meta.get("session_inferred_majors"):
        majors = "、".join(_esc(m) for m in meta["session_inferred_majors"])
        parts.append(f'<div class="info">ℹ️ 现场推断专业：{majors}</div>')
    return "".join(parts)


def _radar_data(result: dict) -> list[dict]:
    """Build per-major radar dataset for Chart.js."""
    labels = [DIMENSION_LABELS[d] for d in ("paths", "reach", "correct", "recover")]
    entries = []
    for name in result["algorithm_rank"]:
        d = result["majors"][name]
        adjusted = [d["dimensions"][dim]["adjusted"] for dim in ("paths", "reach", "correct", "recover")]
        base = [d["dimensions"][dim]["base"] for dim in ("paths", "reach", "correct", "recover")]
        entries.append({
            "name": name,
            "data": {
                "labels": labels,
                "datasets": [
                    {
                        "label": "调整后",
                        "data": adjusted,
                        "backgroundColor": "rgba(45,108,223,0.18)",
                        "borderColor": "#2d6cdf",
                        "borderWidth": 2,
                    },
                    {
                        "label": "基础",
                        "data": base,
                        "backgroundColor": "rgba(150,150,150,0.10)",
                        "borderColor": "#999",
                        "borderWidth": 1,
                        "borderDash": [4, 4],
                    },
                ],
            },
        })
    return entries


def _bar_data(result: dict) -> dict:
    """Build top-level bar chart dataset."""
    labels = list(result["algorithm_rank"])
    totals = [result["majors"][m]["total"] for m in labels]
    colors = [GRADE_COLOR[result["majors"][m]["grade"]] for m in labels]
    return {
        "labels": labels,
        "datasets": [{
            "label": "ADI 总分",
            "data": totals,
            "backgroundColor": colors,
        }],
    }


def render(result: dict, template_path: Path = TEMPLATE_PATH) -> str:
    """Render the full HTML report by substituting placeholders."""
    qbank = _load_qbank()
    with open(template_path, encoding="utf-8") as f:
        tpl = f.read()
    tau = result["kendall_tau"]
    subs = {
        "{{generated_at}}": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "{{confidence_warning}}": _confidence_warning(result["meta"]),
        "{{overview_chips}}": _overview_chips(result["meta"], qbank),
        "{{subjective_table}}": _subjective_table_html(result),
        "{{tau_value}}": f"{tau:+.2f}",
        "{{tau_interpretation}}": _esc(_interpret_tau(tau)),
        "{{score_table}}": _score_table_html(result),
        "{{radar_cells}}": _radar_cells_html(result),
        "{{major_cards}}": _major_cards_html(result),
        "{{advice_list}}": _advice_html(result),
        "{{extras_section}}": _extras_section_html(result),
        # Escape `</` inside JSON to prevent breaking out of <script> blocks
        # if a major name happens to contain a literal closing-tag substring.
        "{{radar_data_json}}":
            json.dumps(_radar_data(result), ensure_ascii=False).replace("</", "<\\/"),
        "{{bar_data_json}}":
            json.dumps(_bar_data(result), ensure_ascii=False).replace("</", "<\\/"),
    }
    for k, v in subs.items():
        tpl = tpl.replace(k, v)
    return tpl


def main() -> None:
    """CLI: read result.json -> write HTML."""
    parser = argparse.ArgumentParser(description="Render ADI result as HTML")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with open(args.input, encoding="utf-8") as f:
        result = json.load(f)
    html_text = render(result)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_text)


if __name__ == "__main__":
    main()
