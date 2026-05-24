# CLAUDE.md

## Commands

```bash
# 同步开发依赖（与 CI 一致）
uv sync --group dev

# 跑测试
uv run pytest

# 入口①：按成绩推荐专业
uv run python scripts/admission_recommender.py --input examples/student_profile.json --top 15

# 入口②：ADI 综合测评（生成 Markdown + HTML 报告）
uv run python scripts/run_assessment.py --input tests/fixtures/case_a_input.json --out-dir ./out

# 提交前自查
uv run pre-commit run --all-files
```

## Architecture

两个独立入口，共享 `references/` 数据层：

```
scripts/
  admission_recommender.py  # 入口①：省份+分数+选科 → 可报专业四档排序
  run_assessment.py         # 入口②：编排 score_engine → render_markdown/html
  score_engine.py           # ADI 4 维度乘积打分核心 (paths/reach/correct/recover)
  render_markdown.py        # Markdown 报告（纯模板，不调 LLM）
  render_html.py            # HTML 单页 + 雷达图（纯模板，不调 LLM）
references/                 # ← single source of truth，代码只读此处
  theory.md                 # ADI 模型理论（真理来源）
  scoring_model.md          # 算法/权重设计 + 完整调参 changelog
  majors_admission_2024.json  # 专业选科要求 + key_subjects 权重
  baseline_adi.json         # 各专业 4 维度基础分
  weights.json question_bank.json provinces.json
```

## Key design constraints

读这些**反直觉**约定，否则容易改坏：

- **分数口径**：传给后端前必须先归一化——语/数/外是 `/150` 原始分，其余科目（选考/理综/文综分科）一律折到 `/100`。后端不做 mode-aware 缩放，错过归一化即数据污染。详见 `SKILL.md`「成绩归一化」。
- **ADI 是反推工程版，不与原算法数字对齐**：目标是**分档结论一致**（低/中/较难/高难），不是精确数字。改权重时盯分档，不要盯绝对分。
- **报告文案 100% 模板生成，不调 LLM**：保证 skill 离线确定性、可单测。新增文案走 `render_*.py` 的模板函数，别引入运行时 LLM 调用。
- **`baseline_adi.json` 与 `majors_admission_2024.json` 的 majors 必须键对齐**（含各自 `_user_additions`）：有 alignment lint 单测守着，漂移会让测试红。
- **Python ≥ 3.10**：代码用 PEP 604 联合类型 `X | None`，3.9 会在 import 阶段 `TypeError`。
- **pre-commit 禁止直接 commit 到 main**：用 feature 分支 + PR，或 bootstrap 场景 `--no-verify`。

## Development Workflow

```
需求模糊  →  /ce:brainstorm  →  docs/brainstorms/
                ↓
            /ce:plan        →  docs/plans/
                ↓
           实现代码
                ↓
            /ce:review      →  修复问题
                ↓
        遇到坑/解决问题  →  /ce:compound  →  docs/solutions/
                ↓
             提 PR
```

| Skill | 触发时机 |
|---|---|
| `/ce:brainstorm` | 需求不清晰，需要发散 |
| `/ce:plan` | 开始实现前，需要多步方案 |
| `/ce:review` | 功能完成后、提 PR 前 |
| `/ce:compound` | 解决了一个非平凡问题后 |


# Karpathy Guidelines

Behavioral guidelines to reduce common LLM coding mistakes, derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.
