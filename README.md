# Financial Single Agent

轻量金融热点发现工具，依次使用 NewsNow、RSS 和 Tavily，统一筛选新闻媒体、
权威媒体与社交平台候选。候选会经过 Python 标题/摘要评分、正文读取和正文相关性复核，
只有高相关正文才会进入观点提炼与主题简报。

主流程参考 BettaFish 的单 Engine 分层：`agent.py` 负责流程编排，`nodes/`
只保留独立 LLM 任务，`tools/` 负责外部数据访问，`state/RunState` 保存单次运行结果，
CLI 仅处理参数与输出。

## 安装与运行

```bash
cd /path/to/financial_single_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
financial-single-agent media-search --query "央行最新货币政策"
```

也可以从该目录的父目录直接运行：

```bash
python3 -m financial_single_agent.cli "要研究的金融事件"
```

报告和事实状态固定写入本项目的 `My_agent/reports/`，不受启动时工作目录影响。
使用 `--no-save` 可只向标准输出打印报告。

本工具不接入实时行情或结构化财务数据库，结果仅基于搜索时可获得的公开资料，
不构成投资建议。

## FastAPI 与前端

安装项目后启动 API：

```bash
financial-single-agent-api
```

浏览器打开 `http://127.0.0.1:8000/` 使用前端：

1. 输入自然语言问题，点击「生成简报」。
2. 默认自动批准推荐检索词并开始聚合；需要改词时展开「高级：审核检索词」。
3. 页面轮询任务进度，完成后展示 Markdown 简报，也可打开排版页。

API 文档仍在 `http://127.0.0.1:8000/docs`。按下面顺序也可直接调用接口：

1. `POST /api/v1/plans`：提交研究问题，生成待审核检索词。
2. `POST /api/v1/plans/{run_id}/approve`：修改并批准检索词，启动后台研究。
3. `GET /api/v1/runs/{run_id}`：查询进度和执行结果。
4. `GET /api/v1/runs/{run_id}/report`：获取最终 Markdown 报告。
5. `GET /api/v1/runs/{run_id}/report/view`：在浏览器查看排版后的 HTML 报告。

API任务、人工审核结果、进度、错误和最终报告保存在 `data/my_agent.db`。服务重启后，
待审核及已完成任务仍可查询；重启时仍在后台执行的任务会标记为失败，需要重新创建。
后台研究最多同时执行两个。

## 媒体候选检索

媒体来源配置位于 `config/media_sources.yaml`。RSS 按 `official_media` /
`news_media` / `social_media` 分组；NewsNow 热榜来源标记为 `news_media` 或
`social_media`；Tavily 在 NewsNow 和 RSS 之后执行，只发现 URL。程序先按动态关键词
匹配、去重并限制每个来源的数量，不会把全部抓取结果交给 LLM：

NewsNow 默认单次超时 30 秒、失败后重试 1 次，并记录 `success` / `cache` 与失败平台；
RSS 使用持久 Session 复用连接、请求失败后最多重试一次，并记录失败 Feed、错误原因和文章 GUID。两路请求间隔、
NewsNow 重试等待都可在同一配置文件调整。RSS Feed 还可单独设置 `max_items` 和
`max_age_days`，值为 `0` 表示不限制。

```bash
python3 -m My_agent.cli media-search \
  --query "新能源汽车购置税政策对车企和消费有什么影响？"
```

该命令输出通过标题相关性复核的媒体候选；需要完整简报时使用下面的一步式命令。

一步完成新闻媒体与社交平台检索、正文读取和联合简报：

```bash
python3 -m My_agent.cli topic-brief \
  --query "央行降准对银行、债券市场和实体经济融资成本有什么影响？"
```

生成的 Markdown 简报保存在 `My_agent/reports/`。使用 `--no-save` 可只打印结果。
`official_media`、`news_media` 和 `social_media` 三类来源都会保留。国务院专项搜索与
通用官方 URL 入口当前不参与主流程；旧的国务院实现归档在 `legacy/official/`。
