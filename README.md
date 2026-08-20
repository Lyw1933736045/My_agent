# Financial Single Agent

轻量金融热点发现工具，使用 NewsNow 和 RSS，统一筛选新闻媒体、权威媒体候选。
候选会经过 Python 标题/摘要评分、正文读取和正文相关性复核，
只有高相关正文才会进入观点提炼与主题简报。

主流程参考 BettaFish 的单 Engine 分层：`agent.py` 负责流程编排，`nodes/`
只保留独立 LLM 任务，`tools/` 负责外部数据访问，`state/RunState` 保存单次运行结果，
CLI 仅处理参数与输出。

## 安装与运行

```bash
cd /path/to/financial_single_agent
python3 -m pip install -e .
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

## PostgreSQL 主数据库

项目所有运行状态、搜索候选和新闻完整正文统一保存到一个 PostgreSQL 数据库，
只使用三张业务表：`events`、`documents` 和 `event_documents`。旧的 SQLite 文件
不迁移，归档在 `legacy/data/`。

```bash
python3 -m pip install -e .
# 在 .env 中配置 DATABASE_URL
alembic upgrade head
financial-single-agent topic-brief --query "研究事件"
```

第一期只保存新闻原始正文；简报生成版、分块、向量、结构化事实和知识图谱暂不入库，
后续需要时从原文重新生成。

## 简报覆盖评测

离线脚本：把导师 `reference.md` 按段切分，召回简报 Top3，用 `deepseek-v4-flash` 打相关/准确/完整/有用四维分（各 25，每段独立 3 次取平均）。不入库、不上前端。

```bash
python3 scripts/eval_reference_coverage.py --case-key case1
```

提示词在 `evaluation/prompts.py`。Judge 模型用 `.env` 里的 `JUDGE_MODEL_NAME`（默认 `deepseek-v4-flash`），与写简报的 `deepseek-chat` 分开。

CLI 仍可用 `--evaluation-case` 导出本次检索正文快照，供对照材料使用。

## FastAPI 与前端

### 前端启动流程

在项目目录执行以下命令：

```bash
cd /Users/luoyuwen/Desktop/projects/My_agent
python3 -m pip install -e .            # 首次安装或依赖更新时执行
cp .env.example .env                  # 首次安装时执行，并填写 LLM/Tavily 密钥
financial-single-agent-api
```

启动成功后，在浏览器打开：

```text
http://127.0.0.1:8000/
```

前端和 FastAPI 属于同一个服务，不需要先启动另一个前端进程。输入问题后点击
「生成简报」即可；服务会在后台依次执行 NewsNow、RSS、Tavily 和微博等已启用来源。
停止服务使用终端中的 `Ctrl-C`。

如果关闭终端后服务仍占用 8000 端口，可以先查看监听进程：

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

记下输出中的 `PID`，再正常结束该进程：

```bash
kill <PID>
```

确认端口仍未释放时，才使用强制结束：

```bash
kill -9 <PID>
```

端口释放后重新执行 `financial-single-agent-api` 即可。

RSS 是否需要单独启动取决于 `.env` 中的 `RSSHUB_BASE`。当前默认值是
`http://localhost:1200`，因此本地使用时需要先启动 RSSHub：

现在执行 `financial-single-agent-api` 时，程序会自动检查本机 Docker 中名为
`financial-single-agent-rsshub` 的容器：已有容器则启动，没有则使用
`diygod/rsshub:latest` 创建并映射到 `1200` 端口。首次启动会下载镜像，因此可能需要一些时间。
这要求 Docker Desktop 已安装并处于运行状态；如果 Docker 不可用，API 仍会启动，但 RSS
请求会继续失败。

1. 启动 Docker Desktop；
2. 启动 `financial-single-agent-api`；
3. 浏览器访问 `http://127.0.0.1:8000/`。

如果将 `RSSHUB_BASE` 改成可用的远程 RSSHub 地址，则不需要单独启动本地 RSSHub；
直接启动 `financial-single-agent-api` 即可。

其他说明：

- RSSHub 不可用时，RSS 会记录失败，但不会阻止 NewsNow、Tavily 或微博继续执行；
- 暂时不使用 RSS 时，将 `config/media_sources.yaml` 中的 `rss.enabled` 设为 `false`。

微博配置说明：

- `weibo.enabled: true` 才会执行微博搜索；
- Cookie 路径通过 `.env` 中的 `WEIBO_COOKIE_FILE` 配置，Cookie 文件不写入 YAML；
- `weibo.comments.enabled: false` 时只抓取微博正文和点赞、评论、转发数量；
- 改为 `true` 才会额外抓取选定帖子的热门评论；
- 不配置有效 Cookie 时，微博 Provider 会单独失败，不影响其他来源。

### API 启动命令

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

API任务、人工审核结果、进度、错误、最终报告和新闻原文保存在 PostgreSQL。服务重启后，
待审核及已完成任务仍可查询；重启时仍在后台执行的任务会标记为失败，需要重新创建。
后台研究最多同时执行两个。

## 媒体候选检索

媒体来源配置位于 `config/media_sources.yaml`。RSS 按 `official_media` /
`news_media` 分组；NewsNow 热榜来源标记为 `news_media`。程序先按动态关键词
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

一步完成 NewsNow 与 RSS 检索、正文读取和联合简报：

```bash
python3 -m My_agent.cli topic-brief \
  --query "央行降准对银行、债券市场和实体经济融资成本有什么影响？"
```

生成的 Markdown 简报保存在 `My_agent/reports/`。使用 `--no-save` 可只打印结果。
`official_media` 和 `news_media` 两类来源都会保留。国务院专项搜索与
通用官方 URL 入口当前不参与主流程；旧的国务院实现归档在 `legacy/official/`。
