# IdeaHunter 核心组件升级与重构日志 (v0.2.0-beta)

本文档记录了基于 RFC 设计文档和 Intro 愿景，对 IdeaHunter 核心模块进行的系统性重构与修复。

---

## 🌍 国际化：中英文双语模式支持 (最新)

*   **背景**：为了将 BizRadar 推广到全球市场与海外开发者社区（如 Hacker News, Reddit），需要系统支持纯正的英文分析与报告输出。
*   **核心改进**：
    *   **全局配置**：`config/settings.py` 和 `.env.example` 新增 `output_language` ("zh" 或 "en") 配置项。
    *   **Agent 双语 Prompt 架构**：重构了所有 5 个核心 Agent（`Extractor`, `PM`, `TechLead`, `Critic`, `Planner`），为每个 Agent 剥离并内置了 `SYSTEM_ZH` 和 `SYSTEM_EN` 双语系统指令，根据请求动态切换。
    *   **请求级语言隔离**：重构了后端 API 的 `ScanBody` 和 `IngestBody` 模型，接受 `language` 参数。API 路由动态克隆并覆盖 `Settings`，确保并发运行不同语言任务时互不干扰。
    *   **UI 与分享卡片全面本地化**：
        *   Web 界面任务表单新增「输出语言」切换控件。
        *   Canvas 分享卡片渲染引擎增加智能探测机制，在英文模式下自动将所有固定标签（如“核心痛点”、“商业评分”）和页脚日期声明切换为英文（如 "Core Pain Point", "Business Score"）。

---
## 📄 Planner Agent 深度升级 — 立项书完整度提升 (最新)

*   **背景**：原版立项书各章节内容过于简单，靠 LLM 自由发挥产出废话（如"可以在社交媒体宣传"），与 RFC 和 Intro 中展示的完整立项书示例相差甚远。
*   **核心改进**：
    *   **新增数据输入**：`extracted_complaint`（原始用户抱怨原文）、TechLead 四个字段（`dev_weeks` / `tl_feasibility` / `tl_tech_risk` / `tl_mvp_approach`）全部注入 Planner，使立项书能引用真实数据而非 LLM 推断。
    *   **章节内容规范化**（8大章节，每章有详细要求）：
        *   **痛点溯源**：强制引用原始抱怨原文（保留口语），分析系统性原因，量化痛苦频率，描述现有凑合方案代价
        *   **产品定义**（新增）：输出产品一句话定位、核心三功能、边界声明（不做什么）以及“啊哈时刻”的用户原话
        *   **目标用户**：要求给出具体职业/场景，描述用户的一天，并输出**三档定价方案**（免费版/专业版¥XX/团队版¥XX）
        *   **竞品格局**：强制输出 Markdown 功能对比表格，含竞品致命缺陷分析和差异化一句话主张
        *   **MVP 功能清单**：按 P0/P1/P2 三层分类，每条功能必须含工期估算（天数）
        *   **技术栈**：按前/后/数据库/AI/部署五层输出，并给出**月运营成本数字**（¥XX/月）
        *   **冷启动获客**：输出 3 个渠道的**可直接复制的逐字话术**，以及 4 周执行计划和成功标准
    *   **5 条质量红线**：违反任何一条 LLM 须重写（如禁止出现"按市场定价"、"成本较低"等废话）
    *   **技术可行性栏**：评分卡新增一行显示 TechLead 的 feasibility_score 和工期

---

## ⚙️ P1 级别信号质量升级 (最新)

### 1. Tech Lead Agent — 技术可行性评估层
*   **背景**：RFC 明确规定需要"技术合伙人 Agent"来过滤技术上不可行（如需训练底层大模型、严重依赖封闭 API）的点子，避免浪费用户时间。
*   **实现**：
    *   新增 `core/agents/techlead_agent.py`。
    *   在流水线 **PM Agent 之后、Critic Agent 之前**插入技术评估节点，输出三个关键指标：
        *   `dev_weeks`：独立开发者实现 MVP 所需工作周数
        *   `feasibility_score`：技术可行性综合评分（0-100）
        *   `tech_risk`：最关键技术障碍一句话描述
    *   评估结果作为上下文注入 Critic Agent，Critic 的系统 Prompt 中明确规定：`feasibility_score < 50` 时强制扣减 `freq_score` 10 分，`dev_weeks > 12` 时酌情扣减 `gap_score` 5 分。
    *   SSE 事件流新增 `techlead` 事件，实时上报评估结果。

### 2. 跨源痛点聚合 — 多平台热度信号放大
*   **背景**：此前系统将多个平台捕获的同一痛点视为独立点子分别立项，无法放大"多平台印证"带来的热度信号，与 Intro 示例中"捕获 42 条高危抱怨"的愿景严重不符。
*   **实现**：
    *   新增 `core/agents/idea_aggregator.py`，采用**字符 bigram Jaccard 相似度算法**（无需任何外部依赖，对中英混合文本效果良好）。
    *   在 `run_pipeline` 完成所有条目处理后，自动触发聚合：对本轮新增 ideas 进行两两相似度比较，超过阈值（默认 0.42）则视为同一痛点。
    *   **合并策略**：保留评分更高的条目，将另一条目的 `raw_complaints_analyzed` 数量累加进来，被合并的条目从数据库中删除。
    *   `SqliteManager` 新增 `bump_complaints()` 和 `delete_idea()` 两个方法支撑聚合操作。
    *   SSE 事件流新增 `aggregation_done` 事件。

---


## 🎯 P0 级别核心重构 (最新)

本次重构主要解决了系统在实际运行中偏离“黄金三定律”初衷，以及生成的立项书质量不可控的致命缺陷。

### 1. 消除“竞品分析”的纯 LLM 幻觉
*   **问题重述**：此前 `Critic Agent` 对竞品的判断完全依赖 LLM 的预训练数据，导致对冷门赛道和 2024 年以后的新产品一无所知，经常出现严重的“幻觉漂移”。
*   **重构方案**：
    *   新增 `core/agents/competitor_research.py` 模块。
    *   在 Orchestrator 流程中，**Extractor 发现痛点后、Critic 评分前**，主动调用 `Serper API` 针对该痛点进行真实的 Web 检索（包含 `工具/App` 以及 `ProductHunt/AlternativeTo` 站内检索）。
    *   将真实的搜索快照作为上下文注入给 `Critic Agent`。
    *   **兜底机制**：若未配置 `SERPER_API_KEY`，则静默降级为旧版模式（由 LLM 推测），确保向后兼容。

### 2. 规范化 Planner 输出与三维指标量化
*   **问题重述**：原本 `Planner Agent` 输出的立项书格式混乱，完全受制于 LLM 的自由发挥，缺少 Intro 愿景中提到的“需求热度”、“付费意愿”等直观量化星级。
*   **重构方案**：
    *   **三维子分拆解**：修改 `Critic Agent` 的输出结构，强制其将总分拆解为严格遵循“黄金三定律”的三个子指标：
        *   `freq_score` (高频重复性，0-35分)
        *   `gap_score` (平台缝隙/大厂免疫，0-35分)
        *   `roi_score` (商业闭环，0-30分)
    *   **自动星级计算**：在 `Planner Agent` 中引入纯 Python 函数 `_freq_label` 和 `_stars`，将上述子分转换为确定的星级（如 🔥🔥🔥、⭐⭐⭐），并作为静态字符串直接注入给 LLM。
    *   **严格 Markdown 模板化**：修改了 `Planner Agent` 的系统提示词，强制要求其输出固定包含 7 大章节的结构化 Markdown，顶部必须自带**《📊 商业机会评分卡》**。

---

## 🛠️ 环境兼容性与启动修复 (Bug Fixes)

针对从独立分支合并至新仓库后产生的大量 500 服务器错误，完成了以下补救：

1.  **SQLite `list_ideas` 参数越界修复**：
    *   **现象**：`server.py` 传递了 `search` 关键字，但新版 `sqlite_manager.py` 缺失该参数。
    *   **修复**：为 SQLite Manager 的 `list_ideas` 方法正式添加了对 `search` 参数的支持，并实现了对 `title` 和 `markdown_prd` 的 `LIKE` 模糊匹配，顺便激活了前端的搜索功能。
2.  **`Settings` 配置对象属性缺失修复**：
    *   **现象**：运行过程中大量抛出 `AttributeError: 'Settings' object has no attribute ...`（涉及 `serper_api_key`, `score_approve_min`, `hot_mode_enabled` 等）。
    *   **修复**：补齐了 `config/settings.py` 中遗漏的 11 个核心字段（包含阈值控制、定时调度配置、第三方 API Key、多跳抓取控制等），消除了全量插件的实例化 `TypeError`。
3.  **`BaseScraper` 签名向后兼容**：
    *   **现象**：新集成的 7 个数据源插件依赖父类的 `multi_hop` 机制，但在调用 `super().__init__()` 时报错。
    *   **修复**：重写了 `plugins/base_scraper.py`，加入了标准的 `__init__` 构造器和包含 `search_keywords` 的抽象方法签名，保证了全部 13 个插件的稳定加载。

---

## 🌟 历史关键里程碑回顾

在 P0 修复之前，我们已经完成了以下底层能力的建设：

1.  **架构升级：全链路 Server-Sent Events (SSE) 实时反馈**
    *   重写了前端轮询逻辑，引入 `EventSource`，实现微秒级的流水线状态上报。
    *   构建了 `EventBus` 机制，串联 8 个核心生命周期钩子（Fetch -> Extractor -> Critic -> Planner -> Approved/Rejected/Error）。
2.  **数据扩容：7 大高质量信息源接入**
    *   突破了单纯的 V2EX 限制，新增了：`Twitter/X`（痛点搜索）、`AppStore`（一星差评流）、`ProductHunt`、`IndieHackers`、`少数派`、`36氪/虎嗅`、`微博热搜`。
    *   新增了全平台的 `scan-all`（一键全源扫描）调度器，前端支持状态徽章可视化监控。
3.  **环境变量与密钥治理**
    *   标准化了 `.env.example`，将所有新增数据源所需的 Token（如 Twitter Bearer、AppStore Country 等）全部文档化并集中管理，实现安全解耦。
