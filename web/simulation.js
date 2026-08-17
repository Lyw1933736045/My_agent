(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  const caseRef = params.get("case") || "case1";
  const caseLabel = caseRef === "case1" ? "案例1" : "当前案例";
  const statusLabels = {
    created: "已创建",
    running: "运行中",
    stopping: "正在停止",
    completed: "已完成",
    failed: "失败",
    stopped: "已停止",
  };
  const labels = window.GraphView.actionLabels;
  const els = {
    message: document.getElementById("sim-message"),
    meta: document.getElementById("sim-meta"),
    start: document.getElementById("sim-start"),
    stop: document.getElementById("sim-stop"),
    rounds: document.getElementById("sim-rounds"),
    status: document.getElementById("sim-status"),
    stats: document.getElementById("sim-stats"),
    list: document.getElementById("sim-rounds-list"),
    report: document.getElementById("sim-report"),
    detail: document.getElementById("graph-detail"),
    dashboard: document.getElementById("analysis-dashboard"),
    networkPanel: document.getElementById("network-panel"),
    dashboardCards: document.getElementById("analysis-cards"),
    actionChart: document.getElementById("action-chart"),
    groupChart: document.getElementById("group-chart"),
    topicChart: document.getElementById("topic-chart"),
    disclaimer: document.getElementById("analysis-disclaimer"),
    finding: document.getElementById("analysis-finding"),
  };

  document.getElementById("to-graph").href = `/graph?case=${encodeURIComponent(caseRef)}`;
  els.meta.textContent = `${caseLabel} · 来源：模拟结果`;
  if (!window.d3) {
    els.message.textContent = "D3 加载失败，无法显示关系图。";
    els.message.classList.add("is-error");
    return;
  }
  GraphView.renderDetail(els.detail, null);

  const view = GraphView.mount(els.networkPanel, {
    onSelect: (selection) => GraphView.renderDetail(els.detail, selection),
  });
  const dashboardView = DashboardView.mount({
    container: els.dashboard,
    cards: els.dashboardCards,
    actionChart: els.actionChart,
    groupChart: els.groupChart,
    topicChart: els.topicChart,
    disclaimer: els.disclaimer,
    finding: els.finding,
  });

  document.querySelectorAll("[data-stage]").forEach((button) => {
    button.addEventListener("click", () => {
      const showNetwork = button.dataset.stage === "network";
      els.dashboard.hidden = showNetwork;
      els.networkPanel.hidden = !showNetwork;
      document.querySelectorAll("[data-stage]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      if (showNetwork) requestAnimationFrame(() => view.resize());
    });
  });

  let latestRunId = null;
  let pollTimer = null;
  let overviewAgents = [];

  async function api(path, options = {}) {
    const response = await fetch(`/api/v1/simulation/cases/${encodeURIComponent(caseRef)}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `请求失败（${response.status}）`);
    return result;
  }

  function message(text, failed = false) {
    els.message.textContent = text;
    els.message.classList.toggle("is-error", failed);
  }

  function setBusy(running) {
    els.start.disabled = running;
    els.stop.disabled = !running;
    els.rounds.disabled = running;
  }

  function showStats(counts) {
    const items = [
      ["智能体", counts.agents],
      ["发帖", counts.posts],
      ["评论", counts.comments],
      ["点赞", counts.likes],
      ["引用", counts.quotes],
      ["转发", counts.reposts],
    ];
    els.stats.innerHTML = "";
    items.forEach(([label, value]) => {
      const item = document.createElement("div");
      const number = document.createElement("strong");
      number.textContent = Number(value || 0);
      const caption = document.createElement("span");
      caption.textContent = label;
      item.append(number, caption);
      els.stats.append(item);
    });
  }

  function countActions(rounds) {
    const counts = { posts: 0, comments: 0, likes: 0, quotes: 0, reposts: 0, agents: overviewAgents.length };
    (rounds || []).forEach((round) => {
      (round.actions || []).forEach((action) => {
        if (action.action_type === "CREATE_POST") counts.posts += 1;
        if (action.action_type === "CREATE_COMMENT") counts.comments += 1;
        if (action.action_type === "LIKE_POST") counts.likes += 1;
        if (action.action_type === "QUOTE_POST") counts.quotes += 1;
        if (action.action_type === "REPOST") counts.reposts += 1;
      });
    });
    return counts;
  }

  function placeholderGraph() {
    return {
      origin: "simulation",
      nodes: overviewAgents.map((agent, index) => ({
        id: `agent_${index}`,
        name: agent.display_name || `智能体 ${index}`,
        node_type: "agent",
        role_group: agent.role_group,
        persona_id: agent.persona_id,
        origin: "simulation",
      })),
      edges: [],
    };
  }

  async function loadAnalysis(runId) {
    if (!runId) return;
    const analysis = await api(`/runs/${runId}/analysis`);
    dashboardView.render(analysis);
    const network = analysis.network || placeholderGraph();
    view.render(network);
    message(`${network.nodes.length} 个智能体，${network.edges.length} 组模拟互动。${network.notice || ""}`);
  }

  function showRounds(result) {
    els.list.innerHTML = "";
    (result.rounds || []).forEach((round) => {
      const block = document.createElement("details");
      block.className = "round-card";
      block.open = round.round === (result.rounds || []).at(-1)?.round;
      const summary = document.createElement("summary");
      summary.textContent = `${round.round === 0 ? "初始事件" : `第 ${round.round} 轮`}｜${(round.actions || []).length} 个行为`;
      block.append(summary);
      (round.actions || []).forEach((action) => {
        const row = document.createElement("div");
        row.className = "round-action";
        const title = document.createElement("strong");
        title.textContent = `${action.agent_name || `智能体 ${action.agent_id}`} · ${labels[action.action_type] || "其他行为"}`;
        const content = document.createElement("p");
        content.textContent = action.content || "（无文字内容）";
        row.append(title, content);
        block.append(row);
      });
      els.list.append(block);
    });
    showStats(countActions(result.rounds));
  }

  function showReport(result) {
    const markdown = result.markdown || "";
    if (window.marked && window.DOMPurify) {
      els.report.innerHTML = DOMPurify.sanitize(marked.parse(markdown));
    } else {
      els.report.textContent = markdown;
    }
  }

  async function loadRun(runId) {
    latestRunId = runId;
    const status = await api(`/runs/${runId}`);
    const running = ["created", "running", "stopping"].includes(status.status);
    setBusy(running);
    const progress = status.total_rounds
      ? ` · ${status.current_round || 0}/${status.total_rounds} 轮`
      : "";
    els.status.textContent = `${statusLabels[status.status] || "状态未知"}${progress}`;
    els.status.className = `status-badge status-${status.status}`;
    if (status.has_result) {
      const rounds = await api(`/runs/${runId}/rounds`);
      showRounds(rounds);
      await loadAnalysis(runId);
    } else {
      showStats({ agents: overviewAgents.length });
      view.render(placeholderGraph());
    }
    if (status.has_report) showReport(await api(`/runs/${runId}/report`));
    return status;
  }

  async function poll() {
    try {
      const status = await loadRun(latestRunId);
      if (["created", "running", "stopping"].includes(status.status)) {
        pollTimer = setTimeout(poll, 2500);
      }
    } catch (error) {
      message(error.message, true);
      setBusy(false);
    }
  }

  async function refresh() {
    clearTimeout(pollTimer);
    const overview = await api("/overview");
    overviewAgents = overview.agents || [];
    showStats({ agents: overviewAgents.length });
    const zep = overview.zep || {};
    if (!zep.ready) {
      setBusy(false);
      els.start.disabled = true;
      message(zep.detail || "Zep 真实图谱未就绪，已终止推演。", true);
      if (overview.latest_run) await loadRun(overview.latest_run.simulation_id);
      else view.render(placeholderGraph());
      return;
    }
    if (overview.latest_run) {
      await loadRun(overview.latest_run.simulation_id);
      if (["created", "running", "stopping"].includes(overview.latest_run.status)) poll();
    } else {
      setBusy(false);
      els.status.textContent = "未运行";
      view.render(placeholderGraph());
      message(`已载入 ${overviewAgents.length} 个智能体。设置轮数后启动推演。`);
    }
  }

  els.start.addEventListener("click", async () => {
    setBusy(true);
    try {
      const rounds = Number(els.rounds.value) || 4;
      const status = await api("/runs", { method: "POST", body: JSON.stringify({ rounds }) });
      latestRunId = status.simulation_id;
      message("推演已启动，页面会自动更新。");
      await poll();
    } catch (error) {
      message(error.message, true);
      setBusy(false);
    }
  });

  els.stop.addEventListener("click", async () => {
    if (!latestRunId) return;
    els.stop.disabled = true;
    try {
      await api(`/runs/${latestRunId}/stop`, { method: "POST" });
      message("已发送停止请求。");
      await poll();
    } catch (error) {
      message(error.message, true);
      setBusy(false);
    }
  });

  refresh().catch((error) => message(error.message, true));
})();
