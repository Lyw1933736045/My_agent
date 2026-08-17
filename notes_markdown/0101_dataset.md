# 四数据源接入说明（NewsNow / RSS / Tavily / 微博）

> 按「数据源是什么 → 怎么接入 → 请求什么 → 返回什么 → 怎么标准化 → 怎么筛选 → 失败怎么办 → 为什么需要它」八个维度整理。  
> 代码路径均相对于 `My_agent/` 根目录。

---

## 0. 总览对比

| 维度 | NewsNow | RSS | Tavily | 微博 |
|------|---------|-----|--------|------|
| **本质** | 第三方热榜聚合 API | RSS/Atom Feed | 搜索 API（Tavily SDK） | 网页爬虫（桌面搜索页 HTML 解析） |
| **接入方式** | `urllib` HTTP GET + JSON | `requests` + `ElementTree` XML | `tavily.TavilyClient` | `requests` + `HTMLParser` |
| **查询输入** | 无 query（按 source id 拉全榜） | 无 query（按 Feed URL 拉全量） | 自然语言 query + 域名定向 | 关键词 `q` + 分页 |
| **正文来源** | Stage 2 `WebReader` 重抓 | Stage 2 `WebReader` 重抓 | Stage 2 `WebReader` 重抓 | 搜索页已解析帖子正文（`content_ready`） |
| **独特价值** | 实时热点、多平台榜单 | 官方/媒体最新稿、带发布时间 | 广域网页发现、定向财经域名 | 社会舆论、互动数据、口语化表达 |

**编排入口：** `agent.py` → `_build_providers()` → `MediaDiscovery.run()`  
**配置：** `config/media_sources.yaml`  
**统一模型：** `tools/media_models.py`

---

## 1. 系统内部统一结构

### 1.1 Stage 1：`MediaCandidate`

发现阶段所有 Provider 最终都映射为 `MediaCandidate`：

```python
@dataclass(frozen=True)
class MediaCandidate:
    title: str
    url: str
    source_name: str
    published_at: Optional[str]
    snippet: str = ""
    discovered_by: tuple[str, ...] = ()      # 如 ("newsnow",) / ("tavily_targeted",)
    source_group: str = "news_media"         # news_media / official_media / social_media
    query: Optional[str] = None
    guid: Optional[str] = None               # RSS guid / weibo:wid
    max_age_days: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

**代码位置：** `tools/media_models.py:8-19`

### 1.2 Stage 2：`MediaDocument`

正文读取后包装为 `MediaDocument`（`content` 为 top-k 片段，`raw_content` 为完整正文）：

**代码位置：** `tools/media_models.py:48-56`

### 1.3 持久化（PostgreSQL）

Stage 1 入库时 `MediaCandidate` 转为 `events` + `documents` + `event_documents` 三表记录：

**代码位置：**
- 表结构：`knowledge/models.py:21-111`
- Stage 1 保存：`api.py:332-400`（`_save_stage1`）
- 候选读取：`run_repository.py`（`save_candidates` / `list_candidates`）

---

## 2. NewsNow

### 2.1 数据源是什么

- **类型：** 第三方热榜聚合服务（非官方 API）
- **服务地址：** `https://newsnow.busiyi.world/api/s`
- **特点：** 按平台 source id 返回当前热榜 JSON，**不支持关键词搜索**
- **已配置平台：** 12 个（财经媒体 9 + 社交热榜 3）

**代码位置：**
- Provider：`tools/newsnow_provider.py`
- 平台列表：`config/media_sources.yaml:19-78`

### 2.2 怎么接入

```text
urllib.request.urlopen
  → GET {api_url}?id={source_id}&latest
  → JSON 解析
  → 校验 status ∈ {success, cache}
```

- 无官方 SDK
- 每个平台独立请求，请求间隔 0.5s
- 失败最多重试 1 次（随机等待 2–3s）

**代码位置：**
- HTTP 请求：`tools/newsnow_provider.py:49-81`（`fetch_json`）
- 重试逻辑：`tools/newsnow_provider.py:94-110`（`_fetch_with_retry`）
- 串行拉取：`tools/newsnow_provider.py:112-159`（`search`）
- Agent 注册：`agent.py:94-104`

### 2.3 请求什么

| 参数 | 值 | 说明 |
|------|-----|------|
| `id` | source id，如 `cls-hot` | 来自 `media_sources.yaml` 的 `sources[].id` |
| `latest` | 无值 flag | 固定追加 `&latest` |
| query / 关键词 | **无** | `search(queries)` 接收的 queries 仅用于后续本地匹配，不参与 HTTP 请求 |
| 分页 | **无** | 每次返回该平台当前热榜全量 |
| 时间范围 | **无** | 无发布时间字段 |

**示例请求：**
```http
GET https://newsnow.busiyi.world/api/s?id=cls-hot&latest
```

**代码位置：** `tools/newsnow_provider.py:49-50`

### 2.4 返回什么

**API 原始 JSON：**

```json
{
  "status": "success | cache",
  "items": [
    { "title": "...", "url": "...", "mobileUrl": "..." }
  ]
}
```

**解析后保留字段：**

| 原始字段 | 映射到 MediaCandidate |
|----------|----------------------|
| `title` | `title`（空白规范化） |
| `url` / `mobileUrl` | `url`（优先 `url`） |
| — | `source_name` ← yaml 中的 `name` |
| — | `published_at` = `None` |
| — | `snippet` = `""` |
| — | `discovered_by` = `("newsnow",)` |
| — | `source_group` ← yaml 中的 `source_group` |

**额外校验：** URL 必须为 `https`，且 hostname 匹配 `expected_domain`。

**代码位置：**
- 状态校验：`tools/newsnow_provider.py:78-80`
- 字段解析：`tools/newsnow_provider.py:128-151`
- 域名安全校验：`tools/newsnow_provider.py:84-92`（`_safe_url`）

### 2.5 怎么标准化

```text
API JSON item
  → 校验 title / url / expected_domain
  → MediaCandidate(title, url, source_name, published_at=None, discovered_by=("newsnow",))
  → MediaDiscovery 时间过滤 + URL 去重
  → Stage 1 写入 PostgreSQL
```

**代码位置：**
- 候选构造：`tools/newsnow_provider.py:144-151`
- 全局去重合并：`tools/media_discovery.py:251-295`（`stage1_by_url`）
- URL 规范化：`utils/dedup.py:21-40`（`canonical_url`）

### 2.6 怎么筛选

NewsNow **不做 query 搜索**，筛选发生在拉取之后的本地预筛：

| 阶段 | 规则 | 代码位置 |
|------|------|----------|
| Provider 内 | 无 title/url/域名 → 丢弃 | `newsnow_provider.py:134-142` |
| MediaDiscovery | `is_media_candidate_relevant(title, snippet="", core, support, "newsnow")` | `media_discovery.py:69-80` |
| 本地预筛规则 | 标题命中任意 **core** → 保留 | `media_relevance.py:39-40` |
| | 标题命中 ≥2 个 **support** → 保留 | `media_relevance.py:41-45` |
| 时间过滤 | NewsNow 无 `published_at`，通常跳过 | `media_discovery.py:224-249` |
| URL 去重 | `canonical_url` 合并 `discovered_by` | `media_discovery.py:251-295` |
| 限额选择 | `select_candidates`（按相关性打分 + `max_per_source`） | `utils/dedup.py:94-151` |

**core / support 来源：** `QueryPlanNode` 输出的 `newsnow_rss_core` / `newsnow_rss_support`（`nodes/query_plan_node.py:67-68`）

### 2.7 失败怎么办

| 失败类型 | 处理方式 |
|----------|----------|
| HTTP 错误 / 超时 / 连接断开 | 重试 1 次；仍失败记入 `diagnostics.failed_sources`，**不阻断其他平台** |
| 无效 JSON | 同上 |
| `status` 非 success/cache | 同上 |
| 单平台 0 条 | 继续下一个平台 |
| 全部平台失败 | Provider 返回空列表；若所有 Provider 均空，后续 Stage 2 可能无候选 |

**代码位置：**
- 重试与诊断：`tools/newsnow_provider.py:94-110`
- MediaDiscovery 异常捕获：`tools/media_discovery.py:129-142`
- 诊断结构：`tools/media_models.py:22-26`（`ProviderDiagnostics`）

### 2.8 为什么需要它

| 对比维度 | NewsNow 的独特价值 |
|----------|-------------------|
| vs RSS | 反映**当前热榜**，不是订阅流；能抓到尚未进入 RSS 的热点 |
| vs Tavily | 零 query 成本；覆盖微博/知乎/B站等**社交平台热榜** |
| vs 微博 | 多平台横向对比；无需 Cookie；适合发现「正在发酵」的话题 |
| 局限 | 无发布时间；无正文；只能看标题；强依赖热榜 API 稳定性 |

---

## 3. RSS

### 3.1 数据源是什么

- **类型：** RSS / Atom Feed（通过 **RSSHub** 中转）
- **默认基址：** `RSSHUB_BASE`（本地 Docker `http://localhost:1200` 或远程实例）
- **特点：** 按 Feed 拉取最新文章列表，带发布时间和摘要
- **已启用 Feed：** 11 个（官方媒体 3 + 财经媒体 8）

**代码位置：**
- Provider：`tools/rss_provider.py`
- Feed 列表：`config/media_sources.yaml:93-182`
- RSSHub 自启动：`utils/rsshub_runtime.py`（API 启动时检查 Docker 容器）

### 3.2 怎么接入

```text
requests.Session.get(feed_url, stream=True)
  → 限制 max_content_bytes（默认 6MB）
  → ElementTree.fromstring(XML)
  → 遍历 item / entry 节点
```

- 无 SDK，标准 XML 解析
- 持久 Session 复用连接
- 请求间隔 0.5–1.0s（随机），失败最多重试 1 次

**代码位置：**
- HTTP 拉取：`tools/rss_provider.py:115-135`（`fetch`）
- 重试：`tools/rss_provider.py:137-151`
- XML 解析：`tools/rss_provider.py:153-199`（`parse`）
- 串行拉 Feed：`tools/rss_provider.py:201-225`（`search`）
- Feed URL 解析（`${RSSHUB_BASE}` 替换）：`utils/media_sources.py`（`resolve_feed_url`）
- Agent 注册：`agent.py:106-137`

### 3.3 请求什么

| 参数 | 值 | 说明 |
|------|-----|------|
| URL | Feed 完整地址 | 如 `${RSSHUB_BASE}/people/finance` |
| query / 关键词 | **无** | 与 NewsNow 相同，queries 仅用于后续本地匹配 |
| 分页 | **无** | 一次拉取 Feed 内全部 entry（受 `max_items` 限制） |
| 时间范围 | `max_age_days` | 全局默认 **60 天**；单 Feed 可覆盖 |
| 条数限制 | `max_items` | 全局 `default_max_items: 0`（不限制）；单 Feed 可设 |

**代码位置：**
- 全局时间窗：`config/media_sources.yaml:85`
- 解析时 cutoff：`tools/rss_provider.py:159-180`
- 条数截断：`tools/rss_provider.py:195-199`

### 3.4 返回什么

**XML 原始字段（item / entry）：**

| XML 标签 | 用途 |
|----------|------|
| `title` | 标题 |
| `link[@href]` / `link` text | URL |
| `pubDate` / `published` / `updated` / `date` | 发布时间 |
| `description` / `summary` / `content` | 摘要（HTML 去标签） |
| `guid` / `id` | 唯一标识 |

**映射到 MediaCandidate：**

| 字段 | 值 |
|------|-----|
| `title` | 去 HTML 后的标题 |
| `url` | link href |
| `source_name` | Feed `name` |
| `published_at` | ISO 8601 字符串 |
| `snippet` | description/summary/content 纯文本 |
| `discovered_by` | `("rss",)` |
| `source_group` | `official_media` 或 `news_media` |
| `guid` | guid 或 url |
| `max_age_days` | 该 Feed 有效时间窗 |

**代码位置：** `tools/rss_provider.py:171-194`

### 3.5 怎么标准化

```text
Feed XML bytes
  → 解析 item/entry
  → 时间过滤（max_age_days）
  → MediaCandidate（含 snippet + published_at + guid）
  → MediaDiscovery 二次时间过滤 + URL 去重
  → Stage 1 入库
```

**代码位置：**
- 候选构造：`tools/rss_provider.py:182-194`
- 二次时间过滤：`tools/media_discovery.py:224-249`
- URL 去重：`tools/media_discovery.py:251-295`

### 3.6 怎么筛选

| 阶段 | 规则 | 代码位置 |
|------|------|----------|
| 解析时 | 无 title / 非法 URL → 丢弃 | `rss_provider.py:173-174` |
| 解析时 | `published_at < cutoff` → 丢弃 | `rss_provider.py:178-180` |
| MediaDiscovery | 标题命中 core → 保留 | `media_relevance.py:39-40` |
| | 标题命中 ≥2 support → 保留 | `media_relevance.py:41-45` |
| | **RSS 额外：** 摘要命中任意 core → 保留 | `media_relevance.py:46-48` |
| 全局时间过滤 | 候选级 `max_age_days` 或全局 `max_age_days` | `media_discovery.py:224-249` |
| 去重 / 限额 | 同 NewsNow | `utils/dedup.py` |

### 3.7 失败怎么办

| 失败类型 | 处理方式 |
|----------|----------|
| HTTP 超时 / 4xx / 5xx | 重试 1 次；失败记入 `failed_sources` |
| 响应超 6MB | 抛错，该 Feed 失败 |
| 无效 XML | 解析失败，该 Feed 失败 |
| RSSHub 未启动 | 所有 RSS Feed 失败；**NewsNow / Tavily 继续** |
| 单 Feed 空结果 | 继续下一个 Feed |

**代码位置：**
- 大小限制：`tools/rss_provider.py:125-126`
- XML 解析异常：`tools/rss_provider.py:217-220`
- API 启动时 RSSHub 检查：`api.py` startup → `utils/rsshub_runtime.py`

### 3.8 为什么需要它

| 对比维度 | RSS 的独特价值 |
|----------|---------------|
| vs NewsNow | 有**发布时间**和**摘要**；覆盖官方媒体（人民网、中新网等） |
| vs Tavily | 稳定、可预期；无需搜索 API 费用；适合权威稿源 |
| vs 微博 | 结构化程度高；无反爬；适合政策稿、媒体通稿 |
| 局限 | 非关键词搜索；依赖 RSSHub 可用性；热榜反应可能慢于 NewsNow |

---

## 4. Tavily

### 4.1 数据源是什么

- **类型：** 第三方搜索 API（[Tavily](https://tavily.com)）
- **接入：** `tavily-python` SDK → `TavilyClient.search()`
- **特点：** 按自然语言 query 搜索网页；支持域名定向和时间范围
- **定位：** **URL 发现**，搜索摘要不作为事实依据

**代码位置：**
- SDK 封装：`tools/search.py:33-74`（`TavilySearchAgency`）
- Provider 层：`tools/tavily_provider.py`
- 配置：`config/media_sources.yaml:211-255`

### 4.2 怎么接入

```text
TavilyClient(api_key)
  → search(query, max_results, search_depth, days, include_domains)
  → 每个 query 执行 search_rounds 轮（默认 2）
  → 每轮：定向域名搜索 + 通用搜索
```

**代码位置：**
- SDK 调用：`tools/search.py:39-74`
- 多轮 + 双通道搜索：`tools/tavily_provider.py:81-164`
- Agent 注册（需 `TAVILY_API_KEY`）：`agent.py:139-160`
- 无 API Key 时跳过：`agent.py:159-160`

### 4.3 请求什么

| 参数 | 当前配置 | 说明 |
|------|----------|------|
| `query` | 1 条（QueryPlan 生成） | `nodes/query_plan_node.py:76-77` |
| `search_rounds` | 2 | 同一 query 重复搜索 2 轮 |
| `max_results_per_query` | 50 | 通用搜索每条 query 上限 |
| `targeted_max_results` | 50 | 定向域名搜索上限 |
| `include_domains` | `trusted_media_domains` + 境内外财经域名列表 | `targeted_search_enabled: true` 时启用 |
| `search_depth` | `basic` | 可选 `advanced` |
| `days` | 60 | 搜索时间范围 |
| `topic` | `general` | 固定 |
| `include_answer` | `false` | 不使用 Tavily 合成答案 |
| `include_raw_content` | `false` | 不拉原文，后续用 WebReader |

**每条 query 的逻辑请求次数：** 2 轮 ×（定向 1 次 + 通用 1 次）= **约 4 次 API 调用**

**代码位置：**
- 参数构造：`tools/search.py:48-59`
- 双通道循环：`tools/tavily_provider.py:91-94`

### 4.4 返回什么

**Tavily API 原始字段（每条 result）：**

| 字段 | 用途 |
|------|------|
| `title` | 标题 |
| `url` | 链接 |
| `content` | **搜索摘要**（非网页正文） |
| `published_date` | 发布日期 |
| `score` | 相关性分数 |

**映射到 MediaCandidate：**

| 字段 | 值 |
|------|-----|
| `title` | 规范化空白后的 title |
| `url` | 原始 url |
| `source_name` | 从 url hostname 提取 |
| `published_at` | `published_date` |
| `snippet` | `content` 前 500 字符 |
| `discovered_by` | `tavily_targeted` 或 `tavily_general` |
| `source_group` | 社交域名 → `social_media`，否则 `news_media` |
| `query` | 搜索词 |
| `metadata.appearances` | `[{query, channel, rank, score}]` |

**代码位置：**
- SearchResult 定义：`tools/search.py:12-20`
- 候选构造：`tools/tavily_provider.py:135-151`
- 社交域名判断：`tools/tavily_provider.py:13-32`
- 重复 URL 合并 appearances：`tools/tavily_provider.py:124-133`

### 4.5 怎么标准化

```text
Tavily API results
  → 校验 title / url scheme
  → canonical_url 去重，合并 appearances
  → MediaCandidate（snippet = 搜索摘要，标注 discovered_by 通道）
  → MediaDiscovery 时间过滤 + stage1 去重
  → Stage 2 WebReader 重抓正文（摘要不作为事实）
```

**关键原则：** Tavily `content` 只是搜索摘要，**Stage 2 必须 WebReader 重抓**。

**代码位置：**
- 摘要截断与标注：`tools/tavily_provider.py:140-150`
- 正文重抓：`agent.py:476-477`（`WebReader.read`）
- WebReader：`tools/web_reader.py`

### 4.6 怎么筛选

| 阶段 | 规则 | 代码位置 |
|------|------|----------|
| Provider 内 | 无 title / 非 http(s) → 丢弃 | `tavily_provider.py:113-114` |
| Provider 内 | 同 URL 合并 appearances | `tavily_provider.py:124-133` |
| MediaDiscovery | **不做** core/support 本地预筛（Tavily 靠 query 本身） | `media_discovery.py:69`（仅 newsnow/rss） |
| 时间过滤 | `published_at < cutoff` | `media_discovery.py:224-249` |
| Reflection 有效性 | `valid_provider_candidates("tavily")`：有 title + 合法 URL | `utils/dedup.py:61-64` |
| Stage 2 | top-k chunk + LLM 相关性 `score ≥ 30` | `agent.py:479-551` |
| 正文去重 | SHA-256 + SequenceMatcher ≥ 0.92 | `agent.py:506-527` |

**自适应补搜（当前状态）：** Tavily 的 `adaptive_triggered` 固定为 `False`（`single_rewrite_only`），不再触发 LLM 补充 query。

**代码位置：** `nodes/retrieval_reflection_node.py:45-58`

### 4.7 失败怎么办

| 失败类型 | 处理方式 |
|----------|----------|
| 无 `TAVILY_API_KEY` | Provider 不注册，进度提示跳过 | `agent.py:159-160` |
| 单次 search 异常 | 记录到 `failed_sources`，**继续另一通道/下一轮** | `tavily_provider.py:104-108` |
| SDK 层重试 | `@with_graceful_retry(max_retries=2)` 返回空结果 | `tools/search.py:39` |
| 定向搜索失败 | 通用搜索仍执行 |
| 全部为空 | 不阻断其他 Provider；Reflection 记录 `initial_valid_count=0` |

**代码位置：**
- Provider 级容错：`tools/tavily_provider.py:104-108`
- SDK 重试：`tools/search.py:29-31`（`_failed_response` 降级）

### 4.8 为什么需要它

| 对比维度 | Tavily 的独特价值 |
|----------|------------------|
| vs NewsNow/RSS | **关键词驱动**，可主动搜索特定事件，不受热榜/订阅范围限制 |
| 定向域名 | 可锁定财联社、第一财经等**可信媒体域名** |
| 通用搜索 | 补充境外媒体、长尾报道、机构观点页 |
| 局限 | 有 API 费用；摘要不可信；社交内容质量参差；需 Stage 2 重抓正文 |

---

## 5. 微博

### 5.1 数据源是什么

- **类型：** 网页爬虫（微博桌面搜索页 HTML + 评论 AJAX JSON）
- **入口：** `https://s.weibo.com/weibo`
- **鉴权：** 个人 Cookie（文件 `WEIBO_COOKIE_FILE`，不写入 yaml）
- **特点：** 帖子正文在搜索页即可获取，带点赞/评论/转发数；可选抓热门评论

**代码位置：**
- Provider：`tools/weibo_provider.py`
- 配置：`config/media_sources.yaml:257-273`（默认 `enabled: false`）
- Cookie 路径解析：`agent.py:166-168`（`resolve_env_path`）

### 5.2 怎么接入

```text
读取 Cookie 文件
  → requests GET s.weibo.com/weibo?q=...&page=N
  → HTMLParser 解析 feed_list_item
  → [可选] GET weibo.com/ajax/statuses/buildComments
  → MediaCandidate（content_ready=True）
```

- 非官方 API，无 SDK
- 搜索页：每页间隔 4–8s（随机）
- 评论：仅对已通过相关性复核的帖子，最多 5 帖，间隔 5–10s

**代码位置：**
- Cookie 读取：`tools/weibo_provider.py:285-294`
- HTTP GET（含 403/重定向检测）：`tools/weibo_provider.py:296-320`
- 搜索页解析器：`tools/weibo_provider.py:43-156`（`_SearchPageParser`）
- 分页抓取：`tools/weibo_provider.py:322-382`（`_fetch_posts`）
- 评论抓取：`tools/weibo_provider.py:384-428`
- 保存前相关性复核后抓评论：`agent.py:302-392`（`_filter_weibo_before_persistence`）

### 5.3 请求什么

**搜索页：**

| 参数 | 值 | 说明 |
|------|-----|------|
| `q` | `weibo_query`（QueryPlan 生成） | 平台口语化关键词 |
| `xsort` | `hot` | 按热度排序 |
| `Refer` | `hot_weibo` | 固定 |
| `page` | 1 ~ `max_search_pages`（默认 5） | 分页 |
| Cookie | Header `Cookie` | 必填 |

**评论接口：**

| 参数 | 值 |
|------|-----|
| `id` | 帖子 `wid` |
| `count` | 20 |
| `is_reload` | 1 |

**停止条件：** 累计 ≥ `target_posts`（默认 30）或当页无新增或没有下一页。

**代码位置：**
- 搜索参数：`tools/weibo_provider.py:334-339`
- 评论参数：`tools/weibo_provider.py:403-407`
- 配置默认值：`config/media_sources.yaml:263-272`

### 5.4 返回什么

**搜索页解析字段（每条帖子）：**

| 字段 | 说明 |
|------|------|
| `wid` | 微博 mid |
| `mblogid` | 博文 id |
| `user_id` / `user_name` | 作者 |
| `text` | 帖子正文 |
| `url` | `https://weibo.com/{uid}/{mblogid}` |
| `published_at` | 从 `<a title=...>` 提取 |
| `likes_count` / `comments_count` / `reposts_count` | 互动数 |
| `text_complete` | 是否含「展开全文」 |
| `platform_rank` | 搜索排名 |

**评论字段：**

| 字段 | 说明 |
|------|------|
| `comment_id` | 评论 id |
| `user_name` | 评论者 |
| `text` | 评论正文 |
| `likes_count` | 点赞数 |

**映射到 MediaCandidate：**

| 字段 | 值 |
|------|-----|
| `title` | 正文前 80 字 |
| `url` | 帖子链接 |
| `source_name` | `"微博"` |
| `snippet` | 完整正文 |
| `discovered_by` | `("weibo",)` |
| `source_group` | `social_media` |
| `query` | 搜索关键词 |
| `guid` | `weibo:{wid}` |
| `metadata.content_ready` | `True`（正文已在 Stage 1 就绪） |
| `metadata` | wid、互动数、评论列表等 |

**代码位置：**
- 帖子解析：`tools/weibo_provider.py:43-156`
- 评论解析：`tools/weibo_provider.py:430-443`
- 候选构造：`tools/weibo_provider.py:458-477`

### 5.5 怎么标准化

```text
HTML 搜索页
  → _SearchPageParser 提取帖子
  → raw_results 快照（含 query、fetched_at）
  → MediaCandidate（content_ready=True, guid=weibo:wid）
  → Stage 1 保存前 LLM 相关性复核（失败回退本地规则）
  → [可选] 对 accepted 帖子抓评论
  → PostgreSQL（raw_content + social_snapshot 元信息）
  → Stage 2 跳过 WebReader，直接复用 snippet 作为正文
```

**代码位置：**
- 保存前复核：`agent.py:302-392`
- Stage 1 微博特殊入库：`api.py:356-391`
- Stage 2 复用正文：`agent.py:466-474`（`content_ready` 分支）
- DB 恢复候选：`api.py:972-1012`（`_candidate_from_database_row`）

### 5.6 怎么筛选

| 阶段 | 规则 | 代码位置 |
|------|------|----------|
| 解析时 | 无 wid / 无 text → 丢弃 | `weibo_provider.py:355-358` |
| 保存前 | **LLM 相关性复核**（batch=15） | `agent.py:333-342` |
| LLM 失败降级 | `is_weibo_candidate_relevant`（core/support 本地规则） | `agent.py:347-358` / `media_relevance.py:52-63` |
| Reflection | 有效结果 < 2 → 触发补搜 | `retrieval_reflection_node.py:59-69` |
| 补搜 | `AdaptiveRetrievalNode` 生成 refined_query | `retrieval_reflection_node.py:116-142` |
| Stage 2 | 已通过保存前复核 → **跳过**正文 LLM 复核 | `agent.py:558-565` |
| 评论 | 仅对 accepted 帖子；按互动数排序取 top N | `weibo_provider.py:384-391` |

### 5.7 失败怎么办

| 失败类型 | 处理方式 |
|----------|----------|
| Cookie 文件不存在/为空 | Provider 整体失败，返回 `[]`，**不阻断其他源** | `weibo_provider.py:247-251` |
| Cookie 失效（登录页/重定向 passport） | 抛错，微博 Provider 失败 | `weibo_provider.py:311-314, 348-349` |
| HTTP 403 / 418 / 432 | 访问限制，抛错 | `weibo_provider.py:316-317` |
| 后续分页失败 | **保留前页结果**，记录 `failed_sources["微博后续页面"]` | `weibo_provider.py:341-347` |
| 评论抓取失败 | 单帖跳过，帖子正文仍保留 | `weibo_provider.py:425-428` |
| 评论默认 | yaml 中 `comments.enabled: true`，但 Provider 默认 `enabled: false` |

**代码位置：** 见上表各行

### 5.8 为什么需要它

| 对比维度 | 微博的独特价值 |
|----------|---------------|
| vs NewsNow 热榜 | 可按**自定义关键词**搜索，不限于榜单条目 |
| vs Tavily | 原生社交平台语境；带**互动数据**（赞/评/转）和评论 |
| vs RSS | 反映**民间舆论**、市场情绪、KOL 观点 |
| 局限 | 强依赖 Cookie；反爬风险高；正文可能不完整（「展开全文」）；请求频率需严格控制 |

---

## 6. 四源汇合后的统一处理

### 6.1 MediaDiscovery 编排顺序

```text
for provider in [newsnow, rss, tavily, weibo]:
    provider.search(...)
    → newsnow/rss 本地预筛
    → [可选] Reflection Loop 补搜（当前主要作用于微博）
    → 时间过滤
    → stage1_by_url 去重（raw_candidates）
    → select_candidates 限额（selected，用于统计）
```

**代码位置：** `tools/media_discovery.py:50-315`

### 6.2 Stage 1 → Stage 2 边界

```text
Stage 1 入库 PostgreSQL
  → _reload_stage1_from_database（以 DB 为唯一边界）
  → WebReader 正文抓取（微博除外）
  → 正文去重
  → top-k chunk 选择
  → CandidateFilterNode LLM 相关性
  → MediaNode 结构化抽取
  → prepared_analysis
  → BriefNode 生成 brief_data
```

**代码位置：**
- Stage 1 保存：`api.py:332-400`
- DB 重载：`api.py:403-445`
- 完整 Stage 2：`agent.py:442-592`
- 断点恢复：`api.py:1031-1224`（`resume-analysis`）

### 6.3 筛选参数（全局）

| 参数 | 默认值 | 配置位置 |
|------|--------|----------|
| `candidate_limit` | 100 | `media_sources.yaml:276` |
| `max_per_source` | 3 | `media_sources.yaml:278` |
| `content_relevance_top_k` | 5 | `media_sources.yaml:279` |
| `relevance_model_min_score` | 30 | `media_sources.yaml:280` |
| `content_filter_max_chars` | 5000 | `media_sources.yaml:281` |
| `tavily_min_valid_results` | 3 | `media_sources.yaml:6` |
| `weibo_min_valid_results` | 2 | `media_sources.yaml:7` |

### 6.4 去重策略汇总

| 层级 | 策略 | 代码位置 |
|------|------|----------|
| URL 规范化 | 去 www、去 tracking params、特定域名合并 | `utils/dedup.py:21-40` |
| Stage 1 | 同 URL 合并 `discovered_by` + `appearances` | `media_discovery.py:251-295` |
| 限额选择 | URL 去重 → 标题去重 → 相关性打分 → per-source 限额 | `utils/dedup.py:94-151` |
| Stage 2 正文 | SHA-256 完全相同 或 前 12k 字符相似度 ≥ 0.92 | `agent.py:506-527` |
| 微博 | 不做正文相似度去重 | `agent.py:510-511` |

---

## 7. 快速定位索引

| 主题 | 文件 |
|------|------|
| Provider 注册与编排 | `agent.py:92-300` |
| 多源发现协调 | `tools/media_discovery.py` |
| NewsNow | `tools/newsnow_provider.py` |
| RSS | `tools/rss_provider.py` |
| Tavily SDK | `tools/search.py` |
| Tavily Provider | `tools/tavily_provider.py` |
| 微博 | `tools/weibo_provider.py` |
| 本地预筛规则 | `tools/media_relevance.py` |
| 去重与限额 | `utils/dedup.py` |
| 自适应补搜 | `nodes/retrieval_reflection_node.py` |
| 正文读取 | `tools/web_reader.py` |
| 数据模型 | `tools/media_models.py` |
| 媒体配置 | `config/media_sources.yaml` |
| Query 规划 | `nodes/query_plan_node.py` |
| Stage 1 入库 | `api.py:332-400` |
| Stage 2 分析 | `agent.py:442-592` |

---

*最后更新：2026-08-17，对齐当前 `My_agent` 主分支实现。*
