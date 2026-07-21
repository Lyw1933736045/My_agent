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
