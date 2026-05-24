# gaokao-adi · 中国高考志愿专业路径测评

> 一个**零依赖**的中国高考志愿辅助工具：先按分数告诉你"能报哪些专业"，再给候选专业打一个"这条路你走得通吗"的综合分。
> 基于风灵之声「专业路径 ADI（Action Domain Index）」模型的工程化复刻。

`gaokao-adi` 同时是一个可独立运行的 Python 命令行工具，也是一个 [Claude Code](https://docs.claude.com/en/docs/claude-code) Skill。

---

## ⚠️ 免责声明（请先读这一段）

**本工具仅供志愿填报参考，不构成任何录取保证或专业建议。** 高考志愿是高风险决策，最终请以教育部考试院、各省考试院、目标院校官方招生章程为准，并结合家庭、老师的判断。

关于分数的诚实表述：

- ADI 的**算法是从风灵之声公开的 4 个标杆案例反推**的工程实现，作者私有算法不公开。本实现的目标是**保证分档结论一致**（低难 / 中等 / 较难 / 高难），而**不是数字精确对齐**。
- 因此请把 ADI 分数当作**横向比较**用（A 专业 vs B 专业谁更走得通），不要把 `312 vs 287` 当成精确差距。
- `references/` 下的招生选科要求、学科权重为 **v1 经验估计 + 教育部 2024 版选考科目指引**，可能与某些院校的具体要求有出入。

---

## 这是什么

工具有**两个独立入口**，可单独用，也可串行用：

| 入口 | 解决什么问题 | 脚本 |
|---|---|---|
| ① 按成绩推荐专业 | "我这个分 + 这个选科，能报哪些专业？" | `scripts/admission_recommender.py` |
| ② ADI 综合分数测评 | "这 1-3 个候选专业，哪条路更走得通？" | `scripts/run_assessment.py` |

- **入口 ①** 根据省份 + 高考成绩 + 选考科目 + 喜欢/不喜欢科目，匹配可报考专业，按 `strong / consider / not_recommended / ineligible` 四档输出 Top N + 解释。
- **入口 ②** 对候选专业做个性化打分（理论范围 `1–625`），覆盖 **paths / reach / correct / recover** 四个维度，输出瓶颈维度、主观偏好 vs 客观可走通对比，生成 **Markdown 报告 + HTML 单页可视化**（含雷达图）。
- **串行用法**：先用 ① 推荐 → 挑出 3 个心仪专业 → 进 ② 做 ADI 测评。

---

## 安装与运行

**要求：Python ≥ 3.10**（代码使用 PEP 604 联合类型注解 `X | None`，3.9 会在 import 阶段报 `TypeError`）。**运行时零第三方依赖**——只用标准库，`git clone` 即可跑。

```bash
git clone https://github.com/xiapuyang/gaokao-adi.git
cd gaokao-adi
```

### 入口 ①：按成绩推荐专业

输入是一份学生档案 JSON（示例见 [`examples/student_profile.json`](examples/student_profile.json)）：

```json
{
  "province": "广东",
  "mode": "3+1+2",
  "track": null,
  "scores": {"语文": 115, "数学": 138, "外语": 130, "物理": 90, "化学": 85, "生物": 82},
  "electives": ["物理", "化学", "生物"],
  "favorite_subjects": ["数学"],
  "disliked_subjects": ["语文"]
}
```

> **分数口径**：语 / 数 / 外为 `/150` 原始分，其余科目（选考 / 理综 / 文综分科）一律折算到 `/100`。详见 `SKILL.md` 的「成绩归一化」一节。

```bash
python scripts/admission_recommender.py --input examples/student_profile.json --top 15
```

输出为排序后的 JSON 列表（`name / fit / category / tags / reason`）。

### 入口 ②：ADI 综合分数测评

输入是「候选专业 + 14 题素质问卷答案」JSON（可参考 [`tests/fixtures/case_a_input.json`](tests/fixtures/case_a_input.json)）：

```bash
python scripts/run_assessment.py --input tests/fixtures/case_a_input.json --out-dir ./out
# 只要 Markdown、跳过 HTML：
python scripts/run_assessment.py --input tests/fixtures/case_a_input.json --out-dir ./out --markdown-only
```

会在 `./out/` 下生成 `gaokao-adi-report-<时间戳>.md` 和同名 `.html`。

---

## 作为 Claude Code Skill 使用

把仓库放到（或软链接到）`~/.claude/skills/gaokao-adi`，Claude Code 会自动发现它。之后用自然语言触发即可，例如：

- "我考了 590 分，物化生，广东，推荐几个专业"（走入口 ①）
- "帮我看看 计算机 / 临床医学 / 法学 这三个专业哪个更适合我"（走入口 ②）

`SKILL.md` 定义了完整的对话流程（暖场、14 题问卷、分支决策、报告呈现），是 Skill 模式下的运行时说明书。

---

## 项目结构

```
gaokao-adi/
├── SKILL.md                  # Claude Code Skill 运行时说明（对话流程 / 问卷 / 模板）
├── scripts/
│   ├── admission_recommender.py  # 入口①：按成绩推荐专业
│   ├── run_assessment.py         # 入口②：ADI 测评流水线（打分 + 渲染）
│   ├── score_engine.py           # ADI 4 维度打分核心
│   ├── render_markdown.py        # Markdown 报告渲染
│   └── render_html.py            # HTML 单页可视化（含雷达图）
├── references/                # 数据与算法文档（single source of truth）
│   ├── theory.md                 # ADI 模型理论摘要
│   ├── scoring_model.md          # 算法与权重设计（含 disclaimer）
│   ├── majors_admission_2024.json  # 专业选科要求 + 学科权重
│   ├── baseline_adi.json / weights.json / question_bank.json / provinces.json
├── assets/report_template.html
├── examples/student_profile.json # 入口① 可跑通示例
└── tests/                     # pytest 测试 + fixtures
```

---

## 开发与测试

```bash
# 用 uv（推荐，自动选对 Python 版本；与 CI 一致）
uv sync --group dev
uv run pytest

# 或自建虚拟环境（不依赖 uv）
python -m venv .venv && source .venv/bin/activate
pip install pytest pytest-cov ruff pre-commit
pytest
```

当前测试套件：**93 passed**。提交前 `pre-commit run --all-files` 会跑 ruff + 各项检查。

---

## 致谢与来源

- **ADI 模型**由作者「**风灵**」（风灵之声）于 2026 年 3 月的三篇文章中提出：
  1. 《张雪峰之后，高考选专业最该问的一件事》— 引入 ADI 四维度 + 标杆案例
  2. 《高考选专业：这条路，你走得通吗？》— 引入个性化测评问卷
  3. 《个人素质对专业选择到底有多大影响？》— 考生 A/B 对照演示个性化修正机制
- 本仓库**不与原作者私有算法对齐**（其算法不公开）。我们做的是「从公开案例 + 标杆值 + 问卷题目 → 反推合理的工程实现」，并在此基础上做了大量参数调优（见 git 提交历史 v3.x 系列）。
- 详细的理论摘要见 [`references/theory.md`](references/theory.md)，算法与权重设计见 [`references/scoring_model.md`](references/scoring_model.md)。

---

## License

[MIT](LICENSE) © 2026 xiapuyang
