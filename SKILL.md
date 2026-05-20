---
name: gaokao-adi
description: 中国高考志愿专业路径双功能工具。功能 ①「按成绩推荐专业」：根据省份+高考成绩+选考科目+喜欢/不喜欢科目，自动匹配可报考专业，按 strong/consider/not_recommended/ineligible 四档输出 Top 15 + 解释（admission_recommender）。功能 ②「ADI 综合分数测评」（Action Domain Index）：对 1-3 个候选专业做个性化打分 1-625，含 paths/reach/correct/recover 4 维度雷达图、瓶颈维度、主观偏好 vs 客观可走通对比，输出 Markdown 报告 + HTML 单页可视化。两功能可串行（先推荐→挑 3 个→进 ADI），也可单独使用。基于风灵之声 ADI 模型工程化复刻。**触发短语（推荐入口）**：「按成绩推荐专业」「我的分能上什么专业」「我考了 X 分推荐专业」「选科匹配专业」「高考分数推荐」「不知道选什么专业」「分数能报哪些专业」「孩子高考分数出来了帮看看报什么」「我物化生选了 XX 分能选什么专业」「推荐几个专业」「专业推荐」。**触发短语（ADI 测评入口）**：「gaokao-adi」「专业测评」「ADI 测评」「专业路径打分」「高考选专业打分」「帮我看几个专业」「风灵 ADI」「这条路你走得通吗」「专业测评一下」「孩子选专业」「子女选专业」「比较几个专业」「选专业打个分」「测一下专业」。只要场景是中国高考志愿且涉及"专业推荐"或"专业难度评估"，就直接调本 skill，不要让用户去查百度或自己拍脑袋。
---

# Gaokao-ADI 中国高考专业路径测评

## 什么时候用

**本 skill 有两个独立入口，可单独用也可串行用：**

| 入口 | 何时触发 | 用什么 |
|---|---|---|
| ① 按成绩推荐专业 | 用户给省份+高考成绩+选科，想知道"我的分能报哪些专业" | `scripts/admission_recommender.py` |
| ② ADI 综合分数测评 | 用户已经有 1-3 个候选专业，想知道"哪条路更走得通" | 14 题素质问卷 + 计算器 + 报告生成 |

**典型组合场景：**
- 「直接给我推荐」→ 只走 ①，输出 Top 15 + 解释，结束
- 「这 3 个专业哪个好」→ 直接走 ②（Step 0.5 选 A 分支）
- 「我不知道选什么，又想认真比较」→ 串行：① 推荐 → 用户挑 3 个 → ② ADI 测评（Step 0.5 选 B 分支，admission_score 会带入 ADI fit 计算）
- 给子女做志愿参考、给亲友家庭做测评

**不要触发：**
- 兴趣测评 / MBTI / 性格测试（本 skill 不评估兴趣，只评估路径可走通性 + 招录匹配度）
- 大学排名查询 / 录取分数线查询（本 skill 不做这类客观数据查询；推荐功能基于专业要求而非历年分数线）
- 欧美 / 加拿大留学专业评估（基础分表+省份配置严格限定中国 2026 市场）

## 核心理念

ADI 4 维度（每项 1-5 分，相乘 1-625）：
- **paths 路径数量**：技能跨行业能力
- **reach 成功可达性**：普通学生四年后能否拿到体面结果
- **correct 纠偏能力**：发现不适合时能否换方向
- **recover 损失可控性**：失败后沉没成本

分档：>300 低难、150-300 中等、50-150 较难、<50 高难。

**核心信念**：作者算法不公开；我们的算法是从公开案例反推的工程实现，保证**分档结论一致**（不保证数字精确对齐）。详见 `references/scoring_model.md` 顶部 disclaimer。

## 主流程

### Step 0: 收集昵称并创建 session 目录（必须先做，第一句话）

任何其他交互之前，先用一句话问昵称：

> 这次测评是给谁做的？给个简短代号就行（例：`student-a`、`kid1`、`self`、`考生1`；不必用真名）。
> 我会把所有过程文件（问卷答案、输入信息、中间结果、报告）放到 `~/.gaokao/<代号>-<时间戳>/` 下，方便你以后回看。
> 提示：目录会持久保留在本机，挑一个**你能识别但不暴露隐私**的代号最好。

收到昵称后**立刻**执行：

1. **清洗 slug**：把空格、`/`、`\`、`:`、`*`、`?`、`"`、`<`、`>`、`|` 等文件系统非法字符替换成 `-`；不限制中文（macOS APFS 原生支持）
2. **生成时间戳**：`YYYYMMDD-HHMMSS`（秒级足够，单次 session 不会重名）
3. **拼出 SESSION_DIR**：`~/.gaokao/<nickname-slug>-<timestamp>`
4. **建目录**：`mkdir -p "$SESSION_DIR"`
5. **告知用户**一次完整路径，例：「过程文件会存到 `~/.gaokao/student-a-20260519-143012/`」

后续**所有**中间文件（`student.json`、`gaokao-adi-input.json`）和产出文件（Markdown、HTML 报告）一律写到 `$SESSION_DIR/`，**禁止**使用 `$CLAUDE_JOB_DIR` 或 `/tmp/`——前者只在当前 job 生命周期内存在，后者跨进程会被清。`~/.gaokao/` 持久、可检索、可按昵称排序。

### Step 0.5: 暖场 + 询问是否要"按成绩推荐"分支

**暖场必须使用以下 verbatim 模板**（用户实测过效果好的版本；不要简化、不要改字、不要换近义词、不要重排表格列）。先告知 SESSION_DIR 路径，然后贴这段：

```
好，本次 session 目录已建：

~/.gaokao/<填入 SESSION_DIR 真实路径>/

所有过程文件（问卷答案、输入、报告 .md/.html）都会落在这里，可永久保留。

---

先 30 秒讲清这个测评是干嘛的

ADI（Action Domain Index） 不是 MBTI、不是兴趣测试、不是"你适合学什么"。它评估的是一条专业路径"走得通"的程度 —— 4 个维度乘起来（1–625 分）：

| 维度 | 问的是 |
|---|---|
| paths 路径数量 | 学完这个专业，技能能跨多少行业 |
| reach 成功可达性 | 一个普通学生四年后能不能拿到体面结果（不是天才向） |
| correct 纠偏能力 | 发现不适合时，能不能换方向、要付多少代价 |
| recover 损失可控性 | 万一彻底失败，沉没成本是 4 年还是 10 年 |

分档：>300 低难、150–300 中等、50–150 较难、<50 高难。

> 一句 disclaimer：算法是从原作公开案例反推的工程版本，分档结论（低/中/较难/高难）可信，具体数字仅供横向比较，别把 312 vs 287 当成精确差距。
```

**固化理由**：「问的是」一栏把抽象维度名（paths/reach/correct/recover）翻译成 17 岁应届考生能立刻判断的具体问题；4 行内容缺一不可，删减任何一行都会让用户在后续问卷里失去维度直觉。"312 vs 287" 数字 anchor 比抽象 disclaimer 更能阻止用户把工程版当作精算结果。

**禁止删除项**：
- "不是 MBTI、不是兴趣测试、不是'你适合学什么'" 三连否定（先排除常见误解）
- 「问的是」一栏的口语化问句（不是技术定义）
- 「不是天才向」括号注（reach 维度的核心边界）
- 分档 4 个数值范围（用户后续看报告靠这套档位定位）
- 「别把 312 vs 287 当成精确差距」具体数字 anchor

然后**问一个分支决策**（用 AskUserQuestion 单题 2 选项）：

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
   - 传统模式（mode=traditional）请改报理综/文综 6 科原始分。理综 = 物理/110 + 化学/100 + 生物/90；文综 = 政治/100 + 历史/100 + 地理/100。允许写成 `物理=95/110` 显式带分母，或裸数字让我按理综分配自动归一。
3. 哪些科目你最**喜欢**？（1-3 个）— 标准：愿意主动花时间钻研、不靠逼自己也会去学的科目（不是"考得好"，是"愿意做"）。影响（v3.7 三档）：(a) fit bonus 按 key_subject 权重缩放（v2.0）——命中专业核心科目（如 CS 的数学 weight 0.4）才给可见 bonus；trivial 权重命中（0.05）几乎无加成。(b) L1 阈值放宽 20%（如化学阈值 60 → 喜欢化学只需 ≥ 48 通过）；不再是 v3.5 的 100% 免检，避免「喜欢化学但化学考 40」无脑放过。(c) L2 权重门槛放宽 20%（0.25 → 0.30，权重 0.25-0.29 的边缘 key_subject 不再触发相对短板检测）+ 学生归一额外 +0.05 buffer。综合：喜欢的科目可见放宽但仍受约束，不会颠覆排序，只在边缘把"喜欢但分数偏低"的方向救回来。
4. 哪些科目你最**不喜欢**？（1-3 个）— 标准要**严苛**：长期学起来真的痛苦/抗拒、未来 4 年大学不想再碰的（不是"偶尔难"或"分数低"）。影响：命中 key_subjects 权重 ≥0.25 的专业会被**软过滤直接剔除**，是**硬性筛选**。填得太随意会无意删掉本可走通的专业；不确定就少填或留空。
```

读 `references/provinces.json` 推断 mode（北京/上海/天津/浙江/山东/海南→3+3 系列；河北等 23 省→3+1+2；新疆/西藏→traditional）。
传统模式额外问"理科/文科"。

### Step 1a' （推荐分支）: 成绩归一化（写 student.json 前必做）

后端契约：`scores` dict 里 语数外 是 /150 原始分，其他科目（选考 / 理综 / 文综 分科）一律 /100。任何偏离此假设的输入必须在拼 JSON **前**就转好；后端不再做 mode-aware 缩放，错过这里就是数据污染。

**归一规则**（按这个顺序判断，命中即停）：

1. **显式 "X/Y" 输入**（任何 mode、任何科目）：`value * 100 / Y`，四舍五入到整数。例：`物理=95/110` → 86；`生物=78/90` → 87。用户自己声明的分母压过 mode 默认。
2. **mode=traditional + track=理 + 裸数字**：理综分科按教育部考试院标准分配换算——物理 ×100/110，化学 不动，生物 ×100/90。例：物理=95 → 86、生物=78 → 87、化学=82 → 82。
3. **mode=traditional + track=文 + 裸数字**：文综三科本就各 /100，不动。
4. **mode=3+3 或 3+1+2 + 裸数字**：选考已是 /100，不动；语数外保持 /150。

**写 JSON 前必须 echo 转换结果让用户确认**——只要触发了规则 1 或规则 2，向用户简短回报："你的 物理=95/110 我按 100 制算成 86，生物=78/90 算成 87，化学 82 不动。对吗？" 错的归一比不归一更糟（用户会以为系统理解了，其实在用错的分子比阈值）。

**反例**（这些都是 bug，不要写进 student.json）：
- 传统理 学生报 物理=95，你直接写 `"物理": 95` —— 错。应是 86。
- 用户写 `生物=78/90`，你写 `"生物": 78` —— 错。应是 87。
- 3+1+2 学生写 `物理=85`，你"贴心地"乘 100/110 改成 77 —— 错。3+1+2 首选物理本就 /100，不归一。

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

写到 `$SESSION_DIR/student.json`（Step 0 已建好目录），跑：
```bash
cd ~/.claude/skills/gaokao-adi && \
  python scripts/admission_recommender.py --input "$SESSION_DIR/student.json" --top 15
```

输出按分类（strong → consider → not_recommended → ineligible）+ score 降序。
把前 15 条贴回对话，**询问用户从中挑 3 个**（必须先在 strong/consider 桶里挑；如果用户想挑 not_recommended 也允许，但要提示原因）。

**记录每个被选专业的 fit/score**——后续作为 `_admission_scores` 字段传给 ADI。

### Step 1: 暖场说明（已并入 Step 0.5，本节通常跳过）

Step 0.5 的 verbatim 模板已覆盖暖场全部内容；用户在分支决策前已经看过 ADI 解释、4 维度、分档、disclaimer。

**直选分支（A）**：从 Step 0.5 直接跳到 Step 2'，不再复述。
**推荐分支（B）**：Step 1a/1b 已经穿插具体动作（问省份/答分数/挑专业），无需在跑完推荐后再次暖场——直接进入 Step 3 素质问卷。

仅当用户主动问"再讲一下 ADI 是什么"时，重新贴 Step 0.5 的 verbatim 模板，**不要**改写或精简。

### Step 2: 第 1 轮问卷 —— 已废弃，并入 Step 2'

早期版本用「大类菜单 + 多轮 AskUserQuestion」收集 3 个专业，现已统一走 Step 2' 的对话直报方式。被否决的菜单方案与演进取舍见 `references/theory.md`「十一、问卷收集方式的设计取舍」（这也是为什么有 Step 2' 而非 Step 2）。

### Step 2'（推荐）: 用对话收集 3 个专业 + 每个专业的资源

**verbatim 模板** — 照原文贴。两次实测漂移要避免：① 漏掉「父亲三甲医院主任→A / 叔叔 BAT→B」锚定例（用户失去 A/B/C 标尺）② 末尾加"列表外专业也可以触发现场推断"尾巴（污染当下聚焦）。

```
请告诉我：

1. 你的 3 个候选专业（按最想报考的顺序列 1/2/3）
2. 每个专业的资源等级（A 明显有: 直系亲属,直接资源 / B 有一些: 间接资源,人脉 / C 基本没有）
   - 资源 = 直系亲属在该行业、可直接对接的内推、知名企业实习机会、家族企业等
   - 例：「父亲是三甲医院主任医师 → 临床医学 A」「叔叔在 BAT 做算法 → 计算机 B」「都没有 → C」

可从下表 39 个专业里选（也可直接打字报表外的名字）：

| 大类 | 可选专业 |
|---|---|
| 理工基础学科 | 数学、物理、化学、生物科学、统计学、应用数学 |
| 计算机与电子 | 数据科学/人工智能、计算机类/软件工程、信息安全/网络工程、电子信息工程、自动化 |
| 传统工程 | 电气工程、机械工程、土木工程、环境工程 |
| 医学类 | 临床医学、口腔医学、药学、护理学 |
| 商科类 | 经济学、金融学、工商管理、市场营销、国际商务、物流管理、电子商务、供应链管理、会计学 |
| 文社科类 | 汉语言文学、英语/外语、新闻传播/传播学、教育学、心理学、社会工作、法学、哲学 |
| 应用与艺术 | 建筑学、艺术类（美术/音乐/表演）、设计类（视觉传达/产品设计等） |

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

**Q01 前置、Q18 后置是有意的测量学设计**（重复测量信度检验，理由详见 `references/theory.md`「十、Q01/Q18 前后置设计」——属设计原则，不在本文展开）。

> ⚠️ **出题时禁止向用户解释这个前后置安排**，也不要说「会和 Q18 做一致性交叉验证」「趁你没被带跑捕捉干净偏好」之类的话。一旦说破，用户被"启动"、刻意保持前后一致，恰好摧毁要捕捉的诚实矛盾信号，自我拆台。题目照 `question_bank.json` 原文问即可。

矛盾组合（AC/CA）触发 `appetite="contradiction"`，由本文末尾「Q1/Q18 矛盾交叉验证」节复核——把诊断价值还给用户。

### AskUserQuestion 调用规范（v3.0 起严格执行）

**单一真理源**：所有 question/option 文案 **verbatim** 来自 `references/question_bank.json`，**禁止**自创补充或改字。

**Schema 注意**：`question_bank.json` 顶层是 `{"_meta": ..., "major_categories": ..., "questions": [...]}`，其中 `questions` 是 **list**（每个元素带 `id` 字段如 `"Q01"`），**不是** dict。读取时**必须先建 id 索引**再按 id 取，否则 `qb["questions"]["Q01"]` 会抛 `TypeError: list indices must be integers`：

```python
import json
qb = json.load(open("references/question_bank.json", encoding="utf-8"))
qbank_by_id = {q["id"]: q for q in qb["questions"]}   # 索引建一次复用
q = qbank_by_id["Q01"]                                 # 然后正常按 id 取
```

**字段映射**（基于 `qbank_by_id`，Python dict 访问语法）：

| AskUserQuestion 参数 | 数据源 |
|---|---|
| `question` | `qbank_by_id[id]["title"] + "：" + qbank_by_id[id]["subtitle"]`（subtitle 缺失则只用 title）|
| `header` | `qbank_by_id[id]["title"]` 的前 12 字 |
| 每个 option 的 `label` | `qbank_by_id[id]["options"][i]["key"] + " " + qbank_by_id[id]["options"][i]["label"]`（如 "A 强"）|
| 每个 option 的 `description` | **多行**：`option["description"]`（含 `\n` 分隔的多个 bullet 子句）+ 换行 + `"（参考：" + option["notes"] + "）"` |

**description 多行渲染细节（v3.0 新增）**：

每个 option 的 `description` 字段现在是**多 bullet 串**（用 `\n` 分隔），原始问卷里每个选项是 2-3 个并列短句。AskUserQuestion 调用时**保留 `\n` 不变** ——AskUserQuestion 渲染器会把每行作为一个 bullet 展示，无需手动加 `•` 前缀。

最终 description 字段 = `option["description"] + "\n（参考：" + option["notes"] + "）"`

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

写到 `$SESSION_DIR/gaokao-adi-input.json`。

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
    --out-dir <SESSION_DIR>
```

工作目录要在 `~/.claude/skills/gaokao-adi/` 下跑，否则 Python 找不到 `scripts.*` 模块。建议：

```bash
cd ~/.claude/skills/gaokao-adi && \
  python -m scripts.run_assessment \
    --input "$SESSION_DIR/gaokao-adi-input.json" \
    --out-dir "$SESSION_DIR"
```

报告产出文件会落在 `$SESSION_DIR/gaokao-adi-report-<timestamp>.md` 和 `.html`，与 `student.json` / `gaokao-adi-input.json` 同目录，方便后续回看或归档。

### Step 8: 把 Markdown 报告贴回对话

stdout 是渲染好的 Markdown，直接贴。stderr 给出文件路径，告诉用户（**用真实 $SESSION_DIR 路径**，不要复制下面占位）：

```
报告已保存到本次 session 目录：
- 目录: ~/.gaokao/<nickname>-<timestamp>/
- Markdown: ~/.gaokao/<nickname>-<timestamp>/gaokao-adi-report-YYYYMMDD-HHMMSS.md
- HTML 单页: ~/.gaokao/<nickname>-<timestamp>/gaokao-adi-report-YYYYMMDD-HHMMSS.html
- 输入快照: ~/.gaokao/<nickname>-<timestamp>/gaokao-adi-input.json（可改答案后重跑）

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

报告自动在「概览」chip 上挂 ⚠️ 标记 + 走特殊文案，并在「综合建议」里加一句"自相矛盾，先想清楚到底要稳还是要冲"。`appetite_tie_break_weights` 在 contradiction 下显式 all-zeros（v1.8 起；v1.7 之前是 None），personal_fit 恒为 0，势均力敌的专业按 majors_input 稳定排序——语义"矛盾下不偏好任何维"写在矩阵 JSON 里，单一真理源。

**Claude 主持流程的额外动作**：
跑完脚本贴报告**之前**，如果 `risk_appetite == "contradiction"`，先向用户复核一句：

> "你 Q1 选了「{path_label}」、Q18 选了「{risk_label}」，这两个一个偏稳一个偏冲。要不要先确认哪个更接近你真实的状态？要改就告诉我重答；否则我按当前的矛盾标记继续生成报告，难度相近的专业就按原顺序排、不按个人偏好重排。"

如果用户重答 → 改 input.json 的 Q01/Q18 字段后重跑脚本（不必从头问 18 题）。如果坚持不改 → 报告里 appetite 仍是 "contradiction"，不影响 ADI 总分（appetite 已不进 ADI 乘法），只是势均力敌的专业少了 personal_fit 个性化重排。

**风险倾向不进 ADI 乘法**（不变量 #4，理由见 `references/theory.md`）——Q01/Q18 仅用于：(a) 势均力敌（同带）时按 personal_fit 重排，(b) 报告措辞，(c) 上面这条矛盾复核。

**Appetite ε-band 排名（旧称 tie-break）**：risk_appetite 在 ADI 总分势均力敌的专业间用 `personal_fit = Σ(w[dim]×adjusted[dim])` 子分重排，**不改显示的 total/难度档**。权重矩阵存 `weights.json::appetite_tie_break_weights`（key 名沿用旧称）——调权重改那里、不改代码。机制全貌、权重派生与算例属计算细节，见 `references/scoring_model.md`「十二、Appetite 排名机制」。

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
