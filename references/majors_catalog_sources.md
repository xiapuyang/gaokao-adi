# 专业清单来源与热度参考（v3.17 覆盖扩充）

> 本文记录 2026-06-27 一次性把 `majors_admission_2024.json` 与 `baseline_adi.json`
> 从 39 个专业扩充到 90 个（新增 51 个，文科为主）的**来源、热度依据、选科口径与 ADI 填参规则**。
> 数据本体在两个 JSON；本文是可追溯的旁注，不参与代码读取。

## 一、权威来源

| 用途 | 来源 | 链接 |
|---|---|---|
| 专业骨架（门类/专业类/专业名规范） | 教育部《普通高等学校本科专业目录（2024 年）》 | http://www.moe.gov.cn/ |
| 选科要求口径 | 教育部《普通高校本科招生专业选考科目要求指引（2024 版/通用版）》 | http://www.moe.gov.cn/jyb_xwfb/xw_zt/moe_357/2024/2024_zt12/wd/gkwd_zlhz/202406/t20240603_1133732.html |
| 专业知识库 / 开设院校数 | 阳光高考·专业知识库（学信网） | https://gaokao.chsi.com.cn/zyk/zybk/ |
| 报考热度榜（TOP50） | 北京高考在线·2025 年本科专业热度榜 | https://www.gaokzx.com/gk/zhiyuan/146856.html |

> ⚠️ 选科要求**逐校逐年微调**，本文与 JSON 给的是 2024 指引下的**类型级常见口径**，
> 落地填报仍以考生所在省（如陕西 sneac.com、湖北 zsxxw.e21.cn）当年发布的电子版招生计划为准。

## 二、热度事实（用于排优先级，非 ADI 输入）

- **2025 热度榜 TOP10**（半数工科）：电气工程及其自动化、数字媒体技术、口腔医学、动物医学、电子商务、人工智能、临床医学、自动化、机械设计制造及其自动化、国际经济与贸易。
- **开设院校数 TOP10**：计算机科学与技术(940)、英语(933)、视觉传达设计(741)、数据科学与大数据技术(739)、环境设计(713)、财务管理(669)、会计学(659)、电子信息工程(644)、软件工程(636)、国际经济与贸易(633)。
- **文科三大王牌（报考占比远高于其他）**：汉语言文学、法学、会计学；**第二梯队**：财务管理、英语。
- 教育类（小学教育/学前教育）受出生人口下行，热度近年回落，但仍是稳定就业型。

这些热度信号**只用于决定"先补哪些专业"**，不进 ADI 乘法——ADI 衡量的是"走不走得通"，与"热不热"是两回事（见 `theory.md` §二）。

## 三、本次新增 51 个专业（按门类）

字段含义：**选科**＝3+1+2 首选及必选再选的常见口径；**ADI**＝paths×reach×correct×recover＝总分（`theory.md` §四分档：>300 低难 / 150–300 中等 / 50–150 较难 / <50 高难）；**资源**＝resource_sensitivity；**把握**＝confidence。

| 专业 | 选科 | ADI 四维=总分 | 分档 | AI 影响 | 资源 | 把握 |
|---|---|---|---|---|---|---|
| 财政学 | 不限 | 3×3×3×3=81 | 较难 | disrupted | high | high |
| 国际经济与贸易 | 不限 | 3×3×3×3=81 | 较难 | disrupted | high | high |
| 金融工程 | 不限 | 3×3×3×3=81 | 较难 | disrupted | high | medium |
| 投资学 | 不限 | 3×2×3×3=54 | 较难 | disrupted | high | medium |
| 保险学 | 不限 | 3×3×3×3=81 | 较难 | disrupted | default | high |
| 财务管理 | 不限 | 4×4×4×4=256 | 中等 | disrupted | default | high |
| 审计学 | 不限 | 4×4×4×3=192 | 中等 | disrupted | default | high |
| 人力资源管理 | 不限 | 3×3×4×3=108 | 较难 | disrupted | default | high |
| 行政管理 | 不限 | 3×3×4×3=108 | 较难 | disrupted | high | high |
| 公共事业管理 | 不限 | 2×2×3×3=36 | 高难 | disrupted | default | medium |
| 旅游管理 | 不限 | 3×3×3×3=81 | 较难 | disrupted | default | high |
| 工程管理 | 物理 | 2×3×3×3=54 | 较难 | neutral | default | medium |
| 信息管理与信息系统 | 不限 | 4×3×4×4=192 | 中等 | neutral | low | medium |
| 政治学与行政学 | 不限 | 2×2×3×3=36 | 高难 | neutral | high | medium |
| 国际政治 | 不限 | 2×2×2×2=16 | 高难 | neutral | high | medium |
| 社会学 | 不限 | 2×2×3×2=24 | 高难 | neutral | default | medium |
| 知识产权 | 不限 | 3×3×2×3=54 | 较难 | threatened | default | medium |
| 公安学类（侦查学/治安学） | 不限 | 2×4×2×3=48 | 高难 | neutral | default | low |
| 思想政治教育 / 马克思主义理论 | 不限 | 3×4×3×3=108 | 较难 | neutral | default | high |
| 翻译 | 不限 | 2×2×3×3=36 | 高难 | threatened | default | high |
| 日语 | 不限 | 3×3×3×3=81 | 较难 | threatened | default | high |
| 小语种（德/法/西/俄等） | 不限 | 2×3×3×3=54 | 较难 | threatened | default | medium |
| 广告学 | 不限 | 3×3×3×3=81 | 较难 | threatened | default | high |
| 网络与新媒体 | 不限 | 3×3×3×3=81 | 较难 | threatened | default | high |
| 编辑出版学 | 不限 | 2×2×3×3=36 | 高难 | threatened | default | medium |
| 汉语国际教育 | 不限 | 2×2×3×3=36 | 高难 | threatened | default | medium |
| 历史学 | 不限 | 2×3×3×3=54 | 较难 | neutral | default | high |
| 考古学 | 不限 | 2×2×2×2=16 | 高难 | neutral | default | medium |
| 文物与博物馆学 | 不限 | 2×2×2×2=16 | 高难 | neutral | high | medium |
| 学前教育 | 不限 | 2×4×3×3=72 | 较难 | neutral | default | high |
| 小学教育 | 不限 | 3×4×3×3=108 | 较难 | neutral | default | high |
| 体育教育 / 体育类 | 不限 | 2×3×2×2=24 | 高难 | neutral | default | medium |
| 地理科学 | 不限 | 3×3×3×3=81 | 较难 | neutral | default | medium |
| 数字媒体技术 | 物理 | 4×3×4×3=144 | 较难 | disrupted | low | medium |
| 通信工程 | 物理+化学 | 4×4×3×4=192 | 中等 | neutral | default | high |
| 材料科学与工程 | 物理+化学 | 2×3×3×2=36 | 高难 | neutral | default | high |
| 化学工程与工艺 | 物理+化学 | 3×3×3×3=81 | 较难 | neutral | default | high |
| 能源与动力工程 | 物理+化学 | 3×4×3×3=108 | 较难 | neutral | default | high |
| 食品科学与工程 | 物理+化学 | 3×3×3×3=81 | 较难 | neutral | default | medium |
| 生物医学工程 | 物理+化学 | 3×3×3×3=81 | 较难 | neutral | default | medium |
| 航空航天类 | 物理+化学 | 3×4×3×3=108 | 较难 | neutral | default | high |
| 车辆工程 | 物理+化学 | 3×4×3×3=108 | 较难 | neutral | default | high |
| 动物医学 | 物理+化学+生物 | 3×4×3×3=108 | 较难 | neutral | default | high |
| 农学类（农学/园艺/植物保护） | 物理+化学 | 2×3×2×2=24 | 高难 | neutral | default | medium |
| 中医学 | 物理+化学 | 2×3×1×2=12 | 高难 | neutral | high | medium |
| 中药学 | 物理+化学 | 3×3×2×3=54 | 较难 | neutral | default | medium |
| 预防医学 | 物理+化学+生物 | 2×3×2×2=24 | 高难 | neutral | default | medium |
| 医学影像学 | 物理+化学 | 2×4×2×2=32 | 高难 | disrupted | high | medium |
| 康复治疗学 | 物理+化学 | 2×4×2×3=48 | 高难 | neutral | default | medium |
| 戏剧与影视类（播音/编导/表演） | 不限 | 2×2×2×1=8 | 高难 | decisive 资源 / threatened | decisive | medium |
| 数字媒体艺术 | 不限 | 3×3×3×3=81 | 较难 | disrupted | default | medium |

## 四、选科口径规则（本次填参依据）

按 2024 指引的类型级常见口径，本次统一遵循：

1. **经管文法教大部分专业**：`required_primary=null`、`traditional_track` 视文/理倾向取 `both`/`文`。
2. **理工类**：`required_primary="物理"`，多数另要求化学（`required_electives_all=["化学"]`）。
3. **医学类**：物理＋化学；临床/口腔/动物医学/预防医学等另加生物。
4. **中医学**：长学制，多数要物理，部分院校历史/物理均可、选化学或生物——`confidence=medium`，并在 `note` 标注。
5. **公安/航空航天/师范类**：另有政审、体测、术科、定向等批次约束，写入 `note`，`confidence` 取 low/medium。

> `key_subjects` 权重一律按"本科真实高频使用学科 + 工作需要"分配并严格 `求和=1.0`（沿用
> `scoring_model.md` changes_v2.2–v2.5 的清理原则：政治/地理/化学/生物不滥给权重）。
> `soft_thresholds` 沿用 v3.5「及格线」口径：语数外 90/150、其他科目 60/100，只给最核心学科设底。

## 五、ADI 填参规则（与既有标杆对齐）

不与原作者算法对数字，只对**分档**（`theory.md` §五）。本次以既有专业为锚平移：

- **会计学/财务管理/审计学**（资格化刚需、可转岗）→ 4×4×4×(3~4)，中等档。
- **国际经济与贸易**镜像 `国际商务`（外贸收窄、资源敏感）→ 3×3×3×3，较难、resource=high、AI disrupted。
- **通信工程**镜像 `电子信息工程`→ 4×4×3×4，中等档。
- **翻译/小语种/广告/网媒**＝AI 直接威胁的内容/语言类 → ai_impact=threatened，较难/高难。
- **中医学**＝长学制高锁定，对标 `临床医学`（correct=1 锁死纠偏）→ 高难、resource=high。
- **戏剧影视类**对标 `艺术类`→ recover=1、resource=decisive、高难——基础锁定性不被个人素质抹平（§八不变量 #3）。

分档分布（新增 51 个）：中等 4、较难 29、高难 18——与"文科多落较难/高难、资格化与工科类才进中等"的现实一致。

## 六、维护须知

- 两文件 `majors` 键必须等集（`test_admission_baseline_keys_aligned` 守护）；新增务必**同时写两边**。
- 每个 baseline 专业 `rationale` 必须含 `baseline/resource/ai_impact` 三层非空（`test_rationale_three_layers_required`）。
- 别名维护：`国际经济与贸易`、`生物医学工程` 本次由"别名"升为"一等专业"，旧别名已删除；
  新增别名（国贸→国际经济与贸易、兽医→动物医学、马理论→思想政治教育/马克思主义理论 等）写在 `baseline_adi.json._aliases`。
- 本次新增专业 `_confidence` 多为 medium：权重与四维是经验估计，上线后应用真实录取/就业数据调优。
- 复算脚本：`outputs/expand_majors.py`（含写后自检：键对齐 / 求和=1.0 / 三层 / 枚举 / 别名冲突）。
