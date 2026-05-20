# Scoring Model — 算法与权重设计

> ⚠️ **Disclaimer**：本算法是从风灵之声公开的 4 个标杆案例 + 考生 A/B 对照叙述中**反推**的工程实现。
> 与作者私有算法**可能存在量纲差异**——例如原文叙述「考生 A 法学 90 分」可能来自其私有计分方式，
> 本实现的目标是**保证分档结论一致**（考生 A 经济学/英语进低难、法学不进；考生 B 三项全高难），
> 而不是数字精确对齐。

## 零、最终分数构成（公式总览）

> 这一节是整个文档的入口：一图看清"最终 ADI 分数是怎么从输入算到输出的"。
> 后续 1-10 章是各组件的 deep-dive。

### 0.1 一句话总结

**最终 = (4 个维度乘积) × (招生匹配折扣)**，理论范围 `[0.5, 625]`（v3.15：floor 0.7→0.5），越高代表"这条专业路径你越走得通"。

### 0.2 两条独立主链

```
┌─────────────────── 主链 A：素质问卷 → ADI_raw ─────────────────┐
│                                                                │
│  Q01, Q08-Q18 选项                                             │
│         ↓                                                      │
│  trait_to_dim  ×  AI 极化(reach/paths)  ×  resource 等级(recover)│
│         ↓                                                      │
│  每维度: adjusted = clamp(base × multiplier, 1, 5)             │
│         ↓                                                      │
│  ADI_raw = ∏ adjusted_dim  ∈  [1, 625]                         │
│                                                                │
└────────────────────────────────────────────────────────────────┘

┌────────────── 主链 B：高考分数（可选）→ blend_factor ───────────┐
│                                                                │
│  各科原始分                                                    │
│         ↓                                                      │
│  norm_i = clip(原始_i / 满分_i, 0, 1)                          │
│         ↓                                                      │
│  FitScore = Σ(w_i × norm_i) / Σ(w_i)  +  favorite_bonus        │
│         ↓                                                      │
│  admission_score = min(1, FitScore × track_mult)               │
│         ↓                                                      │
│  blend_factor = 0.5 + 0.5 × admission_score  ∈  [0.5, 1.0]    │
│                                                                │
└────────────────────────────────────────────────────────────────┘

         ↓ 合流

最终 = ADI_raw × blend_factor          # 没接成绩时 blend_factor = 1
```

### 0.3 端到端公式（紧凑版）

```
最终 = clamp(base_p × mult_p, 1, 5)         # paths   路径广度
     × clamp(base_r × mult_r, 1, 5)         # reach   可达性
     × clamp(base_c × mult_c, 1, 5)         # correct 纠偏能力
     × clamp(base_v × mult_v, 1, 5)         # recover 兜底空间
     × (0.5 + 0.5 × admission_score)        # 招生折扣，无成绩时省略（v3.15 floor 0.5）
```

其中：

- `base_p`/`base_r`/`base_c`/`base_v` = 4 个维度的基础分（专业固有 1-5），来自 `baseline_adi.json[major].dimensions`
- `mult_p`/`mult_r`/`mult_c`/`mult_v` = 4 个维度的个人系数，由问卷答案 + AI 极化 + Resource 等级**连乘**得出（详见 §0.4）
- `admission_score = min(1, (FitScore + favorite_bonus) × track_mult)`
- `FitScore = Σ(w_i × norm_i) / Σ(w_i)`
  - `w_i` = 该专业 `key_subjects` 字典里第 i 科的权重（如 CS 的 `{数学: 0.4, 物理: 0.3, ...}`）
  - `norm_i = clamp(原始分_i / 满分_i, 0, 1)` —— 归一化分数
- `favorite_bonus = 0.05 × Σ(w_fav)`，对每个**既在 favorite_subjects 又在 key_subjects** 的科目，加 `0.05 × 它的权重`
- `track_mult = 0.85` 当 3+1+2 物理-轨学生申请无 STEM 核心科目的专业，否则 `1.0`
- 各科满分：语/数/外 = 150；理综/文综 = 300；其他选考（物/化/生/史/地/政）= 100
- `Σ` 是求和符号，`clamp` 含义见 §四
- ⚠️ blend 是**乘**在 ADI 乘积上的，对强专业的绝对影响远大于弱专业——详见 §0.7 末尾「blend 的话语权被 ADI 量级加权」

### 0.4 个人系数 multiplier 的所有组成

#### 0.4.0 先看一眼这些"查表名"是什么

下面表格的"因子形式"列里会出现一堆代码标识符——它们都是 `weights.json` 里的字段名，本质都是**"答案 → delta 数字"的查表**：

| 标识符 | 含义 | 实际数值在哪 |
|---|---|---|
| `trait_to_dim` | 素质题（Q08-Q11, Q13, Q14）"选项 → 某维度的 delta"总查表 | `weights.json.trait_to_dim[题号][选项][维度]` |
| `state_global` | Q15 状态趋势的全局 delta（同时作用于 4 个维度，量级最小） | `weights.json.state_global[选项]` |
| `resource_to_recover` | Q03/Q05/Q07 专业资源的**基础** delta（还要再乘 `specific_scale`） | `weights.json.resource_to_recover[选项]` |
| `global_resource_to_recover` | Q12 家庭支持的**基础** delta（还要再乘 `global_scale`） | `weights.json.global_resource_to_recover[选项]` |
| `specific_scale` / `global_scale` | 按专业敏感度对资源 delta 的"二次缩放" | 详见 §0.4.1 |
| `ai_impact_levels[ai][ability][dim]` | AI 极化 4×4 矩阵（专业 ai_impact × 学生 ability_index） | `weights.json.ai_impact_levels`，详见 §八 |

**统一公式形态**：每个因子都是 `1 + delta` 的形式——`delta > 0` 拉抬、`delta < 0` 拉低、`delta = 0` 不影响。所有 delta 连乘后得到该维度的 `mult_dim`，再乘上 `base_dim`，最后被 clamp 在 [1, 5]。

#### 0.4.1 完整因子表

每个 `mult_dim` 是以下因子的**连乘**（缺失的题视为 ×1）：

| 来源 | 影响维度 | 因子形式 | 典型幅度 |
|---|---|---|---|
| Q08 学习能力 | reach **(主)** + correct (溢) | `1 + trait_to_dim` 两维独立 | reach: A=+0.20 → D=-0.25 / correct: A=+0.10 → D=-0.10 |
| Q09 难度承受 | reach | `1 + trait_to_dim` | A=+0.20 → D=-0.25 |
| Q10 试错能力 | correct | `1 + trait_to_dim` | 同上 |
| Q11 调整能力 | correct | `1 + trait_to_dim` | 同上 |
| Q12 家庭支持 | 全 4 维（v3.16） | `1 + global_resource_to_recover × global_scale × dim_w` | 基础 A=+0.15 → D=-0.15，scale 见 §0.4.2，dim_w 见下 |
| Q13 长期投入 | reach | `1 + trait_to_dim` | A=+0.15 → D=-0.20 |
| Q14 拓展习惯 | paths **(主)** + reach (溢) + correct (溢) | `1 + trait_to_dim` 三维独立 | paths: A=+0.25 → D=-0.15 / reach: A=+0.10 → D=-0.10 / correct: A=+0.10 → D=-0.10 |
| Q15 状态趋势 | 全 4 维 | `1 + state_global` | A=+5% / B=0 / C=-5% |
| Q03/Q05/Q07 专业资源 | 全 4 维（v3.16） | `1 + resource_to_recover × specific_scale × dim_w` | 基础 A=+0.20 / B=+0.05 / C=0，scale 见 §0.4.2，dim_w 见下 |
| AI 极化 | reach + paths + correct（v3.16） | `1 + ai_impact_levels[ai][ability][维度]` | correct 仅 boost 域非零；见 §八 / §十一 4×4 矩阵 |

> **`dim_w` 资源维度权重（v3.16）**：资源 delta 作用于全部 4 维度，各维按 `weights.json::resource_dim_weights` 二次缩放——`recover 1.0 / reach 0.5 / paths 0.5 / correct 0.25`。语义:资源最帮"兜底"，其次"落地就业+拓宽行业"，对"换技能方向"帮助最小。recover=1.0 保持 v1.1 行为不变。

**不进算法的题**：Q01 路径偏好、Q16 学校/专业偏好、Q17 城市重要性、Q18 风险态度——**不进入 ADI 乘法链**，仅用于：(a) 同分时的 tie-break 排序（详见 §十一）；(b) 建议措辞与 Q01×Q18 矛盾交叉验证。

#### 0.4.2 `global_scale` / `specific_scale` 是什么

这是"**系数的系数**"——同一个 Q12 / Q03 / Q05 / Q07 答案，在不同专业里实际产生的 delta 不一样，因为每个专业对家庭资源的依赖程度不同。

**两步走的算式**：

```
最终 delta = 基础系数（看你的答案）× scale（看专业敏感度）
```

| 题 | 基础系数（你的答案） | × 哪个 scale |
|---|---|---|
| Q12 家庭支持 | `global_resource_to_recover[选项]`（A=+0.15 ~ D=-0.15） | `global_scale` |
| Q03/Q05/Q07 专业资源 | `resource_to_recover[选项]`（A=+0.20 / B=+0.05 / C=0） | `specific_scale` |

**scale 取值表**（来自 `weights.json.resource_sensitivity_levels`）：

| 专业敏感度 | `specific_scale` | `global_scale` | 代表专业 |
|---|---|---|---|
| **low** | 0.5 | 0.7 | CS、数学、统计学（硬技能驱动，家庭资源杠杆小） |
| **default** | 1.0 | 1.0 | 化学、机械、心理学、汉语言（标准化职业路径） |
| **high** | 1.5 | 1.3 | 金融、工商管理、口腔医学、法学（圈子/平台敏感） |
| **decisive** | 2.0 | 1.5 | 临床医学、艺术类（家庭资源近乎决定能否入门） |

**两个例子对比同一个"Q12 选 A（家里明显支持）"**：

- 学 CS（low）：delta = `0.15 × 0.7` = **+0.105**（"你家支持，但 CS 这种硬技能行业靠 LeetCode 和 GitHub，家里帮不上太多"）
- 学临床医学（decisive）：delta = `0.15 × 1.5` = **+0.225**（"医二代信息差近乎决定性——你家支持 = 巨大优势"）

**为什么这样设计**：`baseline_adi.json` 每个专业打了 `resource_sensitivity` 标签，`weights.json` 单独定义这 4 个等级对应的 specific/global 数值——调参时只改 `weights.json`，不动算法代码、也不用逐个专业改 delta，符合"配置与代码分离"。

详见 §十「Resource Sensitivity 等级分类」。

### 0.5 各步取值范围与"满分"条件

| 量 | 范围 | "满分"条件 |
|---|---|---|
| `base_dim` | 1 ~ 5 | 该专业在该维度天花板高（如 CS 的 paths=5） |
| `multiplier` | ~0.5 ~ ~1.5 | 个人素质对该维度全部友好 |
| `adjusted_dim` | 1 ~ 5（硬 clamp） | `base × multiplier ≥ 5` |
| **`ADI_raw`** | **1 ~ 625** | 4 个 `adjusted_dim` 同时 = 5 |
| `norm_i` | 0 ~ 1 | 该科卷面拿满分 |
| `FitScore` | 0 ~ 1 | 所有 `key_subjects` 全考过 **且** 全满分 |
| `favorite_bonus` | 0 ~ ~0.05 | favorite 覆盖核心高权重科目 |
| `track_mult` | 0.85 或 1.0 | 非"理-track 申无 STEM 专业" |
| `admission_score` | 0 ~ 1 | FitScore + bonus ≥ 1 触发 cap |
| `blend_factor` | 0.5 ~ 1.0 | admission_score = 1 |
| **最终** | **~0.5 ~ 625** | 两条主链同时打满 |

**关键不变量**：`adjusted_dim` 的硬 clamp 是设计核心——再强的个人素质也不能把"专业天花板"打穿，确保模型反映的是"在这个专业里走得通的概率"，而不是"这个人有多牛"。

### 0.6 一个完整数值反推

以你截图里的 332.31 为例（接成绩模式）：

**主链 A — ADI_raw**

```
adjusted_paths   = 4.84    base × multiplier 后落点
adjusted_reach   = 5.00    clamp 截顶
adjusted_correct = 3.31    ← 瓶颈维度，封住整体上限
adjusted_recover = 4.53

ADI_raw = 4.84 × 5.00 × 3.31 × 4.53 = 362.64
```

**主链 B — blend_factor**

```
假设 CS key_subjects = {数学: 0.4, 物理: 0.3, 外语: 0.2, 语文: 0.1}
若考生分数 数学 128 / 物理 75 / 外语 117 / 语文 100：
  norm = 128/150, 75/100, 117/150, 100/150
       = 0.853,   0.750,  0.780,   0.667
  FitScore = 0.4×0.853 + 0.3×0.750 + 0.2×0.780 + 0.1×0.667
           = 0.789
  admission_score ≈ 0.72  (具体取决于 favorite 与 track)
  blend_factor(v3.15) = 0.5 + 0.5 × 0.72 = 0.86      # 旧 floor: 0.7 + 0.3 × 0.72 = 0.92
```

**合流**

```
最终(v3.15) = 362.64 × 0.86 = 311.87       # 截图旧值 362.64 × 0.92 = 332.31
```

> ⚠️ 两点提醒：(1) 截图 332.31 来自 **v3.15 之前**（floor 0.7），新 floor 0.5 下同一 admission_score 给出 311.87——录取匹配不再"几乎不动分"。(2) v3.16 起资源作用于全 4 维、AI 作用于 correct，若该生有资源或选了 boost/threatened 专业，`ADI_raw` 本身（此处 362.64）也会变；本例仅作"四件套如何合流"的结构示意，不代表当前模型对该输入的精确输出。

### 0.7 提分边际收益（设计哲学的直接推论）

乘积模型对"最低维"极其敏感，对"已经高的维"几乎无感：

| 改动 | 数值 | 增益 |
|---|---|---|
| `correct` 从 3.31 → 4.31（攻短板） | 362 → 472 | **+30%** |
| `paths` 从 4.84 → 5.00（攻已高项） | 362 → 374 | +3% |
| `admission_score` 从 0.72 → 1.00（高考再上一档） | 312 → 363 | **+16%**（v3.15 floor 0.5 后翻倍） |

**结论**：想提升 ADI，先攻最低维度对应的问卷答案（通常是 Q10/Q11 试错与调整、Q14 拓展习惯），而不是优化已经强的维度，更不是死磕高考分——边际收益完全反过来。

> **blend 的话语权被 ADI 量级加权（保留乘积模型的代价，v3.14 文档化；数值按 v3.15 floor 0.5 更新）**
>
> blend 是**乘**在 ADI 乘积上的，而 ADI 跨越 1–625。所以最重的折扣 `blend = 0.5`（-50%，v3.15 floor）：
> - 砍 625 分专业 → 砍掉 **312.5 分**
> - 砍 50 分专业 → 只砍掉 **25 分**
>
> 即**录取匹配度对「本来就强的专业」绝对影响大得多，对弱专业几乎无所谓**。等价地，在对数空间 `log(final) = Σ log(dim) + log(blend)`，blend 的波动范围（`log(0.5) ≈ -0.693` 到 `0`）仍小于每个维度 `log(5) ≈ 1.6` 的跨度——v3.15 把它从 -0.357 拓到 -0.693，让高考分话语权接近翻倍，但乘积维度仍是主导。
>
> **保留乘积是有意选择**：让「天花板高的专业即使录取匹配一般，也能靠四维乘积顶上来」（追上限语义），而不是「你最匹配的专业自动排第一」（尊重适配语义，对应几何平均 `ADI^(1/4)`）。读者须知:blend **不是均匀折扣**，它的有效权重随专业 ADI 大小变化。

---

## 一、核心公式

```
对每个候选专业 m:
  对每个维度 d ∈ {paths, reach, correct, recover}:
    base = baseline[m][d]                          # 1-5，来自 baseline_adi.json
    multiplier = 1.0
    for trait in traits_that_affect(d):            # Q08-Q14 等
      multiplier *= 1 + weights.trait_to_dim[trait][user_answer][d]
    # 资源（v3.16：作用于全部 4 维，按 resource_dim_weights 分级 recover1.0/reach·paths0.5/correct0.25）
    dim_w = weights.resource_dim_weights[d]
    sens  = weights.resource_sensitivity_levels[baseline[m].resource_sensitivity]
    multiplier *= 1 + weights.resource_to_recover[user.resource_for[m]] * sens.specific * dim_w
    multiplier *= 1 + weights.global_resource_to_recover[user.Q12]      * sens.global   * dim_w
    # AI 极化（v3.16：reach/paths/correct；correct 仅 boost 域非零）
    if d in {reach, paths, correct}:
      multiplier *= 1 + weights.ai_impact_levels[baseline[m].ai_impact][ability_index][d]
    multiplier *= 1 + weights.state_global[user.Q15]        # 全局微调 Q15
    adjusted[d] = clamp(base * multiplier, 1, 5)
  adi_total[m] = adjusted.paths * adjusted.reach * adjusted.correct * adjusted.recover
  total[m]     = adi_total[m] * (0.5 + 0.5 * admission_score)   # v3.15：floor 0.5；无成绩则 ×1
  personal_fit[m] = Σ_d appetite_weights[d] * adjusted[d]        # v3.16：仅排序用，不进 total

# 排序（v3.16 ε-band：同 band 内按 personal_fit，跨 band 按 total）
algorithm_rank = sorted(majors, key=lambda m: (band(total[m]), personal_fit[m]), descending=True)
subjective_rank = [Q2, Q4, Q6]  # 用户输入顺序
agreement = kendall_tau(subjective_rank, algorithm_rank)
```

## 二、维度 → 题目映射

| 维度 | 主要由哪几道题修正 | 解释 |
|---|---|---|
| paths | **Q14 拓展习惯（主）** | 主动叠加技能 → 行业广度 |
| reach | **Q8 学习能力 + Q9 难度承受 + Q13 长期投入意愿（主）** + Q14 拓展习惯（溢） | 能不能扛住前期 + 学得动 + 愿意持续投入 + 多技能加成 |
| correct | **Q10 试错能力 + Q11 调整能力（主）** + Q8 学习能力（溢） + Q14 拓展习惯（溢） | 失败了能不能换路重来 + 学得快好转向 + 多技能多备份 |
| recover | Q12 家庭支持（全局）+ Q3/5/7 专业资源（专业级），**满额** | 走偏后兜底空间（资源杠杆最大处） |
| 资源外溢（溢） | reach + paths（半额）、correct（¼额），v3.16 | 资源也帮"落地就业/拓宽行业"，对"换技能方向"帮助最小 |
| 全局微调 | Q15 状态趋势 | 上升 +5%、下降 -5%，作用于所有 4 维度 |
| AI 杠杆（溢） | reach + paths + correct，经 ability_index（Q08–Q11）查 §十一 4×4 矩阵 | 与 trait 管道叠加；correct 仅 boost 域非零（v3.16）；详见 §十一「双管道」 |
| **不进算法** | Q1 路径偏好、Q16 学校 vs 专业、Q17 城市重要性、Q18 风险态度 | 仅影响排序展示与建议措辞 |

> **主维度 + 半幅度外溢（v3.12 起）**：v3.11 取消了 Q13 → paths（因为题面只问"抗延迟满足"，paths 是外推）。v3.12 反向加上 Q08 → correct 和 Q14 → reach/correct（因为题面**直接支撑**多维度——"学新知识快"自然帮换方向、"主动学英语/编程/证书"自然帮就业+换轨）。
>
> 区别标准：
> - **拒绝**（Q13 paths）：题面里**没出现**对应语义 → 不能挂
> - **接受**（Q08 correct, Q14 reach+correct）：题面里**明确出现**对应行为 → 可挂
>
> 幅度规则：**外溢维度 ≈ 主维度的一半，并保持圆整**。例如 Q08 reach +0.20 / -0.25，correct 外溢 +0.10 / -0.10。这样既不抹掉主维度的强信号，又把题面真实包含的外溢信号兑现到算法里。

## 三、选项 → 修正幅度（initial draft，可在 weights.json 调）

### Q9/Q10/Q11（能力类，单维度，标准 4 级）

| 选项 | 幅度 | 解读 |
|---|---|---|
| A 强 | +0.20 | 显著拉抬 |
| B 中上 | +0.05 | 轻微拉抬 |
| C 中下 | -0.10 | 轻微拉低 |
| D 弱 | -0.25 | 显著拉低 |

### Q8 学习能力（reach 主 + correct 溢）

| 选项 | reach（主） | correct（溢） |
|---|---|---|
| A 强 | +0.20 | +0.10 |
| B 中上 | +0.05 | +0.05 |
| C 中下 | -0.10 | -0.05 |
| D 弱 | -0.25 | -0.10 |

### Q13 长期投入意愿（reach）

| 选项 | 幅度 |
|---|---|
| A | +0.15 |
| B | +0.05 |
| C | -0.10 |
| D | -0.20 |

### Q12 家庭支持（recover 全局）

| 选项 | 幅度 |
|---|---|
| A 明显有 | +0.15 |
| B 一定支持 | +0.05 |
| C 基本支持 | -0.05 |
| D 几乎无 | -0.15 |

### Q14 拓展习惯（paths 主 + reach 溢 + correct 溢）

| 选项 | paths（主） | reach（溢） | correct（溢） |
|---|---|---|---|
| A 持续主动 | +0.25 | +0.10 | +0.10 |
| B 一定拓展 | +0.10 | +0.05 | +0.05 |
| C 偶尔 | -0.05 | -0.05 | -0.05 |
| D 不主动 | -0.15 | -0.10 | -0.10 |

### Q3/Q5/Q7 专业资源（recover 专业级）

| 选项 | 幅度 |
|---|---|
| A 明显有（直系/直接） | +0.20 |
| B 有一些（间接/人脉） | +0.05 |
| C 基本没有 | 0 |

### Q15 状态趋势（全局微调）

| 选项 | 幅度 |
|---|---|
| A 上升 | +0.05 |
| B 稳定 | 0 |
| C 下降 | -0.05 |

## 四、Clamp 边界

### 4.1 什么是 clamp

`clamp(x, min, max)` 是个"夹住"函数——把 x 强制限制在 `[min, max]` 区间里：

- 如果 `x < min` → 返回 `min`
- 如果 `x > max` → 返回 `max`
- 否则原样返回 `x`

举例：

| 输入 | 计算 | 结果 |
|---|---|---|
| `clamp(0.6, 1, 5)` | 0.6 < 1，截到下界 | **1** |
| `clamp(3.2, 1, 5)` | 在区间内，原样返回 | **3.2** |
| `clamp(7.5, 1, 5)` | 7.5 > 5，截到上界 | **5** |

ADI 模型里：

```
adjusted_dim = clamp(base × multiplier, 1, 5)
```

——任何维度调整后**永远在 [1, 5] 之间**，超出就被夹回边界。

### 4.2 为什么需要 clamp

| 设计目标 | 没有 clamp 会怎样 |
|---|---|
| 保持每维度 1-5 的物理意义（与原文 5 级量表对齐） | 数值可能跑到负数或 > 5，失去"等级"语义 |
| **防止个人素质暴涨抹平专业基础短板**（核心不变量） | 一个 base=1 的瓶颈维度乘上 1.5 倍 multiplier 后变 1.5，没事；但若不 clamp 上界，base=5 的维度乘上 1.5 倍变 7.5，会让"天才打满一切"，专业差异被洗掉 |

**核心不变量**：clamp 不是为了截掉极端值这么简单，而是为了保证"专业的天花板由 base 锁死"——再强的个人素质也不能把法学的 correct 维度（base=1）变得和 CS 的 paths 维度（base=5）一样宽。

### 4.3 案例验证：法学（极端例子）

法学的 baseline_adi 4 个 base 是：

```
paths   = 2     (法律行业以外几乎没法转)
reach   = 2     (司法考试 + 名校筛选，本科生准入门槛高)
correct = 1     (毕业后想换方向，沉没成本极大) ← 瓶颈
recover = 2     (失败后可走通的兜底路径少)
```

如果完全不调整（multiplier 全 = 1.0），ADI = `2 × 2 × 1 × 2 = 8`（极低，进高难档）。

**问题**：现在假设一个素质极强的学生（所有问卷题选 A），看 multiplier 能不能把法学救起来。

| 维度 | base | 影响此维的题 + 最优系数 | multiplier 上限（乘起来） | base × multiplier | clamp 后 |
|---|---|---|---|---|---|
| paths | 2 | Q14 +0.25, Q15 +0.05 | ~1.31 | 2 × 1.31 = 2.63 | **2.63** |
| reach | 2 | Q08 +0.20, Q09 +0.20, Q13 +0.15, Q14溢 +0.10, Q15 +0.05 | ~1.91 | 2 × 1.91 = 3.83 | **3.83** |
| correct | 1 | Q10 +0.20, Q11 +0.20, Q08溢 +0.10, Q14溢 +0.10, Q15 +0.05 | ~1.83 | 1 × 1.83 = 1.83 | **1.83** ← 仍是瓶颈 |
| recover | 2 | Q12 +0.15, Q03/Q05/Q07 +0.20, Q15 +0.05 | ~1.45 | 2 × 1.45 = 2.90 | **2.90** |

**最终**：`2.63 × 3.83 × 1.83 × 2.90 ≈ 53`，属较难档（v3.12 起 Q08/Q14 多维度溢出，比 v3.11 的 ≈40 高 33%）。

> **论证核心仍成立**：correct 维度（base=1）即使加上 Q08/Q14 的外溢仍是 4 个维度里最低的 1.83，乘积模型把"跨到低难（≥200）"封死——纸面极限 53 < 200，仍在较难/高难区间。v3.11 锁高难（<50），v3.12 因外溢溢出到较难（<100）；这是新的真实上限，不是 bug，而是模型对"主动拓展技能 = 部分救场"的合理回应。
>
> **v3.16 更新**：本表假设资源只作用 recover（旧模型）。资源扩到全 4 维后，全 A 素质 + **资源全 A + Q12 A** 的法学纸面极限升到 **≈87**（见 `test_personality_cannot_erase_baseline_lock`），仍是较难、远低于中等（150）——baseline lock 不变，只是"家里有法律资源的全才"被合理抬高。correct 仍是瓶颈维。

### 4.4 这个例子要说什么

- **base 是天花板**，individual 系数只是把分数沿着 base 的尺子上下挪
- **乘积模型对最低维极敏感**——correct 这个 base=1 的瓶颈，无论其他维度怎么努力都救不回来
- **clamp 上界 5** 在这个案例里没生效（最高的 reach 才 3.48），但它在其他场景生效——比如 CS 的 paths 维度 base=5，再乘个 1.44 的 multiplier 会变 7.2，被 clamp 截回 5，确保"专业 paths 满格"不会被个人素质再放大

> 简言之：clamp 是 ADI 模型的"物理定律"——保证分数永远反映"在这个专业里走得通的概率"，而不是"这个人有多牛"。

## 五、案例 A/B 期望区间

| 考生 | 专业 | 期望分档 | 期望总分区间 | 来源 |
|---|---|---|---|---|
| A | 经济学 | 中等 ~ 低难 | 200+ | 原文「明显抬起来」 |
| A | 英语/外语 | 中等 ~ 低难 | 200+ | 原文「英语被显著抬高」 |
| A | 法学 | 较难 ~ 高难 | < 100 | 原文「90 分，高难路径」；本实现 clamp 后可能在 50-100 |
| B | 法学 | 高难 | < 50 | 原文「掉进高难路径」 |
| B | 英语/外语 | 较难 ~ 高难 | < 100 | 原文「从可展开变成高难度」 |
| B | 汉语言文学 | 较难 | < 150 | 原文「不至于锁死，但拉不开差距」 |

**这些区间是单元测试的保险丝**。调权重时一旦测试失败，要么是调错了方向，要么是要更新断言区间并在本文档记录原因。

## 六、Kendall τ（3 元序列硬编码）

对长度=3 的序列，Kendall τ 只有 7 种可能值。我们硬编码：

| τ 值 | 含义 | 信号 |
|---|---|---|
| 1.0 | 完全一致 | "你的偏好与可走通性一致" |
| 0.33 | 1 对错位 | "大体一致，但有 1 处反差" |
| 0 | 无相关 | "偏好与可走通性无明显关联" |
| -0.33 | 2 对错位 | "明显反差，建议重新审视" |
| -1.0 | 完全相反 | "⚠️ 你最想的恰恰最难走通" |

计算公式：`τ = (concordant - discordant) / C(n, 2)`，n=3 时 C(3,2) = 3。

## 七、"其他"专业现场推断指引

当用户选「其他」时，Claude 在对话里推 4 维度，使用以下指引：

**paths**：毕业后能进入几个**根本不同的行业**？（不只是岗位）
- 5：≥4 个独立行业（如 CS：互联网/制造业/金融/政企）
- 3：2-3 个行业（如统计学：金融/数据分析/教育）
- 1：1 个行业且无替代（如临床医学）

**reach**：在中国 2026 年，**普通本科**学生四年后能否拿到"体面"工作？
- 5：99% 都能（如 CS/会计）
- 3：50% 左右，分化大（如经济学/英语）
- 1：极少数能（如纯学术/艺术）

**correct**：发现不适合时，**不重读**能否换方向？（自学/辅修/考研跨专业）
- 5：1 年自学即可换轨（如 CS↔数据分析）
- 3：需要 2-3 年系统学习（如法学→金融）
- 1：必须重新读书（如临床医学）

**recover**：失败后**沉没成本**有多大？
- 5：与同龄人差不多（本科一般专业）
- 3：损失 2-3 年（投入高的专业）
- 1：损失 5+ 年 + 资格门槛（医学/法律执业）

**核心校验问题**：你的推断在 baseline_adi.json 的 39 个专业中，最接近哪个？是否在合理位置上？

## 八、调参 SOP

1. 改 `weights.json` 任何一项
2. 跑 `pytest tests/`
3. 如果案例 A/B 测试红了：
   - 大概率是调错了——回滚或反方向调小一些
   - 如果确信新值更合理：更新断言阈值，**在本文末「调参日志」追加一条**记录改动日期、改动项、合理性论据
4. 如果 weights coverage 测试红了：补全漏映射的选项

## 九、调参日志

| 日期 | 改动 | 原因 |
|---|---|---|
| 2026-05-18 | 初始版本 | 基于案例 A/B 反推 |
| 2026-05-18 | 经济学 paths 3→4；金融学 correct 4→3；两者 rationale 扩写 | 与 `wiki/archive/202605/风灵-经济学专业为什么总让人又爱又恨.md` 精确对照。作者亲自指认「经济学路径宽度取值较高」（→ paths 4），并把金融定义为「高筛选职业路径」+ 三重平台敏感（→ correct 4 与「赢家通吃」存在内部张力，下调到 3 更贴）。验证：案例 A/B 全部 21 测试仍绿；案例 A 经济学 430→576（仍低难）。 |
| 2026-05-18 | 算法 v1.1：加入 per-major resource_sensitivity（low/default/high/decisive 4 等级）| 现实差异：金融/医学的家庭资源杠杆远大于 CS。改动：(1) baseline_adi.json 每个专业加 resource_sensitivity 字段；(2) weights.json 把 Q12 从 trait_to_dim 移到 global_resource_to_recover，加 resource_sensitivity_levels（specific/global 二元倍数）；(3) score_engine 在 recover 维度按等级缩放 Q3/Q5/Q7 (specific) 和 Q12 (global)。等级数值可在 weights.json 配置，无需改代码。验证：25/25 测试绿；案例 A 法学略升（27.54→28），仍高难；新增 4 个 sensitivity 单测。 |
| 2026-05-18 | 算法 v1.2：加入 AI 极化效应（ai_impact × ability_index 4×4 矩阵）| 用户洞察："CS 上限提高、下限降低"——简单的 boost 标签是均值幻觉。改动：(1) baseline_adi.json 每个专业加 ai_impact (boost/neutral/disrupted/threatened)；(2) weights.json 加 ai_impact_levels 4×4 嵌套（4 个等级 × 4 个能力档），以及 ability_index_thresholds；(3) score_engine 加 ability_index() 计算（Q8-Q11 加权平均）和 _resolve_ai_impact() 查表，在 reach 和 paths 维度施加 additive delta。能力越强 boost 受益越大、threatened 受影响越小；能力弱在 boost 类专业反而被替代。验证：31/31 测试绿；案例 A 经济学不变（已 clamp），法学/英语略降但仍高难/低难分类不变。新增 6 个极化单测，含核心保险丝 test_cs_low_ability_falls_below_finance_high_ability。 |
| 2026-05-18 | 算法 v1.3：rationale 字段从 string 升级为 dict 三层 (baseline / resource / ai_impact)；render 层和单测同步升级 | 用户洞察：每次重写 baseline_adi.json 都会丢一些 rationale 论据（v1.1 资源敏感度依据 → v1.2 重写时丢失）。改动：(1) baseline_adi.json 每个专业 rationale 升级为 object，三层都必填；(2) render_markdown / render_html 用 📝 / 💼 / 🤖 三个图标分行展示三层；(3) 修复 v1.2 引入的 render bug——top_lift/top_drag 处理 ai_impact contributor 时硬访问 'answer' 字段崩溃，改用 _format_contributor / _contributor_li helper 区分不同 source 类型；(4) 加严格 lint 单测 test_rationale_three_layers_required（39 专业 + _user_additions 任何条目缺一层都报错）+ test_rationale_passthrough_to_result。验证：33/33 测试绿；案例 A 经济学 card 正确展示三层 rationale + AI 影响 contributor。 |
| 2026-05-18 | 算法 v1.4：基于成绩的专业推荐分支 + risk_appetite 集成（Q1+Q18 入算法）+ admission_score 作为 ADI 乘数 | 用户洞察："skill 开始前应询问是否需要根据成绩推荐专业"。新增：(1) `provinces.json`（31 省 → 高考模式 + 科目集）；(2) `majors_admission_2024.json`（39 专业的 required_primary / required_electives_all / traditional_track / key_subjects / soft_thresholds / tags / confidence）；(3) `admission_recommender.py`：is_eligible 硬过滤（3+1+2 首选 + 3+3 再选 + 传统文理） + fit_score（key_subjects 加权平均）+ soft_filter（劣势学科 + 不喜欢冲突）+ recommend 分桶（strong/consider/not_recommended/ineligible）；(4) weights.json 加 `risk_appetite_levels`（5 级）+ `q1_q18_to_appetite` 矩阵（含矛盾组合 AC/CA→neutral）+ `admission_blend` (min_factor 0.7 + range 0.3)；(5) compute_dimension 接 risk_appetite 参数，对 recover/paths 维度应用 `delta = coef × (base-3)/2`；(6) compute_major 接 admission_score 参数，应用 `final = ADI × (0.7 + 0.3 × score)`；输出多 4 个字段：risk_appetite, admission_score, admission_blend_factor, adi_total。验证：53/53 测试绿（18 个新 admission 单测）；广东物化生端到端：CS adm=0.874 → blend 0.962 → final 601；经济 adm=0.866 → blend 0.960 → final 401；strong_averse 用户法学 17.36→16.90、经济 418→428。 |
| 2026-05-18 | 算法 v1.5：**回退 v1.4 的 risk_appetite ADI 乘法**，Q1+Q18 改为仅影响排序 tie-break + 报告措辞 + 矛盾交叉验证 | 用户洞察：v1.4 把主观风险偏好乘进 ADI 总分，违反了 ADI 的"客观可走通"语义——主观"想要稳"和客观"能走通"被搅到一起，且 paths 方向有语义争议（paths=机会广度 vs paths=备选安全网，求稳者两种解读相反）。改动：(1) `compute_dimension` 删除 `risk_appetite` 参数与 recover/paths 的 (base-3)/2 修正块；(2) `compute_major` 不再传 appetite 给 compute_dimension；(3) `compute_all` 新增 `_appetite_sort_key` —— 同分时 averse→recover desc / seeking→paths desc 做 tie-break；(4) meta 新增 `appetite_contradiction`（AC/CA → true）+ `appetite_tie_break_dim`；(5) weights.json 删 `risk_appetite_levels` 系数表（无消费者），保留 `q1_q18_to_appetite` 仅作派生；(6) render_markdown / render_html 增加 APPETITE_LABELS chip + `_appetite_advice` 解读语；(7) SKILL.md 加「Q1/Q18 矛盾交叉验证」流程，跑脚本前 Claude 主持复核。验证：55/55 测试绿——`test_risk_appetite_does_not_affect_adi_total` 断言 averse 与 neutral 用户对 CS/法学/数学 total 完全相同；新增 `test_appetite_tie_break_sorts_by_recover_for_averse` + `test_appetite_contradiction_flagged`。|
| 2026-05-18 | 算法 v1.6：tie-break 从单维度升级为 4 维加权和（A/B/C profile 三角，5 级线性插值） | 用户洞察：单维度 tie-break 丢失精度——BB 真权衡型完全没 tie-break，AA 漏掉 reach 信号，CC 漏掉 correct 信号；且 B 的依赖应是 paths+correct+recover 三维，不是 paths 单维度。改动：(1) `weights.json` 新增 `appetite_tie_break_weights` 矩阵（5 级 × 4 维），删除旧的 `_APPETITE_TIE_BREAK_DIM` 单维度表；(2) `score_engine.py` 删除 `_APPETITE_TIE_BREAK_DIM`，新增 `_resolve_tie_break_weights` + `_compute_tie_break_score`，`_appetite_sort_key` 改用加权和；(3) `compute_all` 把 meta 的 `appetite_tie_break_dim` 改为 `appetite_tie_break_weights`（暴露完整权重 dict 供 debug）；(4) `render_markdown` 用 `_APPETITE_TIE_BREAK_NARRATIVE` 把 5 级权重映射成自然语言（"成功可达性+损失可控性"等）；(5) `SKILL.md` 加权重矩阵说明 + 调参指引。语义：A=reach+recover, B=paths+recover+correct, C=paths+correct，5 级 = strong_averse(1.0A) / averse(0.7A+0.3B) / neutral(1.0B) / seeking(0.3B+0.7C) / strong_seeking(1.0C)，每行权重和=1.0。验证：60/60 测试绿——新增 6 个 tie-break 测试覆盖 weights 加载、AA→A_PURE、CC→C_PURE、BB→B_MIXED 排在 A_PURE 之前、矛盾跳过、totals 等同性 fixture 检查。注意：linear 加权和在 BB 下 C_PURE 仍可能 > B_MIXED（极值优势）——文档化为固有取舍，不强求 B 击败 C。|
| 2026-05-18 | 算法 v1.7：AC/CA 从 'neutral' 重命名为 'contradiction' 独立 label | 用户洞察：v1.6 之前 BB（真权衡）与 AC/CA（矛盾）都映射到 `risk_appetite="neutral"`，下游消费者（admission_recommender、报告 chip、未来的下游 API）拿到字符串无法分辨真权衡和矛盾，违反单一真理源。改动：(1) `weights.json::q1_q18_to_appetite` 把 AC/CA 改成 `"contradiction"`；(2) `score_engine.py` 删除 `_is_appetite_contradiction` 辅助函数（contradiction 信号现在唯一从 appetite 字符串本身派生），`_resolve_tie_break_weights` 不再接 contradiction 参数；(3) `compute_all` meta.`appetite_contradiction` 由 `appetite == "contradiction"` 计算得到（保留 bool 字段以向后兼容下游消费者）；(4) `render_markdown.APPETITE_LABELS` + `render_html._APPETITE_LABELS` 加 `"contradiction": "信号矛盾"`；(5) chip 文案重写："⚠️ 信号矛盾（Q1 与 Q18 方向相反）"；(6) `SKILL.md` 矛盾交叉验证小节描述更新。验证：60/60 测试绿——`test_risk_appetite_contradiction_returns_contradiction_level` 替换 `_falls_to_neutral`，`test_appetite_contradiction_flagged_simple_case` 断言 `risk_appetite == "contradiction"`。|
| 2026-05-18 | 算法 v1.8：appetite_tie_break_weights 加 contradiction 行（all-zeros），矩阵自解释 | 用户洞察：v1.7 之后 contradiction 缺席矩阵→走 `_resolve_tie_break_weights` 的 None 分支跳过 tie-break，但这把"矛盾不查表"的逻辑藏在代码里，违反 v1.7 同动机的单一真理源洁癖。改动：(1) `weights.json::appetite_tie_break_weights` 加 `"contradiction": {paths:0, reach:0, correct:0, recover:0}` 行，`_doc` 注明语义；(2) `meta.appetite_tie_break_weights` 从 None 变为 all-zeros dict（行为等价：加权和恒为 0 → stable sort 保留输入序）；(3) None 分支保留作为防御 unknown level 的兜底；(4) `_appetite_advice` 已有 `if appetite_contradiction: return out` 早退出，不受 all-zeros 字典 truthy 影响；(5) `SKILL.md` 描述同步。验证：60/60 测试绿——`test_contradiction_uses_all_zeros_weights_for_stable_order` 替换 `_skips_tie_break_stable_order`，断言 weights == all-zeros 而非 None。|
| 2026-05-18 | 算法 v1.9：soft_filter 升级为双层（绝对线 + 相对偏科）+ favorite_subjects 给 fit bonus（修隐性 bug）+ derive_risk_appetite docstring 更新 | 用户洞察：(a) admission_recommender 的 soft_thresholds 全是绝对原始分，低分省/弱学生 L1 全部失效后失去筛选信号——需要"用最差的成绩（归一化后）"做相对偏科识别。(b) favorite_subjects 从 v1.4 起在 SKILL.md Step 1a 被收集、在 StudentProfile 接收，但代码里完全没用——隐性 bug。(c) admission_recommender.derive_risk_appetite docstring 描述 AC/CA→neutral 的 v1.6 之前行为，未跟进 v1.7 contradiction label。改动：(1) `weights.json` 加 `soft_filter_relative` 配置块（weights_threshold=0.25, relative_gap=0.15, favorite_fit_bonus=0.05）；(2) `admission_recommender.soft_filter` 在 L1 绝对线之后加 L2 相对偏科——基于学生自身归一化均值减 gap，对 key_subjects 权重 ≥ weights_threshold 的科目检查是否低于偏科线；(3) `admission_recommender.fit_score` 加 favorite_subjects 命中 key_subjects 的 +0.05 bonus（cap 1.0）；(4) `admission_recommender` 通过 `from scripts.score_engine import load_weights` 复用 weights 加载（保持单一真理源）；(5) `derive_risk_appetite` docstring 改成 v1.7+ 行为（6 个可能输出含 contradiction）；(6) `SKILL.md` Step 1a 注明 favorite/disliked 的实际算法用途。验证：66/66 测试绿——新增 6 个 admission 测试：L2 偏科识别（数学权重 0.3 + 学生 92/150 触发）、L2 不误伤均衡学生、L2 不查低权重科目、favorite_subject 加 +0.05、多 favorite 累加且 cap 1.0、favorite 不在 key_subjects 不加 bonus。|
| 2026-05-18 | 算法 v2.0：skill 重命名 + alignment lint + extras 推荐 + 丰富 HTML 卡片 | 用户洞察：(a) skill 叫 major-adi-zh 太泛、-zh 后缀冗余，gaokao-adi 更精确；(b) baseline / admission 两个 39-key 文件无 cross-check，漂移风险高；(c) HTML 报告每张专业卡只有 4 维度表 + 一行 top lift/drag，没把用户问卷答案/成绩具体怎么贡献到分数表达出来；(d) 算完 3 个用户选定专业后没有"额外推荐"，错失把 admission_recommender 的全 39 专业筛选结果暴露给用户的机会。改动：(1) `mv major-adi-zh gaokao-adi`，SKILL.md frontmatter + cd 路径 + run_assessment 文件名 prefix 全部同步；(2) 新增 `test_admission_baseline_keys_aligned` 测试断言两文件 majors 集合一致；(3) `score_engine.compute_extras(input_data, excluded, baseline, weights, n=3)`——优先从 `_admission_pool` 取 strong/consider 桶（无 warning），fallback 取 baseline 全 39（warning="未做选科合规校验"）；按 ADI × admission_blend 排序取 top n。compute_all 自动调用，结果加 `extras` + `extras_warning` + `student_context`（透传 `_student_profile`）字段；(4) `render_html._major_cards_html` 重写——每张卡新增 Phase A `_phase_a_questionnaire_impact`（top 3 lift + top 2 drag contributors 追溯）、Phase B `_phase_b_subject_match`（key_subjects × 学生归一化分数 + soft_thresholds + favorites 状态表，仅 student_context 存在时渲染）、Phase C `_phase_c_summary`（rank + top_lift + bottleneck + admission_score 分桶措辞拼装）；(5) `render_html._extras_section_html`——每张推荐卡 3 段：`_extra_reason_template`（ADI 差异% + admission_score 等级）、`_extra_compare_template`（与最强候选最显著 1-2 维度对比）、`_extra_when_priority_template`（按差异最大维度映射"如果你看重 X"语义）；(6) `report_template.html` 新增 `{{extras_section}}` placeholder + .impact-block/.match-block/.summary-block/.extra-card CSS；标题统一为 Gaokao-ADI；(7) SKILL.md Step 6 加 `_admission_pool` + `_student_profile` 字段说明。所有 narrative 100% 模板（不调 LLM），保 skill 离线确定性。验证：73/73 测试绿（含 6 个新 v2.0 测试：extras 数量/排除选定/排序/admission_pool 优先/fallback warning/student_context 透传）+ 1 alignment lint。端到端实测：full_input.json（3 选定 + admission_pool 9 项 + student_profile）跑出 17.7K HTML，含 Phase A/B/C 全部 subsection 标题与 extras 章节。|
| 2026-05-18 | 算法 v2.1：admission 数据质量修正 + _user_additions 工作流补全 | 用户洞察：(a) 信息安全/CS/电子信息工程等工科专业 `soft_thresholds` 含化学但 key_subjects 化学权重 0.10-0.20，把"化学差"误判为不适合 CS；(b) 数学/应数/统计学等数学密集专业 soft_threshold 数学=100 看似满分实为 67%（数学满分 150），对纯数学方向过于宽松；(c) 「其他专业」现场推断只写 `baseline_adi._user_additions` 不写 `majors_admission_2024._user_additions`，导致新加专业既无法被 admission_recommender 推荐、也不能进 extras fallback 池；alignment lint 也只查主表不查 _user_additions，漂移悄悄发生。改动：(1) `majors_admission_2024.json` 删 6 个工科专业（软件/信息安全/电子信息/自动化/电气/机械）的 soft_thresholds 化学；CS/信息安全 key_subjects 化学权重 0.20 → 0.10、把空出的 0.10 加到数学(+0.05)+物理(+0.05)，sum 仍 = 1.0；(2) 提升 8 个数学密集专业 数学 threshold：数学 100 → 130（行业 87%）、应数 100→125、统计 100→120、数据科学 100→115、金融 100→115、CS/信息安全 95→110、经济学 95→105；(3) `weights.json` 加 `_subject_max_scales` 文档块（语数外/150，物化生政史地/100，理综文综/300），让阅读者一眼看懂 threshold 含义；(4) `admission_recommender` 加 `_resolve_admission_majors` helper 合并 majors + _user_additions，`recommend()` 改用合并字典；`score_engine.compute_extras` fallback 池同步合并 baseline.majors + baseline._user_additions；(5) `test_admission_baseline_keys_aligned` 扩展同时校验 _user_additions 同步（baseline 与 admission 必须等集，不只是子集）；(6) 既有 baseline._user_additions 中的「微电子」backfill 到 admission._user_additions（required_primary=物理、化学权重 0.20 因半导体材料相关、3 个 soft_thresholds 物理 85 / 数学 110 / 化学 70）；(7) `SKILL.md` 「其他专业现场推断」步骤 1 扩展加 admission 字段推断指引（7 个字段：required_primary/electives/track/key_subjects/soft_thresholds/tags/confidence），步骤 3 改为同时写两个文件的 _user_additions（强调 lint 会失败）。验证：75/75 测试绿，新增 2 个 v2.1 测试：`test_recommend_includes_user_additions`（断言微电子能被 recommender 返回）、`test_compute_extras_fallback_pool_includes_user_additions`（断言 baseline 全池含 _user_additions）；alignment lint 现在同时校验 39 主表 + _user_additions 同步。|
| 2026-05-18 | 算法 v2.2：8 个文社科/艺术/心理专业按「实际本科课程 + 工作需要」原则纠偏 key_subjects + soft_thresholds | 用户洞察：当前数据有政治通病——所有文科专业都默认给政治 0.15-0.25 权重，把"高考思政课"和"专业课依赖政治"混为一谈；设计类挂数学权重 0.15 完全脱离艺术生现实；心理学数学权重(0.20)高于语文(0.15)违反实际本科课程（统计与测量重要但论文阅读/实证写作更核心）。改动（pure JSON 数据，零代码改动）：(1) 哲学 key 政治 0.25→0.10、语文 0.35→0.40、外语 0.10→0.20、历史 0.15→0.20——本科课程以中西哲学史/逻辑/伦理为主，需大量原典阅读+论证写作；(2) 法学 key 政治 0.20→0.10、语文 0.30→0.35、外语 0.15→0.20——本科课程重法条阅读+涉外法律；(3) 设计类 key 数学 0.15→0.05、物理 0.15→0.10、语文 0.20→0.25、外语 0.20→0.25、历史 0.10→0.15；soft_thresholds 删数学 85——艺术生数学普遍不强不该卡硬线；(4) 心理学 key 数学 0.20→0.15、语文 0.15→0.25、生物 0.20→0.25、政治 0.10→0.05、物理 0.10→0.05；soft_thresholds 数学 85→80（统计课刚需但允许中等）+ 新增语文 95（论文阅读+实证写作）；(5) 新闻传播 key 政治 0.15→0.10、语文 0.35→0.40；(6) 教育学 key 政治 0.15→0.10、语文 0.25→0.30；(7) 社会工作 key 政治 0.20→0.10、语文 0.25→0.30；(8) 艺术类 key 政治 0.15→0.05、语文 0.25→0.30、历史 0.15→0.20、外语 0.20、新增物理 0.05；(9) soft_thresholds 政治批量删 5 处：新闻传播 70/教育学 65/社会工作 65/法学 75/哲学 75。所有 key_subjects sum 严格 = 1.0。验证：75/75 测试绿；端到端实测文科考生跑哲学/法学/心理学分类正确，工科考生不再被错误的政治权重误判。|
| 2026-05-19 | 算法 v2.3：修 fit_score 与 soft_filter L2 的"missing key_subject 静默跳过"bug + favorite bonus 改按 key_subject 权重缩放 | 用户洞察：陕西物理类考生（语 130/数 140/外 130/物 70/化 70/生 64，喜欢数学/语文/物理/生物）跑推荐，前 5 名居然是艺术类(fit=1.00)/设计类(0.99)/教育学(0.984)/社会工作(0.984)/汉语言文学(0.976) 全为人文社科。根因有三：(a) `fit_score` 循环里 `if raw is None: continue` 把缺失科目从分子分母都剔除，剩余权重自动 re-normalize——教育学 key_subjects 含历史 0.20+政治 0.05 占 25%权重直接消失，分数从应有 ~0.65 抬到 0.88；(b) favorite bonus 是 flat +0.05 per match 不按权重，4 个最爱命中艺术类 trivial 权重也能强加 +0.15；(c) `soft_filter` Layer 2 同样的 `if student_norm is None: continue`，让"未考核心科目"的方向不匹配被静默放行。改动（admission_recommender.py，53 行级别）：(1) `fit_score`——把 `weight_sum += w` 移到 None 检查之前，missing 科目仍计入分母但分子为 0；favorite bonus 改为 `cfg["favorite_fit_bonus"] * sum(key_subjects[fav] for fav in favorites if fav in key_subjects)`——按命中 key_subject 权重缩放，CS 命中数学(0.4) 给 0.02 而非 0.05，艺术类命中数学(0.1) 只给 0.005；(2) `soft_filter` Layer 2——`if student_norm is None: return False, f"你未考{subj}（本专业核心权重 {weight:.2f}），方向不匹配"`，weight ≥ weights_threshold(0.25) 的核心科目缺失即拒；(3) docstring 升级为 v2.0 注释（fit_score 解释新 missing 处理 + 权重缩放 bonus；soft_filter 解释 Layer 2 missing-reject）；(4) SKILL.md Step 1a 第 3 项 favorite 描述同步——说明权重缩放、trivial 命中几乎无加成；(5) test_admission.py::`test_fit_score_favorite_subject_bonus` 期望值改为 `0.05 * cs["key_subjects"]["数学"]`。端到端验证（陕西物理类 student）：艺术类 1.00→0.673 + soft-reject(未考历史)、汉语言文学 0.976→0.687 + soft-reject(未考历史)、设计类 0.99→0.697(consider)、教育学 0.984→0.691(consider)、社会工作 0.984→0.691(consider)；CS 现稳定 0.886(strong)，理科生方向被还原。验证：76/76 测试绿，未引入新测试（修复用现有 fixtures 覆盖）。|
| 2026-05-19 | 算法 v2.4：新增 track-mismatch 跨轨惩罚——理科生跑纯文商专业要从 strong 桶降到 consider | 用户洞察：v2.3 修了"缺失科目静默跳过"的结构性 bug 之后，跨轨软商科仍稳坐 strong 桶——陕西物理类考生（语数外极强、物化生中下）跑商科前 9 全在 0.78-0.88：经济/金融/会计（数学权重 0.35-0.40，理科生友好）和 物流/供应链/工商/电子商务/市场营销/国际商务（数学权重 0.20-0.35，软硬不一）全部 strong。用户预期：经/金/会保留、市场营销剔除、其他待定。**这不是 bug 是 values 决策**——fit 算法给的分数没错（学生语数外确实强），但缺一层 track-awareness：3+1+2 物理首选 = 官方意义的 STEM 轨，主权重在语文/外语的纯文商专业属跨轨，可走通但不该和 STEM-aligned 商科同桶。改动：(1) `_DEFAULT_SOFT_FILTER_RELATIVE` 加 `track_mismatch_penalty=0.85` + `track_mismatch_stem_threshold=0.30` 两个配置项，`_resolve_soft_filter_relative` 透传；(2) 新增 `_STEM_SUBJECTS={数学,物理,化学,生物}` 常量、`_infer_student_track(student)` helper（3+1+2 物理→理、历史→文；traditional 直接读 student.track；3+3 自由选→空字符串）、`_track_mismatch_multiplier(student, admission, cfg)` helper（理-track 学生 + 专业 max STEM weight < threshold → 返回 penalty，否则 1.0；非理-track 一律 1.0）；(3) `fit_score` 末尾改为 `(base + bonus) * track_mult`，仍受 1.0 cap；(4) docstring 加 v2.4 段说明；(5) 新增 3 个测试：`test_fit_score_track_penalty_demotes_humanities_for_li_track`（陕西考生跑市场营销 strong→consider，会计学保 strong）、`test_fit_score_track_penalty_skipped_for_3plus3`（同分同专业 3+3 不受惩罚 > 3+1+2 理受惩罚）、`test_fit_score_track_penalty_not_applied_to_stem_major`（CS 不受惩罚）。**threshold 0.30 的精细校准**：用户给的商科列表里 数学 0.25=市场营销（剔除）、数学 0.30=工商管理/电子商务（borderline 保留）、数学 0.35+ = 经/金/会/物流/供应链（保留），threshold 0.30 正好把"市场营销 out, 工商管理 borderline keep"切开。**为何不对文-track 做对称惩罚**：3+1+2 历史首选学生的硬 STEM 专业已被 `required_primary=物理` 硬过滤，无对称必要；3+3 自由选无清晰 track 信号故不触发。端到端实测（陕西物理类 student）：市场营销 0.781→0.664(consider)、国际商务 0.816→0.693(consider)、其余 7 商科保持 strong；CS 仍 0.886(strong)、艺术/教育/社工/设计也叠加 track 惩罚再降一档。验证：79/79 测试绿（76 → +3 v2.4 测试）。|
| 2026-05-18 | 算法 v2.3：继续清理 v2.2 扫描发现的两条数据气味——数学专业化学权重 + 商科政治权重 | 用户洞察：(1) 数学/应数/统计本科专业不学化学，但 v2.2 之前仍挂 0.05-0.10 化学权重，违反"实际本科课程"原则；(2) 6 个商科专业全部带 0.10 政治权重，0.10 看似不高但在 fit_score 加权和里仍占 10% 影响力——商科 globalization 时代外语依赖明显高于政策依赖。改动（纯 JSON 数据）：(a) 数学 化学 0.10→0、物理 0.20→0.30（数学专业最相关学科），应用数学 化学 0.05→0、物理 0.20→0.25，统计学 化学 0.10→0、物理 0.15→0.20、语文 0.10→0.15（统计论文写作）；(b) 工商管理/市场营销/电子商务/物流管理/会计学 政治 0.10→0.05、外语 +0.05；国际商务 政治 0.10→0.05、外语 0.35→0.40（继续强化首要地位）。所有 key_subjects sum 严格 = 1.0；零代码改动；75/75 测试绿。|
| 2026-05-18 | 算法 v2.4：物理专业去化学（不对称修正）| 用户洞察：物理是化学的基础（量子力学/热力学解释化学），但反过来不成立——物理本科课程（力学/电磁学/热学/光学/量子/统计/原子/固体）**完全不学化学课**，而化学本科核心必修物理化学（含量子化学/热力学统计/化学动力学）。这是个不对称依赖。改动（纯 JSON 数据）：物理专业 key 化学 0.15→0.05（保留极低权重作为理科基础信号）、物理 0.40→0.45（首要强化）、外语 0.10→0.15（物理学论文/原典英文）；soft_thresholds 删化学 70。化学专业完全不动——物理 0.20 key 与 70 soft 都合理。sum=1.0 严格保持；75/75 测试绿。|
| 2026-05-18 | 算法 v2.5：全面清理 key_subjects 中的 生物/化学/地理/政治 错位权重——按「除非特殊专业否则不依赖」原则 | 用户洞察：之前几轮零敲碎打地清化学、政治，留下大量低权重残余（DS/CS 化学 0.10、商科政治 0.05、各种 0.05 生物等）。这些 0.05-0.15 的"弱信号"权重每个都不大，但累计起来仍在 fit_score 里把不相关学科混入加权和，污染推荐排序。一次性清理。改动（纯 JSON，29 个专业）：(a) 政治——15 个清零（护理/经济/金融/工商/市场营销/国际商务/物流/电商/供应链/会计/汉语言/英语/心理/艺术/设计），5 个降到 0.05（新闻传播/教育学/社会工作/法学/哲学，作为政策/政治哲学/法理边缘弱信号）；(b) 化学——8 个工科清零（DS/CS/信息安全/电子信息/自动化/电气/机械/物理），土木 0.10→0.05（材料化学）+ 心理学 0.10→0.05（生理心理学）+ 建筑 0.05 保留；化学/生物科学/环境/临床/口腔/药学/护理 化学权重不动（本科核心必修）；(c) 生物——3 个工科清零（DS/CS/信息安全 0.05→0），医学族/生物族/心理学不动（心理学的 25→30 强化）；(d) 地理——13 个清零（工商/市场营销/国际商务/电商/会计/英语/新闻/教育/社工/法学/艺术/设计/哲学），仅保留物流/供应链 0.15（运输与全球网络真依赖）和建筑学 0.10（场地分析课）。空出的权重每个专业按对口学科重分配，所有 sum 严格=1.0：工科 → 数学+物理+语文 各 +0.05；商科 → 数学+外语+语文+历史；文社科 → 语文+外语+历史。建筑学不动（地理 0.10/化学 0.05 都合理）。零代码改动；75/75 测试绿。这是「政治通病」清理的收官——v2.2-v2.5 一路把"按高考分类配权重"的旧设计完全替换为"按本科课程实际依赖"。|
| 2026-05-18 | 算法 v2.6：Q08-Q14 加 notes 字段 + AskUserQuestion 调用规范收紧 | 用户实测发现 Q08 显示给用户的选项描述与 `question_bank.json` 不一致——Claude 擅自补充了"高考 600+/班级头部"等锚点（这些信息真的有用），同时引入错别字"绿高考考"和未授权改字"D 弱→较弱""加'不建议走对学习要求高的专业'"。单一真理源被破坏，且不可复现。改动：(1) `question_bank.json` 为 Q08-Q14 七题的 28 个选项加 `notes` 字段——把 Claude 创造的有用锚点正式化（高考预期分/排名/行为情境），杜绝幻觉漂移；Q01/Q15-Q18 描述已具体不加 notes；(2) SKILL.md Step 3-5 合并为一张表 + 新增「AskUserQuestion 调用规范（v2.6 起严格执行）」节，明确字段映射 `description = qbank.description + '（参考：' + notes + '）'`，列违规警示矩阵（自创锚点 / 改字 / 错别字 / 加建议性文字）并给 Q08 完整示例代码；(3) 新增 lint `test_question_bank_notes_present_for_q08_q14`——若未来 notes 字段被删除则测试失败防止再次漂移。验证：76/76 测试绿。|
| 2026-05-18 | 算法 v2.7：完善全部 12 题 notes + 清洗 Claude 自创错别字 | 用户跑完一遍完整测评后把 Q01/Q08-Q18 显示给用户的内容贴回来——其中包含大量有用的具体锚点（哪类专业、对应的具体行为情境），同时也暴露 Claude 在 v2.6 之前自创时引入的错别字。让所有内容回流到 question_bank.json 作为 notes，单一真理源最终建立。改动：(1) Q01 加 notes——稳定型对应医生/公务员/法官等体制内、可调整型对应 CS/数学/经济、冲上限型对应金融/创业/科研突破；(2) Q08-Q14 现有 notes 用 Claude 显示的更具体行为细节增强（「能从难中找乐趣」「沉没成本不构成阻碍」「有 1-2 个跨学科项目/副业/独立作品」）；(3) Q15 加 notes（上升/稳态是心理素质底盘/下降需恢复）；(4) Q16 加 notes 并**严格按 JSON 顺序对齐**——A=优先学校 / B=优先专业 / C=看情况——修正 Claude 在 AskUserQuestion 显示时把 A/C 顺序搞反的隐患（用户实际看到的 A 显示成了「优先专业」，是错位的）；(5) Q17 加 notes（不重要/中大型/一线/只一线）；(6) Q18 加 notes（避险/权衡/进取）。错别字清洗 13 处：绿高考考→高考预期、召难→枯燥、携动→裹挟、赔成本→沉没成本、点注→点拨、错远目标→长远目标、拓展身外→拓展课外、占个赔始→占先机、备粗的→更可靠的、锐问→大风险、锉→升、心里质量底定→心理素质底盘、举一反三学别的东西→学其他东西。共更新 44 个选项 notes。Lint test 扩展为 Q01/Q08-Q18 全部 12 题必须有 notes。验证：76/76 测试绿。|
| 2026-05-18 | 算法 v3.0：description 升级为多 bullet 串，verbatim 还原用户提供的原始问卷内容 | 用户提供完整原始问卷文本（每个选项含 2-3 条并列短句）+ 截图，要求 description 保留原始多 bullet 格式，notes 作为通俗补充展示。v2.7 之前 description 是单行压缩串（"学新知识通常很快能抓住重点；较复杂内容也能较快理解"），丢失了原始问卷的结构感。改动：(1) 44 个选项 description 重写为多 bullet 格式，用 `\\n` 分隔——Q01/Q08-Q14/Q17/Q18 全部多行（3 bullet 居多，Q12-Q14 是 2 bullet），Q15/Q16 保持单行（原本就短），Q02-Q07 资源题不动；(2) SKILL.md 「AskUserQuestion 调用规范」升级到 v3.0——明确 description 多行渲染：`qbank.description` 含 `\\n` 分隔的 bullet，AskUserQuestion 调用时**保留 `\\n` 不变**（渲染器自动把每行作为 bullet 展示），最终 description = `qbank.description + '\\n（参考：' + notes + '）'`；违规警示加两条——禁止合并 bullet、禁止删除 bullet；示例代码 Q08 完整展示新写法；(3) 零代码改动（render 层用 label 不读 description）；76/76 测试绿。这是 v2.5 「按本科课程实际依赖」+ v2.7 「Claude 自创内容正式化」之后的最后一步——把所有显示给用户的文案彻底回到 JSON 单一真理源，AskUserQuestion 调用时直接拼接，不再有任何 Claude 重写空间。|
| 2026-05-19 | 算法 v3.13：tie-break B 顶点（neutral）加 reach 0.10，消除 reach 权重沿光谱的断崖 | 用户洞察：tie-break 矩阵里 reach 只出现在 A 顶点，导致权重沿光谱 `strong_averse 0.50 → averse 0.35 → neutral 0.00` 断崖式归零——等于说"只有求稳的人才在乎能否就业"。但 reach（成功可达性）是**地板型维度**（人人都想成功就业），不像 paths（行业广度）那样是进取者专属偏好。把 reach 独占给 A 语义不成立。改动：(1) `weights.json::appetite_tie_break_weights.neutral` 从 {paths 0.34, reach 0, correct 0.33, recover 0.33} 改为 {paths 0.30, reach 0.10, correct 0.30, recover 0.30}——reach 取**最小非零**权重（B 里最低），既修复地板语义，又保留对"reach 极高其他全废"铁饭碗陷阱专业的警惕（若给均权 0.25，这类偏科专业会反超四维均衡专业，且 fixture test_neutral_tie_break_ranks_b_above_a_profile 会失败）；(2) 连带按插值公式 averse=0.7A+0.3B、seeking=0.3B+0.7C 重算两行：averse {0.10,0.35,0.10,0.45}→{0.09,0.38,0.09,0.44}、seeking {0.45,0,0.45,0.10}→{0.44,0.03,0.44,0.09}，使 reach 沿光谱 `0.50→0.38→0.10→0.03→0.00` 单调平滑无断崖；(3) `_doc` 更新 B 顶点定义（四维含地板 reach）+ v3.13 说明；(4) `test_tie_break_weights_loaded_correctly_per_appetite` 5 行断言同步更新 averse/neutral/seeking 三行；(5) `§12.3` 权重矩阵表 + 几何意义 B 顶点定义 + 新增"B 为什么有 reach"段；(6) `§12.4` tie-break 算例重写为"铁饭碗陷阱 X vs 四维均衡 Y"，BB 选 Y（reach 0.10 压不过 paths/correct 全面优势），并演示均权 0.25 下 X 反超 → 论证最小非零权重的必要性。strong_averse/strong_seeking/contradiction 三行不变（纯 A/C 顶点与全 0 不受 B 影响）。验证：84/84 测试绿。|
| 2026-05-19 | 算法 v3.12：Q08 加 correct 外溢、Q14 加 reach+correct 外溢（题面直接支撑的多维度）+ 新增 §十二 Tie-break 机制文档 | 用户洞察：(1) Q08 学习能力题面「学新知识快、复杂内容也能较快理解」直接支撑"换方向时学得快"→ correct；(2) Q14 拓展习惯题面「持续主动学英语/编程/证书/工具」直接支撑"找工作多一手"→ reach、"转向多一手"→ correct。这与 v3.11 取消 Q13→paths **不矛盾**：v3.11 的拒绝标准是"题面没出现对应语义"（"长期投入"未提"多分支"），v3.12 的接受标准是"题面明确出现对应行为"。两者共同确立**正反双向判据**：挂维度的充要条件是题面直接支撑，不是补信号也不是强行单维度。改动：(1) `weights.json::trait_to_dim.Q08` 加 correct 子表（外溢幅度 = reach 主幅度的约一半并保持圆整：A=+0.10/B=+0.05/C=-0.05/D=-0.10）；(2) `weights.json::trait_to_dim.Q14` 加 reach + correct 两个外溢子表（各 A=+0.10 → D=-0.10）；(3) `§0.4.1` 因子表 Q08/Q14 行标注"(主)/(溢)"双维度；(4) `§二` 维度映射表 reach 行加 Q14溢、correct 行加 Q08溢+Q14溢；(5) `§二` 末尾"主维度+半幅度外溢（v3.12 起）"原则替换 v3.11 的单维度表述，明确正反判据 + 半幅度规则；(6) `§三` 把 Q8/Q9/Q10/Q11/Q14 合并表拆成"Q9/Q10/Q11 单维度"+"Q8 双维度"+"Q14 三维度"三张表；(7) `§4.3` 法学案例 reach multiplier 上限 1.74→1.91、correct 1.51→1.83，纸面总分上限 ≈40→≈53（从锁高难<50 溢到较难<100），更新论证为"correct 仍是瓶颈维、跨到低难仍被封死"。**端到端影响**：Case A（全 A 素质）经济 495.56→498.75、英语 237.66→261.42 (+10%)、法学 20.57→27.37 (+33%，仍高难<50 因 case A 资源全 C/Q15=B 非极端)；Case B（中低素质）法学 2.91→2.77、英语 28.04→26.57、汉语言 21.03→19.93（均 -5%，Q14=C 外溢拉低）。**所有案例分档不变**（§五期望区间全满足）。**文档侧**：新增 §十二「Tie-break 机制」——补齐用户指出的"tie-break 没有说明"缺口，含 6 级 risk_appetite 表、5 级×4 维权重矩阵 + 三角线性插值几何、tie_break_score 公式 + 算例、contradiction 全 0 权重特殊处理、调参方法；`§0.4` "不进算法的题"内联指向 §十二。验证：84/84 测试绿（含 tie-break fixture，零改动通过）。|
| 2026-05-19 | 算法 v3.11：Q13 从双维度（reach+paths）收紧为单维度（reach），消除隐性权重加倍 | 用户洞察：Q13 题面只问「能否接受多年持续高投入、回报较晚」——本质是抗延迟满足，对 reach（四年扛得住）的映射干净；但映射同时挂 paths 的逻辑（v3.10 前 §二的"愿意为多分支持续投入"）是外推，题面完全没问"多分支"。文档自承"Q13 在乘法里实际出现两次相当于权重加倍——有意为之"，这是用 Q13 给 paths 补信号的**隐性设计**——违反"一题一信号"原则，未来调参时谁也说不清 Q13 改一档实际影响了哪个维度。改动：(1) `weights.json::trait_to_dim.Q13` 删 paths 子表，4 个选项只保留 reach delta（A=+0.15 ~ D=-0.20 不变）；(2) `scoring_model.md::§0.4.1` 因子表 Q13 行从"reach + paths"改为单维度 reach；(3) `§二` 维度映射表 paths 行去掉 Q13、reach 行解释补"愿意持续投入"；(4) `§二` 末尾把"双维度题"段落改写为「每道题一个主维度（v3.11 起）」原则说明；(5) `§三 Q13` 子表从"reach + paths 幅度"双列改回单列；(6) `§4.3` 法学案例验证 paths multiplier 上限从 1.44 降为 1.31（去掉 Q13 +0.10 贡献），总分上限从 ≈44 降为 ≈40，仍属高难档。(7) **测试 fixture 调整**：`_tie_break_overrides` B_MIXED 从 (paths=3, reach=1, correct=2, recover=2) 改为 (2,1,3,2)——v3.11 之前 paths_mult ≈ reach_mult 时三者 total 严格相等；v3.11 后 paths_mult=1.10 ≠ reach_mult=1.158，原 B_MIXED 因分阶段 rounding 漂到 17.69 vs A/C 的 17.70，tie-break 无法 fire。新 base 排布在 all-B 答案下精确得 17.70，保持 base product=12、"reach 弱 + 三维均衡" B-profile 语义不变。**端到端影响**：Case A 经济学 521.64→495.56 (-5%)、英语 261.46→237.66 (-9%)、法学 22.63→20.57 (-9%)；Case B 法学 2.77→2.91 (+5%)、英语 26.63→28.04 (+5%)、汉语言 19.98→21.03 (+5%)。**所有案例分档不变**（§五期望区间全部满足）。**版本号跳号说明**：文档调参日志最后正式条目为 v3.1，v3.2-v3.10 在 git commit 中存在但未补录文档，本次直接续用 v3.11 与 git history 对齐。验证：84/84 测试绿。|
| 2026-05-18 | 算法 v3.1：Q01 前置到 Step 3 第一题，最大化与 Q18 的间隔 | 用户洞察：Q01 路径偏好（"想要什么样的人生路径"=认知/价值层）和 Q18 风险态度（"面对不确定时通常什么状态"=行为/情绪层）测的是同一构念的两个层面，应该用经典心理测量学的"重复测量信度检验"——分置首尾，最大化间隔，让 consistency-bias 最低。v3.0 之前两题同在 Step 5 最后一轮紧邻，用户潜意识保持回答一致，丢失诊断价值。改动（纯 SKILL.md 文档，零代码零数据改动）：(1) Step 3 改为偏好/状态族 4 题（Q01 + Q15 + Q16 + Q17）front-load——在能力题启动用户思维前，捕捉未被启动的认知/价值偏好；(2) Step 4 能力族（Q08-Q11）不动，居中 deep-dive；(3) Step 5 意愿+风险族（Q12-Q14 + Q18）back-load——Q18 在用户经过 14 题素质题暴露真实自我后才回答；(4) Q01 与 Q18 间距 11 题，consistency-bias 最低，AC/CA 矛盾才会被诚实暴露并触发 SKILL.md 末尾的复核流程；(5) SKILL.md 新加段「为什么 Q01 前置、Q18 后置」详细解释设计动机。零代码改动；76/76 测试绿。|
| 2026-05-19 | 算法 v3.14（纯文档，零代码零权重）：(1) §十一 新增「Ability 与 reach 的双管道」小节——把 ability_index（Q08–Q11）既经 trait 管道又经 AI 矩阵进入 reach 的共线性显式承认为**有意设计**，与 v3.11 删除的 Q13→paths 隐性加倍划清边界（此处题面能力实质驱动 AI 适应力、有语义支撑；Q13 当年是纯外推），并记录两个副作用（调 `trait_to_dim.Q08.reach` 只改一半；Q10/Q11 经 ability_index 漏入 reach/paths）；§二 维度映射表加「AI 杠杆（溢）」行交叉指向。(2) §0.7 末尾 + §0.3 加注：blend 乘在 1–625 乘积上，同一个 -30% 折扣砍强专业 ≈187 分、砍弱专业 ≈15 分，录取匹配度的绝对影响被 ADI 量级加权——保留乘积模型为有意选择（让天花板高的专业即使匹配一般也能靠维度乘积顶上来），但 blend 非均匀折扣（对数空间 log(blend) 波动远小于各维 log(5)）。无需重跑断言；测试不受影响。 | 设计审查 #1（reach 能力双计数 vs v3.11 原则）决定承认+文档化；#2（乘积尺度下 blend 影响非均匀）决定保留乘积+文档化。 |
| 2026-05-19 | 算法 v3.15：admission_blend floor 0.7→0.5（range 0.3→0.5，满分仍=1.0），把折扣区间从 [0.7,1.0] 拓到 [0.5,1.0] | 用户决定：旧 floor 下高考分几乎是装饰——admission 0→1 仅 ±30%，远小于单维度提升的数倍。审查发现硬过滤已剔除不合规专业、soft_filter 已分桶，连续 admission_score 被挤进窄带后，同档内排名几乎纯由素质驱动。改动：(1) `weights.json::admission_blend` min_factor 0.5 / range 0.5 + _doc；(2) 2 个 blend 断言更新（0.7→0.5、0.85→0.75）；(3) scoring_model.md §0.1/§0.2/§0.3/§0.5/§0.6/§0.7 公式与区间同步，§0.6 worked example 重算（332.31→311.87 并标注为 pre-v3.15 截图），§0.7 admission 提分边际 +9%→+16%、blend 量级加权注按 floor 0.5 重写（-50% 砍 625→312.5）。case A/B 案例不传 admission_score 故 blend=1.0 不受影响。验证：84/84 测试绿。 | 设计审查 #3：0.7 floor 让高考分话语权过小，决定降到 0.5 让录取匹配能真正移动排名。 |
| 2026-05-19 | 算法 v3.16：三处结构扩展——(#5) 资源作用于全 4 维、(#6) AI 极化加 correct 列、(#4) appetite 从 exact-tie 升级为 ε-band 排名 + personal_fit 子分 | 用户三项决定。改动：(1) **#5** `weights.json` 加 `resource_dim_weights`（recover 1.0/reach·paths 0.5/correct 0.25）；`compute_dimension` 删 `if dimension=="recover"` gate，资源 delta 对全 4 维按 dim_weight×sensitivity 缩放（recover 行为不变）。(2) **#6** `ai_impact_levels` 每格加 correct（仅 boost 域非零:high+0.10/mid+0.05/low-0.05/very_low-0.10，余 0）；gate 改 `if dimension in ("reach","paths","correct")`。语义:correct 只在 boost 域极化（AI 是该域核心工具,高能力自学换方向↑、被替代者↓），其他域换方向能力由通用能力决定故 0。(3) **#4** 新增 `appetite_rank_band.pct`（0.05）；`_band_index` 把 total 装进对数带，`_appetite_sort_key` 改为 (band, personal_fit)；`compute_major` 输出 `personal_fit`（=旧 tie_break_score）。**显示的 total/grade 不变**——保留 v1.5「ADI 客观可比」语义，appetite 只重排同带专业。(4) 文档:§一 伪代码、§二 映射表（资源外溢行 + AI 加 correct）、§0.4.1 因子表、§十（资源全维 + 调参）、§十一（correct 列 + 双管道补 correct + 调参）、§十二（exact-tie→ε-band 全节重写 + personal_fit 改名 + band 调参）、§4.3（法学纸面极限 53→87 caveat）全部同步。**band-safe 设计**:case A/B 专业无 boost、resource C、Q12 温和,不受 #5/#6 影响。**核心保险丝** `test_personality_cannot_erase_baseline_lock`：全 A+资源 A 下 临床 33.54(高难,held)、法学 87.19(较难)、艺术 55.49——艺术从 <50 升到 较难,assertion 50→100 放宽（decisive-sensitivity 专业资源杠杆最大,是 #5 有意设计,baseline lock 仍成立<150）。新增 2 个 ε-band 测试（_band_index 分带 + _appetite_sort_key 同带按 personal_fit/跨带按 total）。验证：86/86 测试绿（84+2）。 | 设计审查 #4 选「只影响排名不进显示 ADI」(保留 v1.5)、#5 选「分级 recover满/reach·paths半/correct¼」、#6 选「扩到 correct」。 |

## 十一、AI 极化效应（v1.2 核心设计）

### 设计原理

AI 对就业的影响**不是均匀加 / 减**，而是**极化**——同一个专业里，能用 AI 当杠杆的人获得更大优势，被 AI 替代任务的人失去优势。这与三个实证信号对齐：

- **姚顺宇假说**：程序员未来是 1/1000 拿 100 倍工资（极度中心化）
- **Anthropic 数据**：22-25 岁青年进入高暴露职业的入职率下降 14%（已经出现的裂缝）
- **历史模式**：被 AI 真正取代的多是人本来就不喜欢的重复性任务，但**任务结构变化后能否上升**因人而异

为捕捉这个效应，v1.2 的 AI 修正系数依赖**两个维度**：
- 专业的 `ai_impact` 等级（boost/neutral/disrupted/threatened）
- 用户的 `ability_index`（从 Q8-Q11 加权平均算出的能力代理）

### Ability Index 计算

Q8 学习能力 + Q9 难度承受 + Q10 试错 + Q11 调整 → 算术平均（A=4 / B=3 / C=2 / D=1），映射到：

| 平均分 | 等级 | 含义 |
|---|---|---|
| ≥ 3.5 | **high** | 全 A 或 3 A + 1 B 类，AI 杠杆高 |
| ≥ 2.5 | **mid** | 全 B 或 2 A + 2 C，AI 中性使用者 |
| ≥ 1.5 | **low** | 全 C 或 B/C/C/D（案例 B 画像），AI 时代弱势 |
| < 1.5 | **very_low** | 全 D 或 3 D，AI 替代风险最高 |

阈值在 `weights.json.ability_index_thresholds`，可调。

### 4×4 极化矩阵（reach / paths）

| ai_impact \ ability | high | mid | low | very_low |
|---|---|---|---|---|
| **boost** | +0.20 / +0.20 | +0.05 / +0.10 | -0.10 / 0 | **-0.25 / -0.10** |
| **neutral** | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| **disrupted** | 0 / -0.05 | -0.05 / -0.05 | -0.15 / -0.10 | -0.25 / -0.15 |
| **threatened** | **-0.10 / -0.10** | -0.20 / -0.15 | -0.30 / -0.20 | -0.40 / -0.25 |

**correct 列（v3.16，仅 boost 域非零）：**

| ai_impact \ ability | high | mid | low | very_low |
|---|---|---|---|---|
| **boost** | +0.10 | +0.05 | -0.05 | -0.10 |
| neutral / disrupted / threatened | 0 | 0 | 0 | 0 |

> **为什么 correct 只在 boost 域极化**：AI 是这些域（数学/CS/DS/AI）的核心生产工具——高能力者能用它快速自学换方向（correct↑），被替代者更难翻身（correct↓）。在 disrupted/threatened/neutral 域，你的「换方向能力」由通用能力决定（已在 Q10/Q11→correct 体现），不随该域的 AI 状态变化，故 correct delta=0。幅度取 reach 的约一半。

**关键观察：**
- **boost 行不是单向正**：high 受益 +20%，very_low 反被 -25% 替代——这就是"上限提高、下限降低"
- **threatened 行整体负**：即使 high 能力也是 -10%，因为行业结构性收缩
- **disrupted 比 threatened 温和**：保留路径但初级岗被挤
- **neutral 不分能力**：医学/教育/物理等没有 AI 极化效应

### Ability 与 reach 的双管道（有意设计，v3.14 文档化）

`ability_index` 由 Q08–Q11 平均得到，而 reach 维度的 trait 管道**已经**直接吃 Q08/Q09。所以同一份学习能力经**两条管道**进入 reach：

1. **trait 管道**（§二 / `trait_to_dim`）：Q08/Q09 直接抬 reach——语义是「能不能扛住四年、学得动」；
2. **AI 矩阵管道**（本节）：同样的 Q08/Q09（连同 Q10/Q11）算出 ability_index，再经本 4×4 矩阵给 reach 加 delta——语义是「AI 时代能不能把能力转成杠杆」。

**这是有意的，不是 v3.11 要消除的那种隐性加倍。** 区别在语义支撑：v3.11 删 Q13→paths，是因为题面只问「长期投入/抗延迟满足」、根本没出现「多分支」，挂 paths 纯属外推；而这里 Q08–Q11 的学习/适应能力**确实**实质驱动 AI 适应力，两条管道代表两个不同机制，叠加正是模型想表达的核心信号（强者上限更高、弱者被替代）。

**两个必须记住的副作用：**

- **调 reach 的 Q08 权重时，改 `trait_to_dim.Q08.reach` 只改了一半**——另一半藏在 `ai_impact_levels[*][ability].reach`，两处要一起想，否则会重蹈 v3.11 警告的「不知道旋钮动了哪个维度」。
- **Q10/Q11 经 ability_index 漏入 reach/paths**：§二 说 Q10/Q11 只映射 correct，但它们是 ability_index 的成分，故对 boost/threatened 专业也会经 AI 矩阵轻微影响 reach/paths。这是用「4 题平均」当 AI 杠杆代理的固有代价，接受之。
- **（v3.16）correct 在 boost 域也有双管道**：correct 的 trait 输入含 Q08溢/Q10/Q11/Q14溢，而 AI 矩阵的 correct 列又经 ability_index（含 Q08/Q10/Q11）作用于 boost 专业的 correct——同 reach 一样的有意叠加，调 boost 专业 correct 时记得两处都要看。

### 39 专业 ai_impact 分类

| 等级 | 数量 | 专业 |
|---|---|---|
| **boost** | 5 | 数学、应用数学、统计学、数据科学/AI、计算机类/软件工程 |
| **neutral** | 19 | 物理、化学、生物科学、信息安全、电子信息工程、自动化、电气工程、机械工程、土木工程、环境工程、临床医学、口腔医学、药学、护理学、教育学、心理学、社会工作、建筑学、哲学 |
| **disrupted** | 10 | 经济学、金融学、工商管理、市场营销、国际商务、物流管理、电子商务、供应链管理、会计学、设计类 |
| **threatened** | 5 | 法学、英语/外语、新闻传播、汉语言文学、艺术类 |

**分类来源**：Anthropic Economic Index 2026 + GPTs are GPTs (Eloundou) + 中传 2025 撤专业实证 + 风灵框架。详见 `wiki/knowledge/AI就业替代框架.md` 和单测断言。

### 关键单测：核心保险丝

`test_cs_low_ability_falls_below_finance_high_ability` 锁住"AI 时代能力比专业重要"的产品信号：低能力学 CS 应低于高能力学金融。如果未来权重调整后该断言失败，需要在调参日志里说明为什么允许"低能力学 CS"超过"高能力学金融"。

### 调整方法

- 重分级某专业 → 改 `baseline_adi.json.majors.<name>.ai_impact`
- 调整 16 个组合的数值 → 改 `weights.json.ai_impact_levels.<level>.<ability>.<dimension>`
- 调整能力分档阈值 → 改 `weights.json.ability_index_thresholds`
- 调整 correct 列数值 → 改 `weights.json.ai_impact_levels.<level>.<ability>.correct`（v3.16：correct 已纳入 AI，gate 为 `if dimension in ("reach", "paths", "correct")`；当前仅 boost 域非零）
- 想让 AI 也作用于 recover 维度 → 改 score_engine.compute_dimension 的 gate 加入 "recover"（不推荐，recover=个人兜底空间，与"AI 影响产业结构"语义分层冲突）

## 十、Resource Sensitivity 等级分类

每个专业的 `resource_sensitivity` 标记在 `baseline_adi.json` 里。等级数值在 `weights.json.resource_sensitivity_levels`，可调。当前分类：

| 等级 | specific (Q3/Q5/Q7) | global (Q12) | 专业（39 个） |
|---|---|---|---|
| **low** (9) | ×0.5 | ×0.7 | 数学、物理、统计学、应用数学、数据科学/AI、计算机类/软件工程、信息安全/网络工程、电子信息工程、自动化 |
| **default** (21) | ×1.0 | ×1.0 | 化学、生物科学、电气工程、机械工程、土木工程、环境工程、药学、护理学、经济学、市场营销、电子商务、供应链管理、会计学、汉语言文学、英语/外语、新闻传播、教育学、心理学、社会工作、设计类、哲学 |
| **high** (7) | ×1.5 | ×1.3 | 金融学、工商管理、国际商务、物流管理、口腔医学、法学、建筑学 |
| **decisive** (2) | ×2.0 | ×1.5 | 临床医学、艺术类（美术/音乐/表演） |

**分类原则：**
- **low**：硬技能驱动专业（LeetCode/GitHub/作品/竞赛/科研指标），家庭资源在求职信号上有微弱加分但不决定上限
- **default**：标准化职业路径（考证/考公/入职），家庭资源加分但不决定
- **high**：圈子/平台/家族企业敏感专业；普通家庭与有资源家庭的天花板差距显著
- **decisive**：信息差和圈子近乎决定能否进入第一份关键工作；临床医学的医二代信息优势、艺术类的圈子推荐机制是典型代表

**资源作用维度（v3.16）**：资源不再只作用 recover——`resource_dim_weights` 把同一个资源 delta 按维度二次缩放后作用于全 4 维：`recover 1.0 / reach 0.5 / paths 0.5 / correct 0.25`。最终 delta = `base_resource × sensitivity_scale × dim_weight`。语义:资源最帮"兜底"，其次"落地就业/拓宽行业"，对"换技能方向"帮助最小。

**调整方法**：
- 重新分级某专业 → 改 `baseline_adi.json.majors.<name>.resource_sensitivity`
- 调整 4 个等级的数值幅度 → 改 `weights.json.resource_sensitivity_levels`，不改算法
- 调整资源在各维度的相对强度 → 改 `weights.json.resource_dim_weights`（v3.16 方案 B 已落地，曾经只作用 recover），不改算法

## 十二、Appetite 排名机制（v3.16 ε-band，旧称 Tie-break）

### 12.1 它是什么（v3.16：exact-tie 升级为 ε-band）

历史上这是「ADI 总分**完全相同**时按什么排序」。但 exact-tie 在真实数据上几乎不触发（不同专业 total 极少 bit/2 位小数完全相等），导致整套 appetite 权重形同虚设。v3.16 改为 **ε-band 排名**：

- 先把每个专业的 `total` 装进**对数带**（相对宽度 `pct`，默认 0.05 = 5%）。
- **跨带**：纯按 `total` 降序——客观可走通性主导。
- **同带**（total 相对差 ≤ ~5%，视为"势均力敌"）：按 `personal_fit` 降序——`personal_fit = Σ(appetite_weights[dim] × adjusted[dim])`，由 Q01×Q18 推出的 `risk_appetite` 等级查 4 维权重表得来。

**关键性质（v1.5 语义保留）**：appetite **不进入 ADI 乘法链，也不改显示的 total / 难度档**——它只在"势均力敌"时重排，并产出一个 `personal_fit` 子分供报告展示。所以 ADI 仍是"客观可走通"、可跨学生比较的纯客观分。这正是你 #4 选「只影响排名，不进显示 ADI」的落点。

### 12.2 risk_appetite 的 6 个等级

由 `(Q01, Q18)` 答案组合查表（`weights.json::q1_q18_to_appetite`）：

| Q01 \ Q18 | A 求稳 | B 权衡 | C 进取 |
|---|---|---|---|
| **A 想稳定** | strong_averse (AA) | averse (AB) | **contradiction** (AC) |
| **B 想可调整** | averse (BA) | neutral (BB) | seeking (BC) |
| **C 冲上限** | **contradiction** (CA) | seeking (CB) | strong_seeking (CC) |

- **5 个连续等级**：strong_averse → averse → neutral → seeking → strong_seeking，反映"求稳 ↔ 进取"光谱
- **第 6 个特殊等级 contradiction**：AC/CA 组合——价值偏好（Q01）与行为风格（Q18）反向，视为信号矛盾，触发交叉验证警告

### 12.3 4 维度权重矩阵

每个等级对应 4 个维度的权重（`weights.json::appetite_tie_break_weights`，每行权重和=1.0）：

| 等级 | paths | reach | correct | recover | 解读 |
|---|---|---|---|---|---|
| strong_averse | 0.00 | 0.50 | 0.00 | 0.50 | 纯 A profile：要"成功可达"+"失败可控" |
| averse | 0.09 | 0.38 | 0.09 | 0.44 | 主 A，少量 B 混入 |
| **neutral** | 0.30 | 0.10 | 0.30 | 0.30 | 纯 B profile：四维都要一点，reach 最低 |
| seeking | 0.44 | 0.03 | 0.44 | 0.09 | 主 C，少量 B 混入 |
| strong_seeking | 0.50 | 0.00 | 0.50 | 0.00 | 纯 C profile：要"机会广"+"出错能改" |
| **contradiction** | 0.00 | 0.00 | 0.00 | 0.00 | 信号矛盾 → 不偏好任何维度，加权和恒为 0，同分按输入顺序稳定排序 |

**几何意义**：A/B/C 是三角形三个顶点，5 个连续等级是这个三角形上的**线性插值**：

```
strong_averse = 1.0 × A
averse        = 0.7 × A + 0.3 × B
neutral       = 1.0 × B
seeking       = 0.3 × B + 0.7 × C
strong_seeking= 1.0 × C
```

其中：
- A 顶点 = `{reach: 0.5, recover: 0.5}` —— "求稳"的两个支柱
- B 顶点 = `{paths: 0.3, reach: 0.1, correct: 0.3, recover: 0.3}` —— "可调整"的三支柱 + 地板 reach
- C 顶点 = `{paths: 0.5, correct: 0.5}` —— "冲上限"的两个支柱

> **B 为什么有 reach（v3.13 起）**：`reach`（成功可达性）是**地板型维度**——所有人都想成功就业，不分求稳/权衡/进取，不像 `paths`（行业广度）那样是进取者专属偏好。v3.12 之前 B 的 reach=0，导致 reach 权重沿光谱在 `averse 0.35 → neutral 0.00` 出现**断崖**，等于说"只有求稳者才在乎就业"，语义不成立。v3.13 给 B 一个**最小非零** reach（0.10，B 里最低权重）：既修复地板语义，又保留对"reach 极高其他全废"铁饭碗陷阱专业的警惕（若给 reach 均权 0.25，这类偏科专业会反超四维均衡专业）。连带重算 averse/seeking 后，reach 沿光谱变为 `0.50 → 0.38 → 0.10 → 0.03 → 0.00`，单调平滑无断崖。

### 12.4 personal_fit 公式（v3.16，旧称 tie_break_score）

```
personal_fit(major) = Σ (w_dim × adjusted_dim)
                    = w_paths × paths_adj
                    + w_reach × reach_adj
                    + w_correct × correct_adj
                    + w_recover × recover_adj
```

`personal_fit` 作为每个专业的输出字段（供报告展示），并在**同带**专业间做降序排名。例（BB 权衡型，neutral 权重 `{paths 0.30, reach 0.10, correct 0.30, recover 0.30}`，假设 X/Y 落在同一 ε-band）：

- 专业 X（铁饭碗陷阱型）: adjusted = `{paths 1, reach 5, correct 1, recover 4}` —— reach 超高，其他低
- 专业 Y（四维均衡型）: adjusted = `{paths 3, reach 3, correct 3, recover 2}`
- 假设 X 和 Y 的 ADI total 相同
- X 的 tie_break_score = 0.30×1 + 0.10×5 + 0.30×1 + 0.30×4 = 0.30 + 0.50 + 0.30 + 1.20 = **2.30**
- Y 的 tie_break_score = 0.30×3 + 0.10×3 + 0.30×3 + 0.30×2 = 0.90 + 0.30 + 0.90 + 0.60 = **2.70**
- **Y 排前**——权衡者偏好四维均衡的专业。X 的 reach=5 虽超高，但 reach 权重只有 0.10，压不过 Y 在 paths/correct 上的全面优势。

**这个例子正解释了 reach=0.10 而非均权的设计**：若 reach 给均权 0.25，X 的 score = 0.25×(1+5+1+4) = 2.75 反而 > Y 的 0.25×(3+3+3+2) = 2.75（持平甚至因 base 微差反超）——铁饭碗陷阱专业靠单一超高 reach 就能挤掉均衡专业。给 reach 最低非零权重，既承认"权衡者也要就业"，又不让单一维度绑架排序。

### 12.5 contradiction 的特殊处理

`(Q01=A, Q18=C)` 或 `(Q01=C, Q18=A)` → 你"心里想稳"但"行为偏冒险"，或反之。这种矛盾下：

1. `risk_appetite` 标记为 `"contradiction"` 而非任何插值等级
2. `personal_fit` 用全 0 权重 → 永远为 0 → 同带专业按 `majors_input` 顺序保留（stable sort）
3. 报告 chip 显示 ⚠️ "信号矛盾（Q1 与 Q18 方向相反）"
4. SKILL.md 末尾的"Q1/Q18 矛盾交叉验证"流程触发，要求 Claude 与用户复核

**为什么不强行选一边**：算法不知道你是"嘴上稳实际激进"还是"嘴上激进实际稳"——这是用户自己也未必清楚的内在张力。算法的诚实做法是**承认不知道**，把这个张力呈现给用户而不是替他决策。

### 12.6 调参方法

- 改 5 级权重 → 改 `weights.json.appetite_tie_break_weights[等级][维度]`，每行权重和必须=1.0（contradiction 行例外，恒为全 0）
- 改 (Q01, Q18) → appetite 映射 → 改 `weights.json.q1_q18_to_appetite`
- 改"势均力敌"带宽 → 改 `weights.json.appetite_rank_band.pct`（v3.16，默认 0.05）。调大 → appetite 影响更多专业的排序；调小 → 越接近旧的 exact-tie 行为
- 想让 appetite 影响 ADI 主分（v1.4 曾尝试，v1.5 回退）→ 不推荐，会污染"客观可走通"语义。v3.16 选择 ε-band 排名 + `personal_fit` 子分，正是为了既让 appetite 实际起作用、又不碰显示的 ADI total / 难度档

详见调参日志 v1.4 → v1.8 系列对 tie-break 机制的演化记录。
