(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  const caseRef = params.get("case") || "case1";
  const caseLabel = caseRef === "case1" ? "案例1" : "当前案例";
  const messageEl = document.getElementById("graph-message");
  const detailEl = document.getElementById("graph-detail");
  let fullGraph = null;
  let filter = "actors";

  document.getElementById("to-simulation").href = `/simulation?case=${encodeURIComponent(caseRef)}`;
  document.getElementById("graph-meta").textContent = `${caseLabel} · 来源：真实证据`;
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
    if (filter === "actors") {
      const nodes = data.nodes.filter((node) => node.is_actor || node.node_type === "actor");
      const keep = new Set(nodes.map((node) => node.id));
      return {
        ...data,
        nodes,
        edges: data.edges.filter((edge) => keep.has(edge.source) && keep.has(edge.target)),
      };
    }
    const actorIds = new Set(
      data.nodes.filter((node) => node.is_actor || node.node_type === "actor").map((node) => node.id),
    );
    const keep = new Set(actorIds);
    data.edges.forEach((edge) => {
      if (actorIds.has(edge.source) || actorIds.has(edge.target)) {
        keep.add(edge.source);
        keep.add(edge.target);
      }
    });
    return {
      ...data,
      nodes: data.nodes.filter((node) => keep.has(node.id)),
      edges: data.edges.filter((edge) => keep.has(edge.source) && keep.has(edge.target)),
    };
  }

  function draw() {
    if (!fullGraph) return;
    const data = applyFilter(fullGraph);
    view.render(data);
    const notice = filter === "actors"
      ? "当前只显示通过本体筛选、能够发声互动的主体。"
      : filter === "context"
        ? "当前显示主体及其直接关联的知识节点。"
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

  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      filter = button.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      draw();
    });
  });

  fetch(`/api/v1/simulation/cases/${encodeURIComponent(caseRef)}/graph/evidence`)
    .then(async (response) => {
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || `请求失败（${response.status}）`);
      return result;
    })
    .then((data) => {
      fullGraph = sanitizeGraph(data);
      draw();
    })
    .catch((error) => message(error.message || String(error), true));
})();
