(() => {
  "use strict";

  const caseRef = window.CaseSim.caseRef();
  const messageEl = document.getElementById("graph-message");
  const detailEl = document.getElementById("graph-detail");
  const buildPanel = document.getElementById("graph-build");
  const rebuildBtn = document.getElementById("graph-rebuild");
  const generateBtn = document.getElementById("graph-generate");
  const ids = {
    question: document.getElementById("graph-question"),
    asOf: document.getElementById("graph-as-of"),
    horizon: document.getElementById("graph-horizon"),
    maxAgents: document.getElementById("graph-max-agents"),
  };
  let fullGraph = null;
  let filter = "all";
  let pollTimer = null;

  document.getElementById("to-simulation").href = caseRef
    ? `/simulation?case=${encodeURIComponent(caseRef)}`
    : "/simulation";
  document.getElementById("back-brief").href = "/";
  if (!window.d3) {
    messageEl.textContent = "图形组件加载失败，无法显示关系图。";
    messageEl.classList.add("is-error");
    return;
  }
  GraphView.renderDetail(detailEl, null);

  const view = GraphView.mount(document.getElementById("graph-stage"), {
    onSelect: (selection) => GraphView.renderDetail(detailEl, selection),
  });

  function message(text, failed = false) {
    messageEl.textContent = text;
    messageEl.classList.toggle("is-error", failed);
  }

  function applyFilter(data) {
    if (filter === "all") return data;
    const nodes = data.nodes.filter((node) => node.simulation_start);
    const keep = new Set(nodes.map((node) => node.id));
    return {
      ...data,
      nodes,
      edges: data.edges.filter((edge) => keep.has(edge.source) && keep.has(edge.target)),
    };
  }

  function draw() {
    if (!fullGraph) return;
    const data = applyFilter(fullGraph);
    view.render(data);
    const notice = filter === "starters"
      ? "当前只显示被选入本次推演的角色。"
      : (fullGraph.notice || "");
    message(`${data.nodes.length} 个节点，${data.edges.length} 条关系。${notice}`);
  }

  function sanitizeGraph(data) {
    const nodes = Array.isArray(data.nodes) ? data.nodes : [];
    const nodeIds = new Set(nodes.map((node) => node.id));
    const rawEdges = Array.isArray(data.edges) ? data.edges : [];
    const edges = rawEdges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
    const omitted = rawEdges.length - edges.length;
    return {
      ...data,
      nodes,
      edges,
      notice: omitted
        ? `${data.notice || ""} 已忽略 ${omitted} 条端点缺失的关系。`.trim()
        : (data.notice || ""),
    };
  }

  function setBusy(busy) {
    generateBtn.disabled = busy;
    rebuildBtn.disabled = busy;
    ids.question.disabled = busy;
    ids.asOf.disabled = busy;
    ids.horizon.disabled = busy;
    if (ids.maxAgents) ids.maxAgents.disabled = busy;
  }

  async function loadEvidence() {
    const data = await window.CaseSim.api(caseRef, "/graph/evidence");
    fullGraph = sanitizeGraph(data);
    draw();
  }

  async function refresh() {
    if (!caseRef) {
      buildPanel.hidden = true;
      document.getElementById("graph-meta").textContent = "未绑定简报";
      message("请从简报工作区打开知识图谱，这样会加载该简报自己的图谱。", true);
      return;
    }
    const overview = await window.CaseSim.api(caseRef, "/overview");
    const topic = overview.topic || "当前简报";
    document.getElementById("graph-meta").textContent = `${topic} · 来源：真实证据`;
    window.CaseSim.fillForm(ids, overview);
    const running = window.CaseSim.jobRunning(overview);
    setBusy(running);
    generateBtn.hidden = Boolean(overview.graph_ready) && !running;
    rebuildBtn.hidden = !overview.graph_ready;
    if (running) {
      buildPanel.hidden = false;
      message(overview.job.progress || "正在生成知识图谱…");
      pollTimer = setTimeout(() => refresh().catch((error) => message(error.message, true)), 2500);
      return;
    }
    if (overview.job && overview.job.status === "failed") {
      buildPanel.hidden = false;
      message(overview.job.error || "知识图谱生成失败", true);
      return;
    }
    if (!overview.graph_ready) {
      fullGraph = { nodes: [], edges: [] };
      view.render(fullGraph);
      buildPanel.hidden = false;
      message("这份简报还没有知识图谱。填写推演问题、截止时间和时间窗后即可生成。");
      return;
    }
    buildPanel.hidden = false;
    await loadEvidence();
  }

  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      filter = button.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      draw();
    });
  });

  async function generate() {
    const confirmed = generateBtn.textContent.includes("重新")
      ? window.confirm("重新生成会覆盖当前图谱，并清除这份简报的旧推演结果。确定继续？")
      : true;
    if (!confirmed) return;
    setBusy(true);
    try {
      const body = window.CaseSim.collectForm(ids);
      await window.CaseSim.api(caseRef, "/graph", { method: "POST", body: JSON.stringify(body) });
      message("已开始生成知识图谱，页面会自动更新。");
      await refresh();
    } catch (error) {
      message(error.message, true);
      setBusy(false);
    }
  }

  generateBtn.addEventListener("click", generate);
  rebuildBtn.addEventListener("click", generate);
  refresh().catch((error) => message(error.message, true));
})();
