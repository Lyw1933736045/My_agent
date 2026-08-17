# Stage 2：正文读取、去重与相关性复核

## 1. 适用范围

本流程读取 PostgreSQL 中当前 `run_id` 的全部 Stage 1 候选，适用于 NewsNow、RSS 和 Tavily 已保存的候选。Tavily 的搜索方式不在 Stage 2 修改范围内。

入口：`My_agent/scripts/run_stage2.py`

## 2. 正式流程

```text
PostgreSQL 当前 run 的全部 Stage 1 候选
    ↓
读取完整网页正文，并写入 documents.raw_content
    ↓
正文读取失败的候选标记 fetch_failed
    ↓
完整正文去重
    ↓
长正文切分，按事件词选择 top-k 片段
    ↓
LLM 正文相关性复核
    ↓
accepted 候选进入 MediaNode
    ↓
从完整正文提取事实、观点、数据
    ↓
生成简报并更新 run
```

Stage 2 不再读取内存中的 Stage 1 临时结果，而是通过 `RunRepository.list_candidates(run_id)` 从数据库重新读取全部候选：

- `scripts/run_stage2.py:59-67`
- `run_repository.py:327-365`

## 3. 正文读取与数据库保存

每个候选逐个调用 `FinancialMediaAgent.reader.read(url)`。网页读取器只提取 HTML 可见文本，忽略脚本、样式等标签，并限制最多读取 5 MB、最多保留 100,000 个字符。

成功后写入 `documents` 表：

- `raw_content`：完整抓取正文
- `final_url`：跳转后的最终地址
- `content_type`、`fetched_at`
- `fetch_status = success`
- `content_hash`：完整正文的 SHA-256 哈希

相关代码：

- 读取：`scripts/run_stage2.py:76-89`
- 数据库更新：`run_repository.py:297-325`
- 字段定义：`knowledge/models.py:46-72`

读取失败时不进入后续正文分析，`event_documents.analysis_status` 标记为 `fetch_failed`，具体异常写入 `documents.fetch_error`。

## 4. 正文去重

去重发生在完整正文读取成功之后、top-k 之前。

### 4.1 判定方式

对每篇普通新闻正文执行两层判断：

1. 去除全部空白后计算 SHA-256；哈希相同，视为重复。
2. 哈希不同，再比较两篇正文前 12,000 个字符的 `SequenceMatcher.ratio()`；相似度 `>= 0.92`，视为高度相似。

实现位置：`scripts/run_stage2.py:47-49、97-113`。

社交媒体候选当前不做正文相似度去重，直接保留，因为其正文可能是短帖或评论内容。

### 4.2 相似和不相似的区别

- **相似**：同一事件的转载、带不同 URL 参数的同一文章、或正文大部分相同。保留先出现的原文，后续候选标记为 `duplicate`，不再送 LLM 复核，也不进入报告。
- **不相似**：未达到哈希相同或 `SequenceMatcher >= 0.92` 的条件。即使标题类似，只要正文有足够差异，仍继续进入 top-k 和正文相关性复核。
- 去重范围是**单个 run 内部**，不是全局跨 case 去重。不同 case 可以分别建立自己的 `event_documents` 关系；数据库层仍通过规范化 URL 复用同一个 `documents` 记录。

## 5. Chunk 与 top-k

### 5.1 正文相关性复核前的 top-k

正文长度不超过 6,000 字符时，完整正文直接用于相关性复核；超过 6,000 字符时：

- `chunk_size = 1,500`
- `overlap = 200`
- 优先在换行、句号、问号、分号等自然边界切分
- 取最多 `content_relevance_top_k` 个片段，默认 5 个
- 片段按原文顺序重新拼接后送入 LLM

实现位置：

- `scripts/run_stage2.py:37-44、115-124`
- `tools/text_chunking.py:11-45、99-127`

### 5.2 top-k 的轻量排序

每个 chunk 使用简单 substring 匹配计算分数：

- `topic` 完整命中：`+10`
- 每命中一个 `newsnow_rss_core`：`+10`
- 每命中一个 `newsnow_rss_support`：`+3`

只保留分数大于 0 的片段，按分数降序、原始位置升序取前 k 个，最后恢复原文顺序。如果没有任何关键词命中，则使用首段、中段、尾段作为召回兜底，不会因为关键词缺失直接丢弃整篇文章。

该步骤只是控制送入 LLM 的文本长度，不是最终相关性结论。

## 6. LLM 正文相关性复核

复核输入包括：

- 文章标题、来源、URL
- top-k 正文片段
- 当前事件主题
- 动态生成的 `newsnow_rss_core`
- 动态生成的 `newsnow_rss_support`

实现位置：

- 调用：`scripts/run_stage2.py:125-140`
- 判断节点：`nodes/candidate_filter_node.py`
- Prompt：`prompts/prompts.py` 中的正文相关性复核提示词

LLM 返回 `relevant`、`score`、`reason` 等结果。当前保留条件由 `CandidateFilterNode` 执行：文章明确涉及当前事件且模型评分达到配置的 `relevance_model_min_score`（默认 60）时标记为 `accepted`；否则标记为 `rejected`。具体原因写入 `event_documents.analysis_reason`。

注意：`accepted/rejected` 是正文复核结果；`duplicate` 和 `fetch_failed` 在此之前已经被排除。

## 7. accepted 后如何使用正文

相关性复核使用 top-k 片段，但进入 `MediaNode` 后，文章类型候选仍优先使用完整 `raw_content`：

- 正文不超过 12,000 字符：一次使用完整正文
- 超过 12,000 字符：按 `chunk_size = 5,000`、`overlap = 300` 分块，分别提取事实、解释、统计数据、人物观点等，再合并去重

实现位置：`nodes/media_node.py:31-67`。

因此，top-k 用于“是否相关”的高效预筛，最终简报信息提取不会只依赖 top-k 片段。

## 8. 代理问题修复

### 修复前

`WebReader` 只使用系统默认 opener。当前环境代理不可用时，大量网页请求直接失败。本次 Tavily Stage 2 在修复前曾出现：

- 20 条候选中仅 8 条正文读取成功
- 12 条读取失败

### 修复后

`tools/web_reader.py:88-113` 同时建立两种 opener：

1. 先按系统环境使用默认代理请求。
2. 默认请求发生 `ValueError` 失败后，使用 `ProxyHandler({})` 关闭代理直接请求一次。
3. 两次都失败，才将两次异常合并写入 `fetch_error`。

本次实际 Tavily Stage 2 结果：

- 正文读取成功：18/20
- 正文读取失败：2/20
- 知乎：HTTP 403，即使关闭代理仍失败
- 中国经营报专题页：默认代理 HTTP 502，关闭代理 HTTP 521

该修复只影响网页正文读取，不改变 NewsNow、RSS、Tavily 的搜索、重试或时间过滤逻辑。

## 9. 本次 Tavily run 记录

Run ID：`tavily-20260815160733-00927622`

统计：

| 阶段 | 数量 |
|---|---:|
| Stage 1 数据库候选 | 20 |
| 正文读取成功 | 18 |
| 正文读取失败 | 2 |
| 正文去重后 | 15 |
| 正文重复 | 3 |
| LLM 复核 accepted | 11 |
| LLM 复核 rejected | 4 |

### 本次正文相关性复核摘要

保留的 11 条主要包括：

- 吴清：会同人民银行研究推进人民币外汇期货试点
- 吴清陆家嘴论坛发声：推进人民币外汇期货试点
- 吴清：会同人民银行研究推进人民币外汇期货试点
- 大公财经：研究推进人民币外汇期货试点
- 央行宣布 8 项金融政策，将研究推进人民币外汇期货交易
- 证监会主席吴清：会同人民银行研究推进人民币外汇期货试点
- 证监会主席吴清：研究推进人民币外汇期货试点
- 证监会：将发布 QFII 制度优化方案
- 重磅！创业板将支持优质未盈利创新企业上市
- 吴清在 2025 陆家嘴论坛上的主旨演讲
- 陆家嘴论坛：潘功胜、李云泽、吴清等重磅发声

其中部分标题没有直接出现“人民币外汇期货”，但正文中提到了该事项，因此被保留。这符合当前的“正文优先”流程。

筛除的 4 条及原因：

- 2024 陆家嘴论坛专题：提到监管政策，但没有具体提及人民币外汇期货试点
- 央行、金监总局、证监会等金融监管部门齐聚陆家嘴论坛：只有活动预告，没有核心事件内容
- 聚焦第十三届陆家嘴论坛（2021）：年份和事件背景不匹配
- 证监会主席活动列表：只有活动索引，没有实质内容提及外汇期货

其他结果：

- 3 条正文与其他候选高度相似，被标记为 `duplicate`
- 2 条无法读取正文：
  - 中国经营报陆家嘴论坛专题：HTTP 502；关闭代理后 HTTP 521
  - 知乎文章：HTTP 403；关闭代理后仍为 HTTP 403

### 重复正文 URL

以下 URL 与同 run 内其他正文高度相似，被标记为 `duplicate`：

- `https://stcn.com/article/detail/1852025.html?u_atoken=4d968f5ed26cbe5f89ad77cd383f1226&u_asig=ffbfd`
- `https://stcn.com/article/detail/1852025.html?u_atoken=dbd3d5ade71cca24cead3c1882303159&u_asig=ffbfd`
- `https://stcn.com/article/detail/2103104.html?u_atoken=63278d78d249247d1edeb8b59d989dfc&u_asig=ffbfd`

它们分别对应无参数或另一组参数的原始候选，重复项没有进入 LLM 正文复核。

### 不相似、继续复核的 URL

以下 URL 未被判定为正文重复，因此继续执行 top-k 和 LLM 复核；其中 `accepted` 和 `rejected` 再由正文内容决定：

- `https://www.sohu.com/a/1037740469_130887`
- `https://view.inews.qq.com/k/20260617A057SF00?scene=wap&no-redirect=1`
- `https://m.sohu.com/a/1037742955_122014422`
- `https://www.facebook.com/tkp1902financialnews/posts/%E5%90%B3%E6%B8%85%E7%A9%8D%E6%A5%B5%E6%8B%93%E5%AF%AC%E8%B3%87%E9%87%91%E4%BE%86%E6%BA%90-%E7%A0%94%E6%8E%A8%E9%80%B2%E4%BA%BA%E6%B0%91%E5%B9%A3%E5%A4%96%E5%8C%AF%E6%9C%9F%E8%B2%A8%E8%A9%A6%E9%BB%9E%E4%B8%AD%E5%9C%8B%E8%AD%89%E7%9B%A3%E6%9C%83%E4%B8%BB%E5%B8%AD%E5%90%B3%E6%B8%85%E5%9C%A82026%E9%99%B8%E5%AE%B6%E5%98%B4%E8%AB%96%E5%A3%87%E4%B8%8A%E8%A1%A8%E7%A4%BA%E4%B8%AD%E5%9C%8B%E8%B3%87%E6%9C%AC%E5%B8%82%E5%A0%B4%E7%99%BC%E7%94%9F%E4%BA%86%E7%A9%8D%E6%A5%B5%E8%80%8C%E6%B7%B1%E5%88%BB%E7%9A%84%E8%AE%8A%E5%8C%96%E6%96%B0%E5%9C%8B%E4%B9%9D%E6%A2%9D%E7%99%BC%E5%B8%83%E5%85%A9%E5%B9%B4%E5%A4%9A%E4%BE%86%E7%A4%BE%E4%BF%9D%E4%BF%9D%E9%9A%AA%E7%AD%89%E6%8C%81有a/1009864145309347`
- `https://stcn.com/article/detail/1852025.html`
- `https://stcn.com/topic/detail/323.html`
- `https://stcn.com/article/detail/2103104.html?u_atoken=855a48dfdd8c11ca92378c4e22f3dd87&u_asig=ffbfd`
- `https://stcn.com/zt/202106/t20210610_3324772.html`
- `http://www.csrc.gov.cn/csrc/c106423/common_list_2.shtml`
- `https://finance.sina.com.cn/money/forex/rmb/2026-06-17/doc-inicsrqu5774840.shtml`
- `https://wap.eastmoney.com/a/202606173774298593.html`
- `http://finance.people.com.cn/n1/2025/0619/c1004-40504235.html`
- `https://www.stcn.com/article/detail/2103347.html`
- `https://jrj.sh.gov.cn/ZXYW178/20250618/c2373870510a4ed9b31c225fc7351e39.html`
- `https://www.gelonghui.com/p/2345285`

最终 `accepted` 的 URL 为：搜狐、腾讯、移动搜狐、Facebook、大公财经、带参数的 2103104、 新浪、东方财富、人民网、创业板报道、上海金融办演讲、格隆汇中的 11 条；`stcn.com/topic/detail/323.html`、无参数的 `1852025.html`、2021 论坛专题和证监会活动列表为 `rejected`。读取失败的 2 条未进入正文复核。
