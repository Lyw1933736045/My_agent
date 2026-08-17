# NewsNow + RSS：从检索规划到 `analysis_ready`

## 1. 适用范围

本文记录当前代码中 NewsNow、RSS 从查询规划、候选抓取、Stage 1 持久化，到正文复核和 MediaNode 结构化分析的实际流程。终点是 `analysis_ready`，不包含 BriefNode 和最终简报生成。

追加来源入口：`POST /api/v1/runs/{run_id}/sources`

主要调用链：

```text
QueryPlanNode 动态生成 newsnow_rss_core / newsnow_rss_support
    ↓
NewsNow、RSS 抓取当前启用来源
    ↓
基础 URL、标题、时间校验
    ↓
标题/摘要本地轻量相关性预筛
    ↓
规范化 URL、同 URL 合并
    ↓
Stage 1 候选写入 PostgreSQL
    ↓
按 run_id 从 PostgreSQL 重新加载全部候选
    ↓
读取完整正文
    ↓
完整正文去重
    ↓
选择正文 top-k 片段
    ↓
LLM 正文相关性复核
    ↓
accepted 正文进入 MediaNode
    ↓
完整正文、accepted/rejected 结果写入 PostgreSQL
    ↓
MediaNode 结果保存为 prepared_analysis
    ↓
run.status = analysis_ready
```

总编排位置：`api.py:755-812`。

## 2. Query Planner

`QueryPlanNode` 根据本次用户输入动态生成：

- `topic`
- `newsnow_rss_core`：1～3 个核心事件定位词
- `newsnow_rss_support`：3～6 个辅助上下文词
- `tavily_queries`
- `weibo_query`

NewsNow 和 RSS 只共用 `newsnow_rss_core`、`newsnow_rss_support`。代码中没有固定业务事件词表。

生成后执行去空白、去重和数量截断；`newsnow_rss_core` 为空会直接报错。

相关代码：

- Prompt：`prompts/prompts.py:3-39`
- 解析与校验：`nodes/query_plan_node.py:12-96`
- 写入 RunState：`agent.py:205-222`

## 3. Provider 抓取

### 3.1 NewsNow

NewsNow 不使用关键词调用搜索接口。它依次请求配置中所有已启用的热榜来源，再在本地筛选返回标题。

基础校验包括：

- title 必须为非空字符串
- URL 必须是 HTTPS
- URL 域名必须等于或隶属于来源配置的 `expected_domain`

每个来源按配置执行请求间隔和失败重试。NewsNow 返回项没有发布时间，因此 RSS 的 `max_age_days` 不会限制 NewsNow。

相关代码：

- Provider 构建：`agent.py:92-119`
- API、重试、域名校验：`tools/newsnow_provider.py`
- 来源配置：`config/media_sources.yaml:9-78`

### 3.2 RSS

RSS 依次请求配置中所有已启用 feed，解析 RSS XML 或 Atom，读取：

- title
- link
- published / updated / pubDate
- description / summary / content，转为纯文本 snippet
- guid / id

基础校验要求标题非空、URL 为 HTTP(S)。时间有效时按 feed 自身的 `max_age_days` 或 RSS 全局值过滤；当前全局配置是 60 天。无法解析发布时间的候选不会因时间字段缺失被删除。

每个 feed 按配置执行请求间隔和最多一次重试。RSSHub 地址在 Provider 构建前由配置解析。

相关代码：

- Provider 构建：`agent.py:121-146`
- XML/Atom 解析、时间过滤和重试：`tools/rss_provider.py`
- feed 配置：`config/media_sources.yaml:80-229`

## 4. Stage 1 本地相关性预筛

该层不调用 LLM、不读取网页正文、不使用 embedding，只做规范化后的 substring 匹配。

`normalize_match_text()` 执行：

- Unicode NFKC 规范化
- 英文大小写归一
- 中文、英文标点替换为空格
- 连续空白合并
- 去除前后空格

### NewsNow 保留规则

1. title 命中任意 `newsnow_rss_core`；或
2. title 命中至少两个不同的 `newsnow_rss_support`。

### RSS 保留规则

1. title 命中任意 `newsnow_rss_core`；或
2. title 命中至少两个不同的 `newsnow_rss_support`；或
3. title 未通过时，snippet 命中任意 `newsnow_rss_core`。

其他候选不会进入 Stage 1 数据库持久化。

相关代码：

- 统一匹配函数：`tools/media_relevance.py:9-49`
- 在 Provider 返回后统一调用：`tools/media_discovery.py:50-88`

## 5. URL 合并与 Stage 1 持久化

通过本地预筛和时间过滤后，候选按 `canonical_url()` 合并。规范化会统一 HTTP(S) scheme、主机名、路径尾部斜杠，并删除常见追踪参数；相同规范化 URL 只保留一条，同时合并来源、snippet 和 appearances。

当前 Stage 1 保存的是 `DiscoveryResult.raw_candidates`，即本地预筛后、时间有效、按规范化 URL 合并的全部候选。`candidate_limit` 和 `max_per_source` 参与旧的 `selected` 列表计算，但不截断当前 Stage 1 持久化集合。

写入 PostgreSQL：

### documents

- `canonical_url`
- `url`
- `title`
- `publisher`
- `source_type`
- `published_at`
- 初始 `fetch_status = pending`

### event_documents

- `event_id = run_id`
- `document_id`
- `snippet`
- `discovery.providers`
- `discovery.queries`
- `discovery.appearances`
- 初始 `analysis_status = pending`

`documents.canonical_url` 全局唯一；`event_documents(event_id, document_id)` 也唯一。因此同一 URL 再次追加到同一个 case 时不会新增关系，而是合并 provider、query、appearances 和较长 snippet。

相关代码：

- 时间过滤和 Stage 1 URL 合并：`tools/media_discovery.py:224-295`
- URL 规范化：`utils/dedup.py:17-42`
- Stage 1 转数据库字段：`api.py:165-233`
- 数据库去重与合并：`run_repository.py:380-480`
- 表结构：`knowledge/models.py:20-94`

## 6. PostgreSQL 是 Stage 1 → Stage 2 边界

Stage 1 保存完成后，代码调用 `RunRepository.list_candidates(run_id)`，重新加载当前 run 的全部数据库候选，不继续依赖 Provider 的临时内存列表。

因此：

- 纯 NewsNow + RSS run：读取该 run 的全部 NewsNow/RSS 候选。
- 向已有 case 追加 NewsNow/RSS：当前实现会重新加载该 run 已保存的所有来源候选，不仅限于本次新增的 NewsNow/RSS URL。

相关代码：`api.py:236-278、780-786`。

## 7. 正文读取

对数据库候选逐条调用 `WebReader.read(url)`：

1. 使用系统默认网络环境请求。
2. 默认请求失败后，关闭代理直接重试一次。
3. 仅接受 HTML、XHTML 和纯文本。
4. HTML 只提取可见文本，忽略 script、style、noscript、svg 等标签。
5. 响应字节数和正文长度使用 Settings 中的上限。

该读取器只读取单个 URL 返回的页面，不执行站内翻页，也不继续抓取文章中的下一页链接。单篇失败只跳过，不立即终止整个任务。

相关代码：

- 调用：`agent.py:451-500`
- 网页读取器：`tools/web_reader.py`

## 8. 完整正文去重

正文成功读取后，普通文章按完整正文去重：

1. 删除空白后计算 SHA-256，哈希相同视为重复；
2. 哈希不同，再比较正文前 12,000 个字符；`SequenceMatcher.ratio() >= 0.92` 视为高度相似。

重复正文不进入 top-k、LLM 正文复核和 MediaNode。当前 `prepare_analysis()` 只从内存列表删除重复项，没有在此处把对应数据库关系更新成 `duplicate`。

相关代码：`agent.py:506-528`。

## 9. Chunk、top-k 与 LLM 正文复核

每篇完整正文先按以下参数切分：

- `chunk_size = 1500`
- `overlap = 200`
- 尽量在换行、句号、问号、分号等边界结束

每个 chunk 使用轻量分数排序：

- 完整 topic 命中：`+10`
- 每个 core 命中：`+10`
- 每个 support 命中：`+3`

当前最多选择 5 个 chunk，并按原文顺序重新拼接。没有任何词命中时，使用首段、中段、尾段兜底，因此 top-k 不直接决定文章是否相关。

拼接结果交给 `CandidateFilterNode`。当前配置最多向 LLM 提供每篇 5,000 字符，模型返回 `relevant`、`score`、`reason`；只有 `relevant = true` 且分数达到当前配置阈值 30 才标记为 accepted，否则为 rejected。

相关代码：

- Chunk 和 top-k：`tools/text_chunking.py:11-127`
- 调用：`agent.py:475-489、530-584`
- LLM 判断：`nodes/candidate_filter_node.py`
- Prompt：`prompts/prompts.py:122-132`
- 当前参数：`config/media_sources.yaml:275-281`

## 10. MediaNode

只有 accepted 正文进入 MediaNode。MediaNode 不生成最终简报，而是逐篇提取结构化信息：

- reported_facts
- interpretations
- affected_parties
- risks_or_disagreements
- statistics
- named_views
- timeline_events

MediaNode 使用完整 `raw_content`，不是只使用相关性复核的 top-k：

- 正文不超过 12,000 字符：一次分析完整正文；
- 正文超过 12,000 字符：按 5,000 字符、300 字符重叠分块分析，再按字段合并去重。

相关代码：

- 调用：`agent.py:570-590`
- 分块、提取与合并：`nodes/media_node.py`
- Prompt：`prompts/prompts.py:103-119`

## 11. 正文及分析结果写入 PostgreSQL

当前顺序是 MediaNode 成功后才执行 `_save_completed_documents()`。

对完整正文去重后且读取成功的每篇文章，不论 accepted 还是 rejected，都会保存：

### documents

- `raw_content`：完整正文，不保存 top-k 派生片段
- `content_hash`
- `final_url`
- `content_type`
- `fetched_at`
- `fetch_status = fetched`

### event_documents

- `analysis_status = accepted | rejected`
- `analysis_reason`
- `relevance_score`
- accepted 时 `selected_for_report = true`

写入前 `_clean_text()` 会删除 PostgreSQL TEXT 不允许的 NUL 字符 `\x00`。单篇写库异常会记录警告并继续保存其他正文。

当前行为需要注意：

- 正文读取失败的候选在 `prepare_analysis()` 中被跳过，此路径没有同步写入 `fetch_error`。
- 被完整正文去重移除的候选，此路径没有同步写入 `analysis_status = duplicate`。
- 如果正文复核或 MediaNode 整体失败，Stage 1 候选仍已保存，但 `_save_completed_documents()` 尚未执行，因此本轮正文和复核结果不会写入数据库。

相关代码：

- 正文与判断写库：`api.py:281-313、786-787`
- NUL 清洗与正文更新：`run_repository.py:30-38、503-530`

## 12. `prepared_analysis` 与 `analysis_ready`

正文、复核和 MediaNode 全部完成后，API 将以下内容写入 `events.metadata.prepared_analysis`：

```json
{
  "query": "",
  "topic": "",
  "read_attempted_count": 0,
  "read_success_count": 0,
  "relevant_count": 0,
  "media_insights": [],
  "social_insights": []
}
```

随后：

- `events.status = analysis_ready`
- 当前检索 execution 在 `search_history` 中标记 completed
- 已存在的旧报告标记为 `report_stale`
- 不调用 BriefNode，不生成新 Markdown 或 Dashboard

相关代码：

- 组装 `prepared_analysis`：`api.py:788-807`
- 保存和状态更新：`run_repository.py:232-267`

## 13. 后续生成简报

用户调用 `POST /api/v1/runs/{run_id}/brief` 后，代码直接从 PostgreSQL 的 `events.metadata.prepared_analysis` 恢复 MediaInsight，再调用 BriefNode。无需重新执行 NewsNow/RSS 抓取、正文读取、正文相关性复核或 MediaNode。

相关代码：

- API：`api.py:986-999`
- 从数据库恢复并调用 BriefNode：`api.py:815-835`
- BriefNode 入口：`agent.py:593-622`

## 备注 1：正文 NUL 字符导致 PostgreSQL 入库失败

部分网页解析结果可能混入不可见的 NUL 字符 `\x00`（`0x00`）。Python 可以保留该字符，但 PostgreSQL 的 TEXT/VARCHAR 不允许写入，因此曾出现单篇正文导致整次持久化失败的问题。

当前处理方式：

- `raw_content` 入库前统一执行 `replace("\x00", "")`；
- 使用清洗后的正文计算 `content_hash`；
- 单篇正文入库异常时记录警告并继续保存其他文章，避免整个 run 失败。

实现位置：`run_repository.py:30-38、503-530`，`api.py:281-313`。

当前清洗主要覆盖正文和错误信息；若后续需要全面防御，可再将 title、snippet、publisher 等外部文本字段统一接入同一清洗函数。
