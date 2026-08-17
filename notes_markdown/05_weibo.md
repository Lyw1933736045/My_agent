# 微博：从检索到 `analysis_ready`

## 1. 适用范围

本文记录当前代码中微博从查询规划、帖子与评论抓取、保存前相关性复核、PostgreSQL 持久化，到 MediaNode 结构化分析的实际流程。终点是 `analysis_ready`，不包含 BriefNode 和最终简报生成。

追加来源入口：`POST /api/v1/runs/{run_id}/sources`

```text
QueryPlanNode 生成 weibo_query
    ↓
微博热度排序搜索，按页抓取帖子
    ↓
按 wid / URL 去重
    ↓
LLM 对完整帖子做保存前相关性复核
    ↓
模型失败时使用 core/support 本地宽松规则
    ↓
只对通过复核的帖子抓取评论
    ↓
帖子全文、作者、互动量、评论写入 PostgreSQL
    ↓
从 PostgreSQL 重新加载当前 run 的全部候选
    ↓
微博不再读取网页，也不再执行第二次相关性复核
    ↓
通过的完整微博及评论进入 MediaNode
    ↓
保存 social_insights
    ↓
run.status = analysis_ready
```

总编排位置：`api.py:755-812`。

## 2. `weibo_query`

Query Planner 根据用户本次输入生成一条 `weibo_query`：

- 是浓缩的自然事件短句，不是零散关键词列表；
- 保留核心主体、产品或事项以及事件动作；
- 不生成多个候选，不使用日期、URL 或搜索运算符；
- 不允许为空。

微博相关性复核同时复用动态生成的 `newsnow_rss_core` 和 `newsnow_rss_support`，但搜索请求只使用 `weibo_query`。

相关代码：

- Prompt：`prompts/prompts.py:24-39`
- 解析与校验：`nodes/query_plan_node.py:71-96`
- 写入 RunState：`agent.py:205-222`

## 3. Provider 启用与 Cookie

微博在全局配置中默认 `enabled: false`，但通过 API 明确选择 `weibo` 来源时仍会构建 Provider。

Cookie 不写在项目配置中，由 `${WEIBO_COOKIE_FILE}` 指向项目外文件。Cookie 文件不存在、格式无效、返回登录页或登录重定向时，本轮微博抓取失败。

当前网络设置：

- `trust_env_proxy: false`，微博请求不使用系统环境代理；
- 超时 20 秒；
- HTTP 403、418、432 被识别为访问限制；
- 请求不自动跟随重定向。

相关代码：

- Provider 构建：`agent.py:160-190`
- Cookie 与请求校验：`tools/weibo_provider.py:285-320`
- 配置：`config/media_sources.yaml:257-273`

## 4. 帖子搜索与翻页

微博搜索请求使用：

```text
q = weibo_query
xsort = hot
Refer = hot_weibo
page = 当前页
```

当前配置：

- 目标帖子数：30
- 最大搜索页数：5
- 翻页间隔：随机 4～8 秒

Provider 内部允许的最大页数上限是 10，但当前配置实际使用 5 页。抓取满足任一条件时停止：

- 去重后累计帖子达到 30 条；
- 当前页没有新增帖子；
- 页面不存在下一页；
- 后续页面请求失败，此时保留已经抓取的前页结果。

首轮有效帖子少于 2 条时，Reflection 节点可让 LLM 生成一条不同的 `refined_query`，再执行一次有限补搜。首轮与补搜结果随后合并去重。

相关代码：

- 搜索与停止条件：`tools/weibo_provider.py:226-253、322-382`
- 补搜判断与新 query：`nodes/retrieval_reflection_node.py:33-142`
- 统一补搜编排：`tools/media_discovery.py:144-222`

## 5. 帖子字段

桌面搜索页解析 `action-type=feed_list_item` 区域，获得：

- `wid`、`mblogid`
- `user_id`、`user_name`
- 帖子 URL、发布时间
- 帖子文本
- 转发数、评论数、点赞数
- 搜索排序和平台排名

“万”“亿”等互动量会转换为整数。帖子缺少 `wid` 或正文时丢弃；同一轮内按 `wid` 去重。

如果搜索页文本包含“展开全文”，只记录 `text_complete = false`；当前代码没有继续请求微博详情页补全被折叠的正文。

转换成统一候选时：

- `title`：帖子文本前 80 个字符
- `snippet`：当前抓到的完整帖子文本
- `source_group = social_media`
- `guid = weibo:{wid}`
- `content_ready = true`

相关代码：

- HTML 解析：`tools/weibo_provider.py:31-158`
- 候选转换：`tools/weibo_provider.py:458-477`

## 6. 保存前相关性复核

微博与 NewsNow/RSS 不同：正常路径优先使用 LLM，在写 PostgreSQL 之前判断帖子是否相关。

处理方式：

1. 按 `guid` 或 URL 再去重；
2. 每 15 条组成一批；
3. 将帖子全文作为 `MediaDocument.content`；
4. LLM 根据 topic、`newsnow_rss_core`、`newsnow_rss_support` 返回 `relevant`、`score`、`reason`；
5. 只有 `relevant = true` 且分数达到 30 才保留。

若某一批 LLM 调用失败，则该批改用本地宽松规则：

- 帖子命中任意一个 core；或
- 帖子命中至少两个不同 support。

本地兜底只使用规范化后的 substring 匹配。未通过相关性复核的帖子仍停留在本轮内存中，不写入 PostgreSQL，也不抓取评论。

相关代码：

- 保存前复核：`agent.py:302-368`
- LLM 判断：`nodes/candidate_filter_node.py`
- 本地兜底：`tools/media_relevance.py:52-64`
- Prompt：`prompts/prompts.py:122-132`

## 7. 评论抓取

评论只对已通过保存前相关性复核的帖子执行。

当前规则：

- 仅选择原始评论数大于 0 的帖子；
- 按评论数、点赞数、转发数降序；
- 最多选择 5 条帖子；
- 每条帖子只请求一次评论接口，`count = 20`；
- 帖子之间随机等待 5～10 秒；
- 不继续翻评论页。

保存的单条评论字段：

- `comment_id`、`post_wid`
- `user_id`、`user_name`
- `created_at`
- `text`
- `likes_count`

若接口返回 `max_id`，记录 `has_more = true` 和 `truncated = true`，表示仍有未抓取评论。单条帖子的评论请求失败只记录错误，不删除该微博帖子。

相关代码：`tools/weibo_provider.py:255-283、384-443`。

## 8. PostgreSQL 持久化

相关微博完成评论抓取后直接作为 Stage 1 候选保存，不再划分网页式的“候选摘要”和“后续正文抓取”。

### documents

- `canonical_url`、微博原 URL
- `title`：帖子前 80 字符
- `publisher = 微博`
- `source_type = social_media`
- `raw_content`：帖子全文
- `fetch_status = success`
- `content_type = text/plain`
- `fetched_at`
- `metadata`：platform、wid、mblogid、user_id、user_name、text_complete

### event_documents

- `event_id = run_id`
- `snippet`：帖子全文
- `analysis_status = accepted`
- `analysis_reason`、`relevance_score`
- `selected_for_report = true`
- `discovery`：provider、query、search_sort、platform_rank
- `metadata.social_snapshot`：帖子文本、抓取时间、点赞/评论/转发数、评论抓取状态和评论列表

评论当前保存在 `event_documents.metadata.social_snapshot.comments` JSONB 中，没有单独的 comments 表。

同一 URL 再次追加到同一个 run 时不会新建 `event_documents` 关系，而是合并 discovery 和 metadata。

相关代码：

- Stage 1 字段组装：`api.py:165-226`
- 数据库保存和去重：`run_repository.py:380-480`
- 表结构：`knowledge/models.py:20-94`

## 9. PostgreSQL 之后的处理

Stage 1 完成后，代码按 `run_id` 从 PostgreSQL 重新加载当前 case 的全部候选。微博记录恢复为：

- `content_ready = true`
- `prechecked_relevance = true`
- 正文来自已保存的帖子全文
- 互动量和评论来自 `social_snapshot`

因此微博在 `prepare_analysis()` 中：

- 不调用 WebReader；
- 不读取微博详情页；
- 不执行完整正文相似度去重；
- 不再次调用 LLM 判断相关性；
- 直接复用保存前的 score 和 reason，形成 `weibo_pre_persistence` 决策。

如果同一个 run 还包含 NewsNow、RSS 或 Tavily，它们仍按各自的网页正文流程处理。

相关代码：

- 数据库重载：`api.py:236-278`
- 微博正文直通与复核复用：`agent.py:451-474、506-512、534-567`

## 10. MediaNode 与 `public_opinion`

通过保存前复核的微博进入 MediaNode。输入包括：

- 完整帖子文本
- 点赞数、评论数、转发数
- 平台排名和搜索排序
- 已抓取评论

微博属于 `social_media`，MediaNode 不对帖子执行长正文分块，而是一次处理完整文本，并将互动量、评论和微博身份字段保留在 MediaInsight.metadata 中。

MediaNode 输出的事实和观点仍受 Prompt 约束：社交观点不能写成已确认事实，互动量只能说明传播规模，不能推断观点正确性或因果关系。

完成后，微博结果写入：

```text
events.metadata.prepared_analysis.social_insights
```

BriefNode 后续读取 `social_insights`，将其用于 `brief_data.public_opinion`。如果不调用简报 API，此时只保存结构化分析，不生成 Markdown 或 Dashboard。

相关代码：

- MediaNode：`nodes/media_node.py:31-112`
- Media Prompt：`prompts/prompts.py:93-119`
- prepared_analysis：`api.py:788-807`
- 后续 BriefNode：`api.py:815-835`、`agent.py:593-622`

## 11. 状态终点

保存 MediaNode 结构化结果后：

- `events.status = analysis_ready`
- 本次 execution 在 `search_history` 中标记 completed
- 若 case 已有旧报告，则设置 `report_stale = true`
- 后续可以直接调用 `POST /api/v1/runs/{run_id}/brief` 生成新简报，无需重新抓取微博或再次分析帖子

相关代码：`run_repository.py:232-267`。

## 备注 1：当前微博流程的边界

- 微博帖子短，正文在保存前已完整获得，因此不需要网页 Stage 2 正文抓取。
- 相关性判断发生在保存前；无关帖子和其评论都不进入数据库。
- 评论只抓取最多 5 条高评论帖子，每帖单次请求最多返回 20 条，不做评论翻页。
- Cookie、访问频率和 HTTP 访问限制是主要失败点；后续页失败时会保留已成功抓取的前页帖子。

