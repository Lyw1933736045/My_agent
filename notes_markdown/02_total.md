# 从原文到简报：完整处理流程

本文按当前代码整理从候选发现、正文处理到最终简报生成的 1—9 个环节。

## 1. Stage 1：候选发现与保存前筛选

Query Planner 根据用户输入动态生成：

- `topic`
- `newsnow_rss_core`
- `newsnow_rss_support`
- `tavily_queries`
- `weibo_query`

NewsNow、RSS、Tavily 和微博随后执行各自的 Provider 抓取。

NewsNow/RSS 在 Stage 1 使用本地轻量相关性筛选：

- 标题命中任意 core，保留；
- 标题命中至少两个 support，保留；
- RSS 摘要命中任意 core，也保留；
- 其他候选丢弃。

微博的正常路径在保存前使用 LLM 判断相关性；LLM 失败时使用 core/support 本地宽松规则。只有通过的微博才会继续抓取评论。

通过筛选的候选按规范化 URL 去重后写入 PostgreSQL。Stage 1 保存的是预筛后的 `raw_candidates`，不是旧的 `selected` 限额列表。

代码位置：

- Query Planner：`nodes/query_plan_node.py:12-96`
- Query Prompt：`prompts/prompts.py:3-39`
- Provider 编排：`agent.py:224-287`
- NewsNow：`tools/newsnow_provider.py`
- RSS：`tools/rss_provider.py`
- 本地预筛：`tools/media_relevance.py:9-49`
- 多 Provider 协调：`tools/media_discovery.py:50-88`
- Stage 1 入库：`api.py:165-233`
- 数据库去重：`run_repository.py:380-480`

## 2. Stage 2：从 PostgreSQL 读取候选

Stage 2 以 PostgreSQL 为边界，根据 `run_id` 重新读取候选，不依赖 Stage 1 的临时内存列表。

读取内容包括：

- 标题、URL、来源；
- 搜索摘要；
- provider 和 query；
- `analysis_status`；
- 已保存的正文；
- 微博评论和互动数据。

纯 NewsNow/RSS/Tavily run 会读取该 run 的全部候选。向已有 case 追加来源时，当前实现也会重新读取该 run 已保存的全部候选。

代码位置：

- 数据库读取：`run_repository.py:531-580`
- 候选恢复：`api.py:236-278`
- 追加执行调用：`api.py:755-786`

Stage 2 断点恢复时使用新增入口：

```http
POST /api/v1/runs/{run_id}/resume-analysis
```

该入口不重新抓取 Provider，只处理数据库中的未完成候选。

代码位置：`api.py:890-1079、1257-1269`。

## 3. 正文读取

NewsNow、RSS、Tavily 候选逐条调用 `WebReader.read(url)`：

1. 先使用系统默认网络环境；
2. 默认请求失败后关闭代理直接重试；
3. 只接受 HTML、XHTML 和纯文本；
4. HTML 只提取可见文字，忽略脚本、样式等标签；
5. 响应字节数和正文长度受 Settings 配置限制。

读取失败的文章记录 `fetch_failed`，不会进入后续正文分析。

微博不调用 WebReader。微博正文在搜索阶段已作为 `raw_content` 保存，Stage 2 直接复用。

代码位置：

- 网页读取：`tools/web_reader.py`
- 普通文章调用：`agent.py:451-500`
- 断点恢复读取：`api.py:935-1005`
- 微博 `content_ready` 恢复：`api.py:831-871`

## 4. 完整正文去重

普通文章在正文读取成功后按完整正文去重：

1. 删除空白后计算 SHA-256；
2. 哈希相同，视为重复；
3. 哈希不同但前 12,000 字符 `SequenceMatcher.ratio() >= 0.92`，也视为高度相似。

重复候选不再进入 top-k、正文 LLM 复核和 MediaNode，并记录为 `duplicate`。

微博属于社交材料，当前不做普通文章式正文相似度去重。

代码位置：

- 正常流程去重：`agent.py:506-528`
- 断点恢复去重：`api.py:974-994`
- 相似度计算：`api.py:886-887`

## 5. Chunk 与 top-k

长正文会切分为多个片段：

- `chunk_size = 1500`
- `overlap = 200`
- 优先在换行、句号、问号、分号等自然边界切分

每个片段根据以下规则做轻量排序：

- topic 命中：`+10`
- 每个 core 命中：`+10`
- 每个 support 命中：`+3`

最多选择 `content_relevance_top_k` 个片段，当前配置为 5 个，再按原文顺序拼接。

没有任何关键词命中时，使用首段、中段、尾段作为召回兜底。

top-k 只用于正文相关性复核，不是最终简报的全部输入。MediaNode 后续仍优先使用完整正文。

代码位置：

- 切分和选择：`tools/text_chunking.py:11-127`
- 正常流程调用：`agent.py:475-489`
- 断点恢复调用：`api.py:1007-1022`
- 参数配置：`config/media_sources.yaml:275-281`

## 6. LLM 正文相关性复核

CandidateFilterNode 接收：

- 文章标题和来源；
- topic；
- `newsnow_rss_core`；
- `newsnow_rss_support`；
- top-k 正文片段。

模型返回：

- `relevant`
- `score`
- `reason`

当前保留条件是：

```text
relevant = true 且 score >= 30
```

通过的文章写为 `accepted`，未通过的文章写为 `rejected`。判断结果写入 `event_documents`：

- `analysis_status`
- `analysis_reason`
- `relevance_score`
- `selected_for_report`

微博如果在 Stage 1 已经通过保存前相关性复核，Stage 2 会复用原判断，不再次调用正文相关性 LLM。

代码位置：

- 判断节点：`nodes/candidate_filter_node.py`
- Prompt：`prompts/prompts.py:123-132`
- 正常流程：`agent.py:530-584`
- 断点恢复流程：`api.py:1024-1047`
- 微博保存前复核：`agent.py:302-368`

## 7. MediaNode 结构化抽取

只有 accepted 内容进入 MediaNode。MediaNode 逐篇提取：

- `reported_facts`
- `interpretations`
- `affected_parties`
- `risks_or_disagreements`
- `statistics`
- `named_views`
- `timeline_events`

输入正文规则：

- 普通文章不超过 12,000 字符：一次分析完整正文；
- 超过 12,000 字符：按 5,000 字符、300 字符重叠分块；
- 微博：直接使用帖子正文、评论和互动数据。

每篇文章产生一个 `MediaInsight`。多个分块的抽取结果会在文章内部合并去重。

代码位置：

- MediaNode：`nodes/media_node.py:27-126`
- Media Prompt：`prompts/prompts.py:98-121`
- 正常流程调用：`agent.py:588-592`
- 断点恢复调用：`api.py:1049-1052`

## 8. 保存正文和 `prepared_analysis`

正文保存到 `documents`：

- `raw_content`
- `content_hash`
- `final_url`
- `content_type`
- `fetched_at`
- `fetch_status`

正文入库前会清除 NUL 字符 `\x00`，避免 PostgreSQL TEXT 写入失败。

逐篇相关性状态保存到 `event_documents`。

MediaNode 结果保存到：

```text
events.metadata.prepared_analysis
```

主要字段：

```json
{
  "read_attempted_count": 0,
  "read_success_count": 0,
  "relevant_count": 0,
  "media_insights": [],
  "social_insights": []
}
```

全部结构化分析完成后：

- `events.status = analysis_ready`
- 不执行 BriefNode；
- 不生成新的 Markdown 或 HTML。

当前标准追加路径是在 MediaNode 完成后保存正文和分析结果。断点恢复路径会在每篇正文读取成功后先写入正文，最后再写入 `prepared_analysis`。

代码位置：

- 正文保存：`api.py:281-313`
- NUL 清洗：`run_repository.py:30-38、503-530`
- `prepared_analysis` 保存：`run_repository.py:296-328`
- 表结构：`knowledge/models.py:20-94`

## 9. BriefNode 与最终简报

当 run 已达到 `analysis_ready`，调用：

```http
POST /api/v1/runs/{run_id}/brief
```

BriefNode 直接读取数据库中的 `prepared_analysis`，不重新抓取网页、不重新做正文相关性判断，也不重新运行 MediaNode。

BriefNode 跨来源综合：

- 官方层面；
- 境内媒体；
- 境外及港澳媒体；
- 社会舆论；
- 综合研判。

输出结构化 `brief_data`，再由固定模板生成 Markdown。HTML Dashboard 与 Markdown 使用同一份 `brief_data`，展示核心摘要、时间线、主题卡片、来源和综合研判。

代码位置：

- Brief API：`api.py:1274-1285`
- 从数据库恢复分析：`api.py:1082-1100`
- BriefNode 调用：`agent.py:594-622`
- BriefNode：`nodes/brief_node.py`
- Markdown 模板和 HTML 页面：`api.py` 报告渲染相关函数、`web/`

## 总流程

```text
候选抓取
→ Stage 1 保存前筛选
→ PostgreSQL
→ 数据库读取
→ 完整正文读取
→ 正文去重
→ top-k
→ LLM 正文相关性复核
→ MediaNode 结构化抽取
→ prepared_analysis
→ BriefNode 跨来源综合
→ brief_data
→ Markdown + HTML Dashboard
```
