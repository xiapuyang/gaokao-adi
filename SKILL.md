---
name: gaokao-adi
description: 中国高考志愿专业路径 ADI 测评（Action Domain Index）。用户选 3 个候选专业 + 答 14 道个人素质问卷，给出每个专业的个性化分数（1-625）、瓶颈维度、主观偏好 vs 客观可走通对比，并生成 Markdown 报告 + HTML 单页可视化。基于风灵之声 ADI 模型工程化复刻。当用户提到「gaokao-adi」「专业测评」「ADI 测评」「专业路径打分」「高考选专业打分」「帮我看几个专业」「风灵 ADI」「这条路你走得通吗」「专业测评一下」「孩子选专业」「子女选专业」时使用。即使用户只说「测一下专业」「比较几个专业」「选专业打个分」也应该触发——只要场景是中国高考志愿且涉及"专业难度评估"，就直接调本 skill，不要让用户去查百度或自己拍脑袋。
---

# Gaokao-ADI 中国高考专业路径测评

## 什么时候用

**触发场景：**
- 用户提到中国高考志愿、专业选择，想比较几个专业的"能不能走通"
- 用户给出 1-3 个候选专业（或希望先听推荐再细评）
- 给子女做志愿参考、给亲友家庭做测评

**不要触发：**
- 兴趣测评 / MBTI / 性格测试（本 skill 不评估兴趣，只评估路径可走通性）
- 大学排名查询 / 录取分数线查询（本 skill 不做这类客观数据查询）
- 欧美 / 加拿大留学专业评估（基础分表严格限定中国 2026 市场）

## 核心理念

ADI 4 维度（每项 1-5 分，相乘 1-625）：
- **paths 路径数量**：技能跨行业能力
- **reach 成功可达性**：普通学生四年后能否拿到体面结果
- **correct 纠偏能力**：发现不适合时能否换方向
- **recover 损失可控性**：失败后沉没成本

分档：>300 低难、150-300 中等、50-150 较难、<50 高难。

**核心信念**：作者算法不公开；我们的算法是从公开案例反推的工程实现，保证**分档结论一致**（不保证数字精确对齐）。详见 `references/scoring_model.md` 顶部 disclaimer。

## 主流程

### Step 0: 暖场 + 询问是否要"按成绩推荐"分支

读 `references/theory.md` 前 30 行，用 3-4 句话向用户复述 ADI 模型。然后**问一个分支决策**（用 AskUserQuestion 单题 2 选项）：

> 你已经选好 3 个目标专业了，还是想先按你的高考成绩+选科推荐一批匹配专业，再从中挑 3 个进 ADI 测评？
> - **A. 我已经选好了** → 直接进 Step 2'（对话报 3 个专业）
> - **B. 先按成绩推荐** → 走 Step 1a 收集分数/选科/喜好 → Step 1b 跑推荐 → 用户挑 3 个 → 进 Step 3

如果选 A：跳到 Step 2'。
如果选 B：走下面 Step 1a/1b（推荐分支），然后 admission_score 会带入 ADI 计算。

### Step 1a（推荐分支）: 收集省份 + 高考成绩 + 选科 + 喜好

用对话收集，**先问省份**（决定 mode）：

```
请告诉我：
1. 省份（如：广东、北京、上海、西藏等）
2. 高考成绩（语文/数学/外语三门必填；选考科目按你的实际选考填）：
   - 语文 /150
   - 数学 /150
   - 外语 /150
   - 选考 1 / 选考 2 / 选考 3（科目名 + 原始分；3+3 满分 100；3+1+2 首选 100/再选赋分 100）
3. 哪些科目你最喜欢？（1-3 个）— **v1.9 起**：在 fit_score 里每命中专业 key_subjects 一项加 +0.05 bonus（cap 1.0）
4. 哪些科目你最不喜欢？（1-3 个）— 命中 key_subjects 权重 ≥0.25 的专业会被软过滤拦截
```

读 `references/provinces.json` 推断 mode（北京/上海/天津/浙江/山东/海南→3+3 系列；河北等 23 省→3+1+2；新疆/西藏→traditional）。
传统模式额外问"理科/文科"。

### Step 1b（推荐分支）: 跑推荐脚本 + 让用户选 3 个

拼装 student profile JSON：
```json
{
  "province": "广东", "mode": "3+1+2", "track": null,
  "scores": {"语文": 115, "数学": 138, "外语": 130,
             "物理": 90, "化学": 85, "生物": 82},
  "electives": ["物理", "化学", "生物"],
  "favorite_subjects": ["数学"],
  "disliked_subjects": ["语文"]
}
```

写到 `$CLAUDE_JOB_DIR/student.json`，跑：
```bash
cd ~/.claude/skills/gaokao-adi && \
  python scripts/admission_recommender.py --input <path> --top 15
```

输出按分类（strong → consider → not_recommended → ineligible）+ score 降序。
把前 15 条贴回对话，**询问用户从中挑 3 个**（必须先在 strong/consider 桶里挑；如果用户想挑 not_recommended 也允许，但要提示原因）。

**记录每个被选专业的 fit/score**——后续作为 `_admission_scores` 字段传给 ADI。

### Step 1: 暖场说明（原 Step 1，现移至此）

读 `references/theory.md` 前 30 行，用 3-4 句话向用户复述：
1. ADI 是什么（不是 MBTI，是"路径可走通性"评估）
2. 流程：选 3 个候选专业 + 答 14 题素质问卷
3. 输出：每个专业的分数 + 瓶颈维度 + Markdown + HTML 报告
4. 一句关键 disclaimer：「算法是从原作公开案例反推的工程版本，分档结论可信，具体数字仅供横向比较」

### Step 2: 第 1 轮问卷（4 题）—— 偏好 + 专业 1 大类

用 AskUserQuestion 一次抛 4 题：

1. **Q01 路径偏好**：稳定路径型 / 可调整路径型 / 冲上限路径型
2. **Q02a 专业 1 大类**：先选大类（理工基础学科 / 计算机与电子 / 传统工程 / 医学类）
3. **Q02b 专业 1 大类（续）**：商科类 / 文社科类 / 应用与艺术 / 其他（自由输入）
4. **Q15 近期状态**：上升 / 稳定 / 下降

⚠️ 第 2 题和第 3 题是「大类两段问」——因为 AskUserQuestion 单题最多 4 选项，7 大类要分 2 题问。让用户在第 2 题或第 3 题里选「我看下面那栏的选项」也可以。**实际处理**：直接告诉用户「7 个大类分两题展示，请在你的目标大类那一题选中即可，另一题随便选一个不相关的占位也行」。

实际上更简洁的实现：把"第 1 轮"拆成"先问 Q01 + Q15 + Q02 大类分组（2 选 1：理工医学组 vs 商文艺组）+ 占位"，然后第 2 轮再问具体大类。或者干脆每个专业各起一轮。

**推荐实现**：每个专业用 **2 轮 AskUserQuestion** 走完——一轮选大类（分 2 题展示 7 大类），一轮选具体专业。这样 3 个专业 = 6 轮，加上其他题约 9-10 轮交互。比较啰嗦但流畅。

**更简化实现**（推荐）：第 1 步**用一段普通对话**让用户**直接打字告诉你 3 个候选专业的名字**（参考 `references/question_bank.json` 的 39 个名称），跳过大类菜单。然后用 AskUserQuestion 只问个人素质题。

> **决策**：默认走简化方案——让用户打字报 3 个专业名 + 每个专业的资源等级（A/B/C）。AskUserQuestion 主要用于素质题。

### Step 2'（推荐）: 用对话收集 3 个专业 + 每个专业的资源

用一段对话：
```
请告诉我：

1. 你的 3 个候选专业（按最想报考的顺序列 1/2/3）
2. 每个专业的资源等级（A 明显有: 直系亲属,直接资源 / B 有一些: 间接资源,人脉 / C 基本没有）
   - 资源 = 直系亲属在该行业、可直接对接的内推、知名企业实习机会、家族企业等
   - 例：「父亲是三甲医院主任医师 → 临床医学 A」「叔叔在 BAT 做算法 → 计算机 B」「都没有 → C」

可选专业列表见 references/question_bank.json，或直接告诉我你想填的名字
（解析顺序：主表 → 别名表 → _user_additions → 现场推断后自动落盘）。

格式示例：
1. 计算机类/软件工程 — C
2. 数据科学/AI — B
3. 金融学 — C
```

用户回答后，Claude 把答案记下来。专业名解析顺序见下面「专业名解析顺序」段；只有四层都未命中才走「现场推断」流程。

（**路径偏好 Q1** 和 **近期状态 Q15** 不在这一步问，留给 Step 5 用 AskUserQuestion 结构化收集——选项多、措辞要精确，结构化展示比口述更靠谱。）

### Step 3-5: 三轮 AskUserQuestion（共 12 题，每轮 4 题）

| 轮 | 题目 | 主题 |
|---|---|---|
| Step 3 | Q01 路径偏好 / Q15 近期状态 / Q16 学校 vs 专业 / Q17 城市重视度 | **偏好与状态族**（前置）|
| Step 4 | Q08 学习能力 / Q09 难度承受 / Q10 试错 / Q11 调整 | **能力族** |
| Step 5 | Q12 家庭支持 / Q13 长期投入 / Q14 拓展习惯 / Q18 风险态度 | **意愿+风险族**（后置）|

**为什么 Q01 前置、Q18 后置（v3.1 起的关键设计）**：

Q01（路径偏好）和 Q18（风险态度）测的是**同一构念的两个层面**：
- Q01：**认知/价值**层 —— "你希望什么样的人生路径"
- Q18：**行为/情绪**层 —— "你面对不确定时通常什么状态"

把它们分置首尾的目的是经典心理测量学的**重复测量信度检验**：
- Q01 前置（用户还没被能力/意愿题"调动思维"前）→ 捕捉**未启动的认知偏好**
- Q18 后置（用户经过 14 题素质题暴露真实自我后）→ 捕捉**真实情绪反应**
- 两题间隔 11 题，**consistency-bias 最低**，矛盾（如 Q01=C 想冲 + Q18=A 求稳）才会被诚实暴露

矛盾组合（AC/CA）触发 `appetite="contradiction"`，SKILL.md 末尾「Q1/Q18 矛盾交叉验证」节会让 Claude 主动复核——把诊断价值还给用户。

**v3.0 之前 Q01 放在最后一轮**——这是错误的设计选择，会让 user 在 Q01 时已被前面题"启动"，丢失干净的认知信号。v3.1 起修正。

### AskUserQuestion 调用规范（v3.0 起严格执行）

**单一真理源**：所有 question/option 文案 **verbatim** 来自 `references/question_bank.json`，**禁止**自创补充或改字。

**字段映射**：

| AskUserQuestion 参数 | 数据源 |
|---|---|
| `question` | `qbank[id].title + "：" + qbank[id].subtitle`（subtitle 缺失则只用 title）|
| `header` | `qbank[id].title` 的前 12 字 |
| 每个 option 的 `label` | `qbank[id].options[i].key + " " + qbank[id].options[i].label`（如 "A 强"）|
| 每个 option 的 `description` | **多行**：`qbank.description`（含 `\n` 分隔的多个 bullet 子句）+ 换行 + `"（参考：" + notes + "）"` |

**description 多行渲染细节（v3.0 新增）**：

`qbank.description` 现在是**多 bullet 串**（用 `\n` 分隔），原始问卷里每个选项是 2-3 个并列短句。AskUserQuestion 调用时**保留 `\n` 不变** ——AskUserQuestion 渲染器会把每行作为一个 bullet 展示，无需手动加 `•` 前缀。

最终 description 字段 = `qbank.description + "\n（参考：" + qbank.notes + "）"`

**违规警示**：

| 错误 | 例子 |
|---|---|
| 自创锚点 | 不要凭印象加"高考 600+/班级头部"——这些必须从 `notes` 字段读取，不能自己写 |
| 改字 | "D 弱" 不要写成 "D 较弱"；"维"不要漏成"惟" |
| 错别字 | 严禁出现"绿高考考""召难""携动"这类拼接错误 |
| 合并 bullet | 不要把 `"希望进入体系后稳定；不希望频繁折腾；可以接受慢"` 合成单行——保留 \n 分隔 |
| 删除 bullet | 原始 3 条 bullet 必须 3 条都保留，不要简化为 1 条 |
| 补充建议性文字 | 不要加"不建议走对学习要求高的专业"这种 description 之外的话（除非 notes 里有）|

**示例（Q08）**：

```python
AskUserQuestion(question=[{
  "question": "学习能力：理解和掌握新知识的能力",
  "header": "学习能力",
  "options": [
    {
      "label": "A 强",
      "description": "学新知识通常很快能抓住重点\n面对较复杂内容，也能较快理解\n学过后能较稳定掌握\n（参考：高考预期 600+ 或代表性奖项；班级/年级头部；能举一反三）"
    },
    {
      "label": "B 中上",
      "description": "大多数内容可以理解\n较难内容需要多花一些时间\n经过练习通常可以掌握\n（参考：努力后能达到较好状态；班级中上水平；高考接近一本线或略高）"
    },
    {
      "label": "C 中下",
      "description": "基础内容可以学会\n稍复杂的内容就容易吃力\n需要较多重复才能掌握\n（参考：努力后能达到中等水平；机械性学习为主；举一反三较弱）"
    },
    {
      "label": "D 弱",
      "description": "新知识理解较慢\n稍难内容就容易跟不上\n即使反复学，掌握也不稳定\n（参考：学新东西吃力，需要反复讲解才能听懂；不建议读对学习要求高的专业）"
    }
  ],
  "multiSelect": false
}])
```

**全 12 个 substantive 题（Q01/Q08-Q18）含 notes 字段**——每个选项的 description 后必须追加 `（参考：…）` 行；Q02-Q07 资源题不含 notes，照原 description 走即可。

### Step 6: 拼装 input.json 并落盘

把所有答案组装为：

```json
{
  "majors": [
    {"rank": 1, "name": "<专业1>", "resource": "<A|B|C>"},
    {"rank": 2, "name": "<专业2>", "resource": "<A|B|C>"},
    {"rank": 3, "name": "<专业3>", "resource": "<A|B|C>"}
  ],
  "answers": {
    "Q01": "<A|B|C>", "Q08": "<A-D>", "Q09": "<A-D>", "Q10": "<A-D>",
    "Q11": "<A-D>", "Q12": "<A-D>", "Q13": "<A-D>", "Q14": "<A-D>",
    "Q15": "<A|B|C>", "Q16": "<A|B|C>", "Q17": "<A|B|C|D>", "Q18": "<A|B|C>"
  },
  "_session_overrides": { /* 仅当有"其他"专业时填 */ }
}
```

写到 `$CLAUDE_JOB_DIR/gaokao-adi-input.json` 或临时文件。

**如果走了推荐分支（Step 1a/1b）**：再加三个字段：

```json
"_admission_scores": {
  "计算机类 / 软件工程": 0.86,
  "数学": 0.72,
  "经济学": 0.65
},
"_admission_pool": [
  {"name": "计算机类 / 软件工程", "score": 0.86, "category": "strong"},
  {"name": "数据科学 / 人工智能", "score": 0.84, "category": "strong"},
  ...
],
"_student_profile": {
  "scores": {"语文": 130, "数学": 138, "外语": 132, "物理": 88, "化学": 85, "生物": 82},
  "electives": ["物理", "化学", "生物"],
  "favorite_subjects": ["数学"],
  "disliked_subjects": ["语文"]
}
```

- `_admission_scores`（v1.4 起）：ADI 算分时 `final = ADI × (0.7 + 0.3 × admission_score)` 衰减不匹配专业。
- `_admission_pool`（v2.0 新增）：完整的 recommend() 输出（贴回对话给用户挑 3 个那批），用于"额外推荐"章节的候选池——extras 仅取 strong/consider 桶。
- `_student_profile`（v2.0 新增）：原始分 + 选科 + 喜好，透传到 HTML 报告的"成绩匹配度"小节（Phase B）。

**直选分支（Step 2'）** 无以上字段也能跑通，但 HTML 报告会缺 Phase B 小节，且 extras 章节走 fallback 池（全 39 专业，会加"未做选科合规校验"警告）。

### Step 7: 调脚本

```bash
python -m scripts.run_assessment \
    --input <input_path> \
    --out-dir <cwd_or_user_dir>
```

工作目录要在 `~/.claude/skills/gaokao-adi/` 下跑，否则 Python 找不到 `scripts.*` 模块。建议：

```bash
cd ~/.claude/skills/gaokao-adi && \
  python -m scripts.run_assessment --input /tmp/gaokao-adi-input.json --out-dir /tmp/
```

### Step 8: 把 Markdown 报告贴回对话

stdout 是渲染好的 Markdown，直接贴。stderr 给出文件路径，告诉用户：

```
报告已保存：
- Markdown: /tmp/gaokao-adi-report-YYYYMMDD-HHMMSS.md
- HTML 单页: /tmp/gaokao-adi-report-YYYYMMDD-HHMMSS.html

要不要用浏览器打开 HTML 看可视化（雷达图+柱状图）？
```

## 专业名解析顺序（重要）

接到用户报的专业名后，按以下顺序解析，**不要跳步**：

1. **主表精确匹配** — 查 `baseline_adi.json.majors`（39 个条目）
2. **别名表** — 查 `baseline_adi.json._aliases`，如「集成电路 → 微电子」「数据科学/AI → 数据科学 / 人工智能」。命中后用规范化名走后续逻辑
3. **用户追加表** — 查 `baseline_adi.json._user_additions`（之前现场推断后落盘的专业）
4. **现场推断 + 自动落盘** — 都没命中才走这条；推断完**不要询问**用户是否落盘，直接 Edit 写到 `_user_additions`（带 `_source: "claude-inferred"`, `_date`, `_confidence: "medium"`）

第 1-3 步都不需要用户介入，对话层无感知。第 4 步才是「其他专业」流程。

## 「其他」专业的现场推断流程

仅当主表 + 别名 + 用户追加表三层都未命中时执行：

### 步骤 1：当场推断 4 维度 + admission 字段

读 `references/scoring_model.md` 的「七、'其他'专业现场推断指引」段，用其中的判断标准给出 4 个 ADI 分数（1-5）+ 3 字段 rationale（baseline/resource/ai_impact）。**呈现格式：**

```
航空航天工程（中国 2026 视角）：
- paths 3 — 航天/民航/军工/科研，方向有 3-4 个但跨度有限
- reach 3 — 普通本科需深造或考公；纯就业 50% 左右
- correct 2 — 资格门槛高、转 CS/电子需要 2-3 年
- recover 3 — 沉没成本中等（4 年 + 可选转 EE 族）

依据：与电气族相邻，但行业更封闭、地域集中。基础分约 54（较难）。
```

**v2.1 起额外推断 admission 字段**（招生规则，用于后续 recommend()）：

| 字段 | 推断规则 |
|---|---|
| `required_primary` | 工科 → `"物理"`；文社科 → `"历史"`；医学/商科/中性 → `null` |
| `required_electives_all` | 强制选考。医学类常含 `["化学"]`；其他多为 `[]` |
| `traditional_track` | 工科/理科基础 → `"理"`；文社科 → `"文"`；商科/中性 → `"both"` |
| `key_subjects` | 从 ADI 维度反推权重，sum = 1.0。工科 dominant：数学+物理 ≥ 0.55；文科 dominant：语文 ≥ 0.30；医学：化学+生物 ≥ 0.45 |
| `soft_thresholds` | 仅 key_subjects 中权重 ≥ 0.20 的科目设阈值。参考 `weights.json::_subject_max_scales` 满分。工科物理 ~80/100、数学 ~110/150；纯数学/统计学专业 ~125+/150 |
| `tags` | 2-4 个领域标签 |
| `confidence` | 推断时设 `"medium"`（原始 39 个有数据的设 `"high"`）|

**示例（航空航天工程）**：

```
required_primary: 物理
required_electives_all: []
traditional_track: 理
key_subjects: {物理: 0.30, 数学: 0.30, 外语: 0.15, 化学: 0.15, 语文: 0.10}
soft_thresholds: {物理: 80, 数学: 105}
tags: [航天工程, 国防, 制造业]
```

呈现给用户时，ADI 4 维度是主要确认对象（步骤 2 走 AskUserQuestion），admission 字段简要列出但**不让用户改**（这些是高考客观规则，不是个人价值判断）。

### 步骤 2：AskUserQuestion 单题确认

选项：
- A 同意
- B 我要调整（请告诉我改哪个维度成几分）
- C 换一个专业

如果用户选 B：让他用自然语言告诉你改动（如「reach 改成 2」），Claude 接收后**再确认一次**最终值。

### 步骤 3：自动落盘到**两个文件**的 `_user_additions`（不询问）

v2.1 起，必须同时写两个文件，否则 `test_admission_baseline_keys_aligned` lint 失败：

1. `references/baseline_adi.json._user_additions`（ADI 4 维度 + rationale）
2. `references/majors_admission_2024.json._user_additions`（admission 字段，步骤 1 推断的那些）

**不要再问**「要不要永久落盘」。下次同一专业自动复用。

落盘 schema：

**baseline_adi.json._user_additions["航空航天工程"]**：
```json
{
  "paths": 3, "reach": 3, "correct": 2, "recover": 3,
  "resource_sensitivity": "default", "ai_impact": "neutral",
  "_source": "claude-inferred", "_date": "<today>", "_confidence": "medium",
  "rationale": {"baseline": "...", "resource": "...", "ai_impact": "..."}
}
```

**majors_admission_2024.json._user_additions["航空航天工程"]**：
```json
{
  "required_primary": "物理",
  "required_electives_all": [],
  "required_electives_any": [],
  "traditional_track": "理",
  "key_subjects": {"物理": 0.30, "数学": 0.30, "外语": 0.15, "化学": 0.15, "语文": 0.10},
  "soft_thresholds": {"物理": 80, "数学": 105},
  "tags": ["航天工程", "国防"],
  "confidence": "medium",
  "_source": "claude-inferred", "_date": "<today>"
}
```

入了两个 `_user_additions` 之后，本次运行无需 `_session_overrides`——直接跑算分引擎（baseline 读取 `_user_additions`）和 admission_recommender（v2.1 起自动合并 majors + `_user_additions`）。

### 何时仍走 `_session_overrides`

仅当用户**明确表示这次推断仅供实验、不要落盘**时（罕见）。默认行为是直接落盘。

## 置信度警告

如果有 ≥1 个专业走 `_session_overrides`，报告里会自动出现「现场推断专业」提示；
如果 ≥2 个，会出现红色「⚠️ 置信度警告」横幅；
3 个都走 → 建议用户至少保留 1 个词典内专业作为锚点参照。

## Q1/Q18 风险倾向矛盾交叉验证

`compute_all` 的 meta 包含 `risk_appetite` 字符串与 `appetite_contradiction: bool`。Q01 与 Q18 方向相反时，appetite 直接是 `"contradiction"`（v1.7 起独立 label，不再与 BB 的 "neutral" 同名）：

- Q01=A（稳定路径型） + Q18=C（进取）→ `appetite = "contradiction"`
- Q01=C（冲上限路径） + Q18=A（避险）→ `appetite = "contradiction"`

报告自动在「概览」chip 上挂 ⚠️ 标记 + 走特殊文案，并在「综合建议」里加一句"自相矛盾，先想清楚到底要稳还是要冲"。`appetite_tie_break_weights` 在 contradiction 下显式 all-zeros（v1.8 起；v1.7 之前是 None），加权和恒为 0，同分按 majors_input 稳定排序——语义"矛盾下不偏好任何维"写在矩阵 JSON 里，单一真理源。

**Claude 主持流程的额外动作**：
跑完脚本贴报告**之前**，如果 `risk_appetite == "contradiction"`，先向用户复核一句：

> "你 Q1 选了「{path_label}」、Q18 选了「{risk_label}」，这两个一个偏稳一个偏冲。要不要先确认哪个更接近你真实的状态？要改就告诉我重答；否则我按当前的矛盾标记继续生成报告，同分时不做 tie-break。"

如果用户重答 → 改 input.json 的 Q01/Q18 字段后重跑脚本（不必从头问 18 题）。如果坚持不改 → 报告里 appetite 仍是 "contradiction"，不影响 ADI 总分（appetite 已不进 ADI 乘法），只是少了 tie-break 帮助。

**风险倾向不影响 ADI 分数**：从 v1.5 起，Q01/Q18 不再参与 recover/paths 维度的乘法。它们仅用于（a）同分时的 tie-break 排序，（b）报告措辞，（c）上面这条矛盾复核。ADI total 保持"客观可走通"语义不被主观偏好污染。

**tie-break 加权三角（v1.6）**：tie-break 不再是单维度，而是 4 维加权和 `tie_break_score = Σ(w[dim] × adjusted[dim])`，权重矩阵在 `weights.json::appetite_tie_break_weights`。

派生公式：Q1 三选项的维度依赖三角 → 5 级线性插值：

| Q1 选项 | 核心动机 | 维度依赖（A/B/C profile） |
|---|---|---|
| A 稳定路径型 | 不失败 | reach + recover |
| B 可调整路径型 | 保转换权 | paths + recover + correct |
| C 冲上限路径型 | 高天花板 | paths + correct |

| Appetite | paths | reach | correct | recover | 来源 |
|---|---|---|---|---|---|
| strong_averse (AA) | 0.00 | 0.50 | 0.00 | 0.50 | 1.0×A |
| averse (AB/BA) | 0.10 | 0.35 | 0.10 | 0.45 | 0.7A + 0.3B |
| neutral (BB) | 0.34 | 0.00 | 0.33 | 0.33 | 1.0×B |
| seeking (BC/CB) | 0.45 | 0.00 | 0.45 | 0.10 | 0.3B + 0.7C |
| strong_seeking (CC) | 0.50 | 0.00 | 0.50 | 0.00 | 1.0×C |

要调整权重（如让 B 更偏 recover），改 `weights.json` 里这张矩阵即可，无需改代码。矩阵每行 sum=1.0 是约定不是硬约束（脚本不强校验）。

## Error Handling

- **专业名拼写不在词典**：先查 `_aliases` 是否命中（如「数据科学/AI」「集成电路」等都已别名）；命中则用规范化名继续。仍未命中再走「其他专业现场推断流程」。仅当用户明显拼错（如「计算机科学」想说「计算机类」）才问澄清。
- **用户答案有缺漏（< 14 题）**：拒绝运行，告知至少需要 Q08~Q14 这 7 道 + Q15 + 3 个专业 + 资源题；可以从断点续填。
- **run_assessment.py 退出码 != 0**：把 stderr 原样贴给用户，保留 input.json 路径让用户重跑或排查。
- **Chart.js CDN 加载失败**（HTML 在无网环境）：模板已内置降级——加载失败时把 canvas 替换为提示框，数据仍能从上方表格看到。
- **Python 环境缺失**：提示用户先 `python3 --version` 确认；本 skill 仅用标准库 + argparse，无外部依赖。

## 输出模板（ALWAYS use this exact framing）

跑完脚本后，把 stdout 的 Markdown 原样贴回对话。**不要重写**——Markdown 已经是最终格式。然后**再补一段**简短结语：

```markdown
---

**下一步建议（你挑）：**
1. 用浏览器打开 HTML 看可视化雷达图
2. 调整某个素质答案重测（比如 Q13 改成 D 看分数怎么变）
3. 把这个测评结果存到 vault（如 Databases/子女教育/）— 调用 add-to-db skill
```

如果 τ < 0：在结语前加一行 `> ⚠️ 你最想的专业不是算法第一名。建议先回看「主观 vs 客观」对照表再做决定。`

## 内部约定

- **不要直接写 Python 算分**：所有数学走 `scripts/score_engine.py`，保确定性。
- **不要在脚本里调 LLM**：算法引擎是纯数学；LLM 推断只发生在 Claude 对话里（"其他"专业现场推断）。
- **不要修改 `baseline_adi.json` 的 majors 段**：词典专业的基础分是 plan 阶段定下的，只能往 `_user_additions` 追加。
- **不要降低 pytest 覆盖**：调权重时如果案例 A/B 测试红了，按 `scoring_model.md` 的调参 SOP 处理——更新断言区间并在调参日志记一笔，不要直接删测试。

## 参考文件

- `references/theory.md` — ADI 模型理论摘要 + 原文链接
- `references/baseline_adi.json` — 39 专业 × 4 维度基础分
- `references/scoring_model.md` — 算法公式、权重、案例验证
- `references/weights.json` — 个性化修正系数
- `references/question_bank.json` — 18 题完整结构化定义
- `scripts/score_engine.py` — 算分引擎（确定性）
- `scripts/render_markdown.py` — Markdown 渲染
- `scripts/render_html.py` — HTML 单页渲染
- `scripts/run_assessment.py` — 主入口 CLI
- `tests/` — pytest 用例（案例 A/B 保险丝）
