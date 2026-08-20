# My_agent 项目总览（任务一：金融事件舆情跟踪 Agent）

> 本文档面向项目总结、面试准备与后续迭代，**仅覆盖任务一**（信息收集 → 结构化分析 → 简报生成 → 评测 → 前端集成）。  
> 任务二（舆情演化与情感分析）暂不纳入。  
> 更细的流水线说明见同目录 `02_total.md`、`03_stage2.md`、`04_newsnow_rss.md`、`05_weibo.md`。

---

## 1. 项目定位

构建面向**金融政策发布**和**市场热点事件**的轻量级单 Agent，结合新闻、政策文件和机构观点等公开信息，实现：

- 事件信息收集（多源检索）
- 内容整理与结构化抽取
- 阶段性跟踪（Case 复用、追加来源、补充检索）
- 舆情简报生成（带来源引用）
- 质量评测（离线覆盖评测脚本，不进入前端）

**不做的事：** 不接实时行情、不接结构化财务数据库；结果仅基于公开资料，不构成投资建议。

---

## 2. 整体架构

### 2.1 接口层

- **FastAPI + Pydantic** 封装任务创建、Query 审核、运行状态查询、报告获取等能力，将前端交互与 Agent Workflow 解耦。
- **原生 HTML / CSS / JavaScript** 前端，通过 `fetch` 调用 API，支持数据源选择、Query 审核、任务进度轮询、Markdown / HTML Dashboard 报告查看与案例问答。评测不在前端展示，由后端离线脚本完成。
- 采用 **`run_id` + `case_id`** 管理长任务生命周期；支持 **Human-in-the-loop**：用户可确认或修改 Tavily Query 后再执行检索。

**核心 API 流程：**

```text
POST /api/v1/plans                          → 生成检索计划（waiting_for_review）
POST /api/v1/plans/{run_id}/approve         → 批准 Query，后台执行
GET  /api/v1/runs/{run_id}                  → 轮询进度与状态
GET  /api/v1/runs/{run_id}/report           → 获取 Markdown + brief_data
GET  /api/v1/runs/{run_id}/report/view      → HTML Dashboard
```

**扩展能力（增量检索 / 断点恢复）：**

```text
POST /api/v1/runs/{run_id}/rerun            → 人工修改 Tavily Query 后补搜
POST /api/v1/runs/{run_id}/sources          → 追加指定数据源并执行 Stage 2
POST /api/v1/runs/{run_id}/resume-analysis  → 从 PostgreSQL 恢复未完成 Stage 2
POST /api/v1/runs/{run_id}/brief            → 基于 prepared_analysis 单独生成简报
POST /api/v1/cases/lookup                   → 历史 Case 复用匹配
POST /api/v1/cases/{case_id}/chat           → 案例内问答（fast / analysis / deep）
```

### 2.2 编排层

- **FinancialMediaAgent**（`agent.py`）负责定义 Query 规划、媒体检索、正文获取、相关性筛选、观点抽取和报告生成的执行顺序。
- **RunState**（`state/run_state.py`）作为运行时状态对象，在各阶段之间传递用户问题、检索词、候选数据、正文文档、相关性判断、结构化观点及最终报告。
- **6 个 Node + 5 个数据源工具 + PostgreSQL 持久化** 完成模块化分工：

| Node | 职责 |
|------|------|
| `QueryPlanNode` | 自然语言 → topic / core / support / tavily_query / weibo_query |
| `RetrievalCheckNode` | 首轮检索后判断各 Provider 是否达到阈值 |
| `AdaptiveRetrievalNode` | 为不足的 Provider 生成补充 Query（当前主要作用于微博） |
| `CandidateFilterNode` | LLM 正文相关性复核 |
| `MediaNode` | 逐篇结构化抽取（事实、观点、时间线等） |
| `BriefNode` | 跨来源综合 → `brief_data` JSON → 固定模板渲染 Markdown |

| Tool / Provider | 职责 |
|-----------------|------|
| `NewsNowProvider` | 12 个平台热榜 HTTP API |
| `RSSProvider` | 11 个 RSSHub Feed |
| `TavilyMediaProvider` | Tavily 网页搜索（定向域名 + 通用） |
| `WeiboProvider` | 微博搜索（Cookie） |
| `WebReader` | 候选 URL 正文抓取与清洗 |

### 2.3 工具层

**4 个 Provider + 1 个正文读取工具：**

- **NewsNow**：每个来源请求一次，失败最多重试 1 次，请求间隔 0.5s。
- **RSS**：`requests` 拉取 RSSHub Feed 并解析 XML；默认保留最近 **60 天**内容（`max_age_days: 60`）；请求间隔 0.5–1.0s，失败最多重试 1 次。
- **Tavily**：每个 Query 执行「可信域名定向搜索 + 通用搜索」两轮（`search_rounds: 2`）；当前 Query 规划**只生成 1 条** Tavily Query，并做 anchor 校验防止偏离原问题。
- **微博**：单轮最多访问 **5 页**、目标 **30 条**帖子；页面请求间隔 4–8 秒；默认配置 `weibo.enabled: false`，需配置 Cookie 后在前端勾选启用；评论抓取可配置（`comments.enabled: true`，最多 5 帖）。

**正文处理链路：**

1. `WebReader` 重新抓取正文，HTML 提取可见文字，失败时关闭代理重试。
2. 按 **1,500 字符 / Chunk、200 字符 overlap** 切分。
3. 根据 topic、core、support 关键词轻量打分，选取 **Top-K=5** 片段作为 LLM 复核输入。
4. `CandidateFilterNode` 判定 `relevant=true` 且 `score ≥ 30`（配置项 `relevance_model_min_score`）才进入 MediaNode。
5. MediaNode 优先使用**完整正文**（≤12,000 字符一次分析；超长按 5,000 字符分块合并）。

**LLMClient**（`llms/base.py`）：基于 OpenAI SDK，兼容 DeepSeek、Qwen 等 OpenAI-compatible API；统一配置模型、接口地址、超时与重试。

### 2.4 数据处理层（Stage 1 / Stage 2）

```text
Stage 1：信息发现（高召回、候选级全量保留）
  Query Plan → 多 Provider 检索 → 本地预筛 → 去重 → 写入 PostgreSQL

Stage 2：证据筛选与结构化分析（内容理解）
  从 DB 读取候选 → 正文抓取 → 去重 → top-k → LLM 相关性 → MediaNode → prepared_analysis

Brief：跨来源综合
  prepared_analysis → BriefNode → brief_data → Markdown + HTML Dashboard
```

**Stage 1 预筛规则（NewsNow / RSS，本地、无 LLM）：**

- 标题命中任意 **core** → 保留
- 标题命中至少两个 **support** → 保留
- RSS 摘要命中任意 core → 保留

**微博特殊路径：** Stage 1 保存前用 LLM 做相关性复核（失败时回退 core/support 本地规则）；通过后直接以 `content_ready` 写入 DB，Stage 2 不再重复复核。

**正文去重（普通文章）：**

- SHA-256（去空白后）相同 → 重复
- 前 12,000 字符 `SequenceMatcher.ratio() ≥ 0.92` → 高度相似

### 2.5 工程与存储层

**PostgreSQL 三表模型**（`knowledge/models.py`，Alembic 迁移）：

| 表 | 用途 |
|----|------|
| `events` | Case / Run 元信息、检索计划、状态、报告、`prepared_analysis` |
| `documents` | 新闻原文（`raw_content`、`content_hash`、抓取状态） |
| `event_documents` | Run 与文档关联、发现元信息、分析状态、相关性分数 |

**Case / Run 层级：**

- 一次用户提问创建一个 **Case**（`event_type=case`）。
- 每次检索执行对应一个 **Run**（`event_type=run`，`parent_event_id` 指向 Case）。
- 支持同一 Case 下追加来源、聚合多个 Run 的 `prepared_analysis` 后统一生成简报。
- `query_fingerprint` 支持历史 Case 查找与复用。

**并发与容错：**

- 后台研究线程池 `max_workers=2`。
- 服务重启后，`running` 状态任务标记为失败；`waiting_for_review` / 已完成任务可继续查询。
- 任务支持 `cancel`；单 Provider 失败不阻断其他来源。

---

## 3. 端到端流程

### 3.1 主流程（一次完整研究）

```text
用户输入问题
    ↓
QueryPlanNode（topic / core / support / tavily / weibo）
    ↓  [可选] Human-in-the-loop 审核 Tavily Query
MediaDiscovery（NewsNow → RSS → Tavily → 微博）
    ↓
RetrievalCheckNode → [不足时] AdaptiveRetrievalNode → 补搜一次
    ↓
微博保存前 LLM 相关性复核
    ↓
Stage 1 入库 PostgreSQL（raw_candidates 快照）
    ↓
Stage 2：正文读取 → 去重 → top-k → LLM 相关性 → MediaNode
    ↓
prepared_analysis 持久化（status = analysis_ready）
    ↓
BriefNode：LLM 输出 JSON → Pydantic 校验 → 模板渲染
    ↓
brief_data + Markdown + HTML Dashboard（status = completed）
```

### 3.2 Reflection Loop（自适应检索）

```text
首轮媒体检索
        ↓
RetrievalCheckNode
        ↓
判断有效结果是否达到阈值
   ├─ 达到阈值 → 继续后续流程
   └─ 未达到阈值（当前主要为微博 < 2 条）
          ↓
   AdaptiveRetrievalNode
          ↓
      生成补充 Query
          ↓
      Provider 补搜一次
          ↓
      合并首轮和补充结果
```

**当前实现细节（与早期设计差异）：**

- 配置阈值：Tavily ≥ 3、微博 ≥ 2（`config/media_sources.yaml`）。
- **Tavily 自适应补搜当前关闭**：`RetrievalCheckNode` 对 Tavily 固定 `adaptive_triggered=False`，原因 `single_rewrite_only`（首轮 Query 已在 `QueryPlanNode` 中做 anchor 校验，不再触发 LLM 补充改写）。
- **微博**仍会在有效结果不足时触发一轮 `AdaptiveRetrievalNode` 补搜。
- 人工补搜：`POST /runs/{id}/rerun` 修改 Tavily Query；`POST /runs/{id}/sources` 追加数据源。

---

## 4. 报告结构化（已实现）

早期版本为 Prompt 直出 Markdown；**当前已改为**：

```text
MediaNode 结构化 insights
    ↓
BriefNode 调用 LLM 输出 brief_data JSON
    ↓
Pydantic 校验 + normalize（tools/brief_models.py）
    ↓
render_brief_markdown() 固定模板渲染
    ↓
同一份 brief_data 驱动 Markdown 与 HTML Dashboard
```

**固定字段结构：**

- `executive_summary`：核心摘要
- `timeline`：事件时间线
- `key_metrics`：关键数据
- `official` / `media.domestic` / `media.overseas` / `public_opinion`：分来源主题卡片
- `synthesis`：共识 / 差异 / 风险 / 后续观察
- `sources`：来源目录（程序侧注入，不信任 LLM 编造 URL）

---

## 5. Query 优化策略

### 5.1 Query 规划（QueryPlanNode）

一次 LLM 调用，输出：

| 字段 | 数量限制 | 用途 |
|------|----------|------|
| `topic` | 1 | 研究主题 |
| `newsnow_rss_core` | ≤3 | NewsNow/RSS 强相关预筛词 |
| `newsnow_rss_support` | ≤6 | NewsNow/RSS 弱相关预筛词 |
| `tavily_queries` | **1** | 网页搜索（带 anchor 校验，防止偏离原问题） |
| `weibo_query` | 1 | 微博搜索词（偏平台口语化表达） |

### 5.2 分平台 Query 设计思路

- **Tavily**：事件主体、不同新闻表述、市场影响、机构观点；每条 Query 走「定向可信域名 + 通用搜索」。
- **微博**：事件主体、核心动作、平台常用表达；去除过度标题化限定。
- **NewsNow / RSS**：不生成独立 Query，用 core/support 在本地匹配热榜标题与 RSS 摘要。

### 5.3 Human-in-the-loop

- 前端默认自动批准并开始；展开「高级」可暂停审核 Tavily Query。
- 报告生成后若覆盖不足：可修改 Tavily Query 补搜、追加数据源、或基于已有 `prepared_analysis` 重新生成简报（`report_stale` 标记）。

---

## 6. 离线简报覆盖评测

评测是**后端开发工作**，不接入 FastAPI、不在前端展示、不写入业务库。

### 6.1 评什么

用导师金标 `reference.md` 对照库里已生成简报的 `report_data`。写简报仍用 `deepseek-chat`；打分用 `deepseek-v4-flash`。

入口：

```bash
python3 scripts/eval_reference_coverage.py --case-key case1
```

### 6.2 流程

```text
reference.md 按金标结构切段（境内总览 / 一是…五是 / 境外 / 自媒体等）
    ↓
简报 report_data 按字段展开为可检索单元
（摘要、官方、境内外媒体、舆情、时间线、指标、综合研判）
    ↓
Qwen embedding 余弦召回每段简报 Top3
    ↓
Judge 对「该段 reference × Top3」打四维分（各 0–25，合计 100）
每段独立 3 次，四维分别取平均
    ↓
写入同目录 coverage.md（不入库）
```

**四维：** 相关性 / 准确性 / 完整性 / 有用性。看核心意思，不抠字眼，不要求同一家媒体。没写到的不算准确性错误。

**段判定标签：** 总分 ≥70 命中，≥45 部分，否则弱覆盖。  
**综合得分：** 各段总分算术平均。

提示词：`evaluation/prompts.py`（`REFERENCE_SECTION_JUDGE_PROMPT`）。  
脚本：`scripts/eval_reference_coverage.py`。

`evaluation/snapshot.py` 仅导出检索正文快照（CLI `--evaluation-case`），不参与打分。

## 7. 前端能力（任务一）

启动：`financial-single-agent-api` → 浏览器访问 `http://127.0.0.1:8000/`

| 功能 | 说明 |
|------|------|
| 数据源选择 | NewsNow / RSS / Tavily / 微博（可多选） |
| Query 审核 | 可选暂停，修改 Tavily Query 后批准 |
| 进度轮询 | 2s 间隔，展示各阶段进度文案 |
| 报告展示 | Markdown 渲染 + HTML Dashboard（`/report/view`） |
| 补充检索 | 修改 Tavily Query 重跑 / 追加数据源 |
| 案例问答 | 基于 Case 内简报、结构化 insights 与原文问答 |

---

## 8. 数据源配置摘要

配置文件：`config/media_sources.yaml`

| 来源 | 规模 | 默认状态 |
|------|------|----------|
| NewsNow | 12 个平台热榜 | 启用 |
| RSS | 11 个 Feed（官方 3 + 财经媒体 8） | 启用，需 RSSHub |
| Tavily | 1 Query × 2 轮 ×（定向+通用） | 启用，需 API Key |
| 微博 | 最多 5 页 × 30 帖 | 默认关闭，需 Cookie |

**筛选参数：**

- `candidate_limit: 100`
- `max_per_source: 3`（微博按 `target_posts × max_search_pages` 放宽）
- `content_relevance_top_k: 5`
- `relevance_model_min_score: 30`

---

## 9. 案例测试建议

金标目录：`data/evaluation_cases/case_N/`

每个案例：

- `reference.md`：人工参考报告
- `coverage.md`：离线覆盖评测输出（脚本生成，不入库）

运行评测：`python3 scripts/eval_reference_coverage.py --case-key case1`

**测试时建议记录：**

1. 各 Provider 成功/失败来源与候选数量
2. `retrieval_reflection.json` 中的自适应补搜轨迹（若有快照）
3. 覆盖评测四维分，尤其是完整性偏低的段落
4. 典型失败模式：正文抓取失败、相关性误杀、时间线缺失、来源引用断裂

---

## 10. 待改进（当前认知）

| 优先级 | 方向 | 说明 |
|--------|------|------|
| P1 | 历史事件多轮检索 | 针对历史事件做「提问—检索—事件抽取」多轮扩充，补全 Timeline；控制 2–3 轮，避免按月暴力搜索 |
| P1 | Tavily 自适应策略 | 当前 Tavily 补搜关闭，可评估是否恢复或改为 Query 规划阶段多 Query |
| P2 | 社交媒体扩展 | 接入微信公众号 / 微信文章 |
| P2 | Event Graph | 基于 Timeline + 结构化 facts 构建事件知识图谱 |
| P3 | 向量检索 | 案例问答目前为关键词 + LLM rerank，可引入 embedding 提升召回 |

---

## 11. 关键代码索引

| 模块 | 路径 |
|------|------|
| 流程编排 | `agent.py` |
| API 入口 | `api.py` |
| 持久化 | `run_repository.py`、`knowledge/models.py` |
| Query 规划 | `nodes/query_plan_node.py` |
| 自适应检索 | `nodes/retrieval_reflection_node.py`、`tools/media_discovery.py` |
| 正文相关性 | `nodes/candidate_filter_node.py`、`tools/text_chunking.py` |
| 结构化抽取 | `nodes/media_node.py` |
| 简报生成 | `nodes/brief_node.py`、`tools/brief_models.py` |
| 数据源 | `tools/newsnow_provider.py`、`rss_provider.py`、`tavily_provider.py`、`weibo_provider.py` |
| 正文读取 | `tools/web_reader.py` |
| 评测 | `scripts/eval_reference_coverage.py`、`evaluation/prompts.py` |
| 前端 | `web/index.html`、`web/app.js` |
| 媒体配置 | `config/media_sources.yaml` |

---

## 12. 与旧版总结的主要差异（更新清单）

若你之前写过一版项目总结，以下内容需要同步修改：

1. **存储**：SQLite → **PostgreSQL 三表**（`events` / `documents` / `event_documents`）。
2. **报告生成**：Prompt 直出 Markdown → **LLM JSON → Pydantic → 模板渲染**（`brief_data` + HTML Dashboard）。
3. **任务模型**：单一 `run_id` → **Case + Run 层级**，支持追加来源与 Case 级聚合简报。
4. **Stage 解耦**：新增 `analysis_ready` 状态；`resume-analysis` 可从 DB 断点恢复 Stage 2；`brief` 可单独触发。
5. **Tavily Query**：首轮最多 2 条 → **当前固定 1 条**（带 anchor 校验）；Tavily 自适应补搜**当前关闭**。
6. **RSS 时间窗**：15 天 → **60 天**（配置可调）。
7. **微博参数**：3 页 × 20 帖 → **5 页 × 30 帖**；评论抓取配置为启用（Provider 本身默认关闭）。
8. **前端扩展**：新增案例查找、追加来源、案例问答；评测不进前端，走离线脚本。

---

## 13. 面试高频追问（自检用）

准备时确保能答出「为什么这样设计」而不只是「做了什么」：

**架构类**

- 为什么把 Stage 1 和 Stage 2 拆开？边界放在哪？
- Case 和 Run 为什么要分层？追加来源时如何避免重复分析？
- 为什么用 PostgreSQL 而不是 SQLite / 向量库？

**检索类**

- NewsNow/RSS 为什么用本地规则预筛，而微博用 LLM 预筛？
- Tavily 为什么要「定向域名 + 通用搜索」两轮？为什么不直接用搜索摘要当正文？
- Reflection Loop 的停止条件是什么？Tavily 为什么关闭了自适应补搜？

**生成类**

- BriefNode 为什么不直接收正文，只收 MediaNode 的结构化 insights？
- `brief_data` 里 sources 为什么由程序注入而不是信任 LLM？
- 正文相关性为什么要先 top-k 再 LLM，而不是全文直接送 LLM？

**评测类**

- 为什么按 reference 段落召回简报 Top3，而不是对整篇简报一次性打分？
- 四维里完整性和相关性为什么可能差很多？说明简报什么问题？
- 为什么评测用 flash、写简报用 chat，并且要打 3 次取平均？

**工程类**

- 单 Provider 失败为什么不 fail-fast？
- 服务重启后 running 任务如何处理？
- 微博反爬 / Cookie 失效时如何保证主流程可用？

---

*最后更新：2026-08-17，对齐当前 `My_agent` 主分支实现。*
