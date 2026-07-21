# Financial Single Agent

独立金融研究 Agent，保留“问题规划 → 搜索 → 总结 → 反思 → Markdown 报告”
流程，只分析公开可检索的金融政策、市场热点和机构观点。

## 安装与运行

```bash
cd /path/to/financial_single_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
financial-single-agent "央行最新货币政策对银行股和债券市场可能产生哪些影响？"
```

也可以从该目录的父目录直接运行：

```bash
python3 -m financial_single_agent.cli "要研究的金融事件"
```

报告和事实状态固定写入本项目的 `My_agent/reports/`，不受启动时工作目录影响。
使用 `--no-save` 可只向标准输出打印报告。

本工具不接入实时行情或结构化财务数据库，结果仅基于搜索时可获得的公开资料，
不构成投资建议。

## 国务院主题检索与多文件简报

将自然语言主题拆成最多三条检索词，并仅通过中国政府网官方搜索发现政策 URL：

```bash
python3 -m My_agent.cli search-web \
  --query "国务院针对新能源汽车消费和税收有哪些支持政策？" \
  --limit 10
```

搜索结果只用于发现 URL，不使用搜索摘要生成事实。候选会写入
`My_agent/data/my_agent.db`，终端显示 ID、标题、来源和 URL。

人工选择多个候选后生成简报：

```bash
python3 -m My_agent.cli brief --ids 1,2,3
```

每条候选都会重新在线读取官方正文并调用 FactNode。BriefNode 只接收提取后的
EventFact 和官方链接，不接收搜索摘要或完整网页正文。简报保存在
`My_agent/reports/`。

## 媒体候选检索

媒体来源配置位于 `config/media_sources.yaml`。每个 NewsNow 来源通过 `source_group`
标记为 `news_media` 或 `social_media`，RSS 用于补充最新文章；程序先按动态关键词
匹配、去重并限制每个来源的数量，不会把全部抓取结果交给 LLM：

```bash
python3 -m My_agent.cli media-search \
  --query "新能源汽车购置税政策对车企和消费有什么影响？"
```

该命令只输出待读取的媒体候选；需要完整简报时使用下面的一步式命令。

一步完成新闻媒体与社交平台检索、正文读取和联合简报：

```bash
python3 -m My_agent.cli topic-brief \
  --query "央行降准对银行、债券市场和实体经济融资成本有什么影响？"
```

生成的 Markdown 简报保存在 `My_agent/reports/`。使用 `--no-save` 可只打印结果。
当前 `config/media_sources.yaml` 中的 `official.enabled` 为 `false`，所以官方检索支路
暂时关闭；改为 `true` 即可恢复国务院搜索和官方 RSS。
