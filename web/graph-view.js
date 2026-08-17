(() => {
  "use strict";

  const ACTION_LABELS = {
    CREATE_POST: "发帖",
    CREATE_COMMENT: "评论",
    LIKE_POST: "点赞",
    DISLIKE_POST: "点踩",
    REPOST: "转发",
    QUOTE_POST: "引用",
    FOLLOW: "关注",
    DO_NOTHING: "未行动",
  };

  const TYPE_LABELS = {
    actor: "可发声主体",
    concept: "关联知识",
    agent: "智能体",
  };

  const RELATION_LABELS = {
    SUPPORTS: "支持",
    EXPAND_STANDARD_TO: "将标准扩展至",
    INVESTIGATED: "调查",
    RECOVERED_SINCE: "自此恢复",
    WITHSTOOD: "经受住",
    SUPPORTS_BUILDING: "支持建设",
    SUPPORTS_DOMESTIC_LISTING_FOR: "支持国内上市",
    HAS_BREAKTHROUGH_IN: "在相关领域取得突破",
    RELATES_TO: "关联",
    INVESTIGATED_ILLEGAL_ACTIVITY: "调查违法活动",
    INCLUDES_FIELD: "包含领域",
    WILL_ISSUE: "将发布",
    STATES: "陈述",
    IS_A: "属于",
    IMPROVES_THROUGH: "通过相关方式改善",
    STATED_ACTION_BY: "由其提出行动",
    EMBRACES: "拥抱",
    BELIEVES_IN: "相信",
    HAS_EFFICIENCY: "具有效率",
    WILL_CRACK_DOWN_ON: "将打击",
    COLLECTED_FINE: "收缴罚款",
    EXPRESSED_SUPPORT_FOR_RESEARCH_AND_DEVELOPMENT_OF: "支持研发",
    DEEPENS_REFORMS_IN: "深化改革",
    INDICATED_SUPPORT_FOR_LAUNCHING: "表示支持推出",
    PROPOSES_IMPLEMENTATION_OF: "提出实施",
    CHAIRMAN_OF: "担任主席",
    AFFECTS: "影响",
    SUMMARIZED: "总结",
    WILL_PREPARE_FOR: "将准备",
    CONTINUES_TO_IMPROVE: "持续改善",
    DEEPLY_EMPOWERS: "深度赋能",
    HOLDS_SHARES_IN: "持有股份",
    ENGAGES_IN: "参与",
    ISSUED: "发布",
    SOLVES_PROBLEM_FOR: "解决相关问题",
    IMPLEMENTED: "实施",
    PROMOTES_DEVELOPMENT_IN: "促进发展",
    MAKES_STATEMENT_ABOUT: "发表关于相关事项的声明",
    PROPOSES_PILOT_FOR: "提出试点",
    CRACKS_DOWN_ON: "打击",
    PARTICIPATES_IN: "参与",
    APPLICABLE_TO: "适用于",
    WILL_REVISE: "将修订",
    AIMS_TO_CONSOLIDATE_MOMENTUM_IN: "旨在巩固发展势头",
  };

  function nodeLabel(node) {
    return String(node?.display_name || node?.name_zh || node?.name || "未命名节点").trim();
  }

  function explanation(node) {
    const summary = String(node?.summary_zh || node?.summary || "").trim();
    return summary || `该${TYPE_LABELS[node?.node_type] || "知识节点"}来自真实证据抽取。`;
  }

  function chineseValue(value, fallback) {
    const text = String(value || "").trim();
    return text || fallback;
  }

  function radius(node) {
    if (node.node_type === "agent") {
      return Math.min(22, 9 + Math.sqrt(Number(node.influence_count || node.activity_count || 1)) * 1.7);
    }
    return node.node_type === "actor" ? 12 : 6;
  }

  function labeled(node) {
    return ["actor", "concept", "agent"].includes(node.node_type);
  }

  function edgeName(edge) {
    return edge.name_zh || ACTION_LABELS[edge.name] || RELATION_LABELS[edge.name] || "相关关系";
  }

  function assignCurves(links) {
    const buckets = new Map();
    links.forEach((link) => {
      const key = `${link.source}\0${link.target}`;
      const list = buckets.get(key) || [];
      list.push(link);
      buckets.set(key, list);
    });
    buckets.forEach((list) => {
      list.forEach((link, index) => {
        link.curve = (index - (list.length - 1) / 2) * 22;
      });
    });
  }

  function endpoints(link) {
    const dx = link.target.x - link.source.x;
    const dy = link.target.y - link.source.y;
    const dist = Math.hypot(dx, dy) || 1;
    const start = radius(link.source);
    const end = radius(link.target) + 8;
    return {
      x1: link.source.x + (dx / dist) * start,
      y1: link.source.y + (dy / dist) * start,
      x2: link.target.x - (dx / dist) * end,
      y2: link.target.y - (dy / dist) * end,
    };
  }

  function linkPath(link) {
    if (link.source.id === link.target.id) {
      const x = link.source.x;
      const y = link.source.y;
      const sweep = 28 + Math.abs(link.curve || 0) * 0.4;
      return `M ${x},${y - 8} C ${x + sweep},${y - 46} ${x - sweep},${y - 46} ${x},${y - 8}`;
    }
    const point = endpoints(link);
    const mx = (point.x1 + point.x2) / 2;
    const my = (point.y1 + point.y2) / 2;
    const dx = point.x2 - point.x1;
    const dy = point.y2 - point.y1;
    const dist = Math.hypot(dx, dy) || 1;
    const ox = (-dy / dist) * (link.curve || 0);
    const oy = (dx / dist) * (link.curve || 0);
    return `M${point.x1},${point.y1} Q${mx + ox},${my + oy} ${point.x2},${point.y2}`;
  }

  function row(label, value) {
    if (value === null || value === undefined || value === "") return "";
    const text = Array.isArray(value) ? value.filter(Boolean).join("、") : String(value);
    if (!text) return "";
    return `<div class="detail-row"><span>${label}</span><p>${escapeHtml(text)}</p></div>`;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function renderDetail(container, selection) {
    if (!container) return;
    if (!selection) {
      container.innerHTML = "<p>点击节点或关系查看详细信息。</p>";
      return;
    }
    const item = selection.data;
    const origin = item.origin === "simulation" ? "simulation" : "real";
    const originText = origin === "simulation" ? "模拟互动" : "真实证据";
    if (selection.kind === "edge") {
      const samples = (item.samples || []).map((text) => `<blockquote>${escapeHtml(text)}</blockquote>`).join("");
      container.innerHTML = `
        <h4>${escapeHtml(edgeName(item))}</h4>
        <span class="origin-pill is-${origin}">来源：${originText}</span>
        ${row("起点", item.source_name || item.source)}
        ${row("终点", item.target_name || item.target)}
        ${row("次数", item.count)}
        ${row("轮次", item.rounds)}
        ${row("关系说明", item.summary_zh || item.summary)}
        ${row("生效时间", item.valid_at)}
        ${row("失效时间", item.invalid_at || item.expired_at)}
        ${item.source_ids?.length ? row("证据来源", `已关联 ${item.source_ids.length} 条证据`) : ""}
        ${samples ? `<h5>内容摘录</h5>${samples}` : ""}
      `;
      return;
    }
    const traces = (item.trace || [])
      .filter((trace) => trace && (trace.url || trace.title))
      .slice(0, 6)
      .map((trace) => {
      const label = "查看来源";
        return trace.url
          ? `<a href="${escapeHtml(trace.url)}" target="_blank" rel="noopener">${label}</a>`
          : `<p>${label}</p>`;
      })
      .join("");
    container.innerHTML = `
      <h4>${escapeHtml(nodeLabel(item))}</h4>
      <span class="origin-pill is-${origin}">来源：${originText}</span>
      ${row("类型", TYPE_LABELS[item.node_type] || "知识节点")}
      ${row("主体类型", chineseValue(item.entity_type_zh, "未分类知识节点"))}
      ${row("角色", chineseValue(item.role_group, "未标注"))}
      ${row("发出互动", item.outgoing_count)}
      ${row("收到互动", item.incoming_count)}
      ${row("言论类型", chineseValue(item.claim_type, "未标注"))}
      ${row("事实类型", chineseValue(item.assertion_type, "未标注"))}
      ${row("时间", item.event_time)}
      ${row("说明", explanation(item))}
      ${item.source_ids?.length ? row("证据来源", `已关联 ${item.source_ids.length} 条证据`) : ""}
      ${traces ? `<h5>来源</h5>${traces}` : ""}
    `;
  }

  function mount(container, options = {}) {
    if (!container) throw new Error("GraphView 需要一个容器");
    const svgEl = container.querySelector("svg") || container.appendChild(
      document.createElementNS("http://www.w3.org/2000/svg", "svg"),
    );
    let layout = null;
    let current = null;
    let selectedId = null;
    const onSelect = options.onSelect || (() => {});

    function size() {
      return {
        width: Math.floor(container.clientWidth),
        height: Math.floor(container.clientHeight),
      };
    }

    function draw(data) {
      if (!window.d3 || !data) return;
      const { width, height } = size();
      if (width < 40 || height < 40) return;

      if (layout) layout.stop();
      const origin = data.origin === "simulation" ? "simulation" : "real";
      const nodes = (data.nodes || []).map((node) => ({ ...node }));
      const links = (data.edges || []).map((edge) => ({
        ...edge,
        source_name: edge.source_name,
        target_name: edge.target_name,
      }));
      assignCurves(links);

      const svg = d3.select(svgEl);
      svg.on(".zoom", null);
      svg.selectAll("*").remove();
      svg
        .attr("width", width)
        .attr("height", height)
        .attr("viewBox", `0 0 ${width} ${height}`)
        .attr("class", `network-svg origin-${origin}`);

      const defs = svg.append("defs");
      defs.append("marker")
        .attr("id", "arrow-real")
        .attr("viewBox", "0 -4 8 8")
        .attr("refX", 8)
        .attr("refY", 0)
        .attr("markerWidth", 7)
        .attr("markerHeight", 7)
        .attr("orient", "auto")
        .append("path")
        .attr("d", "M0,-4L8,0L0,4")
        .attr("fill", "#7f9690");
      defs.append("marker")
        .attr("id", "arrow-sim")
        .attr("viewBox", "0 -4 8 8")
        .attr("refX", 8)
        .attr("refY", 0)
        .attr("markerWidth", 7)
        .attr("markerHeight", 7)
        .attr("orient", "auto")
        .append("path")
        .attr("d", "M0,-4L8,0L0,4")
        .attr("fill", "#b47b36");

      const root = svg.append("g");
      svg.call(
        d3.zoom()
          .scaleExtent([0.25, 3.5])
          .filter((event) => {
            if (event.type === "wheel") return true;
            return event.target === svgEl;
          })
          .on("zoom", (event) => root.attr("transform", event.transform)),
      );

      const linkGroup = root.append("g").attr("class", "network-links");
      const lines = linkGroup.selectAll("path")
        .data(links)
        .join("path")
        .attr("class", origin === "simulation" ? "simulation-edge" : "evidence-edge")
        .attr("marker-end", origin === "simulation" ? "url(#arrow-sim)" : "url(#arrow-real)")
        .attr("stroke-width", (edge) => Math.min(4.5, 1.2 + Math.sqrt(edge.count || 1)))
        .on("click", (event, edge) => {
          event.stopPropagation();
          selectedId = edge.id;
          groups.classed("is-selected", false);
          lines.classed("is-selected", (item) => item.id === edge.id);
          onSelect({ kind: "edge", data: {
            ...edge,
            source: edge.source.id || edge.source,
            target: edge.target.id || edge.target,
            source_name: edge.source.name || edge.source_name,
            target_name: edge.target.name || edge.target_name,
          } });
        });
      lines.append("title").text((edge) => {
        const count = edge.count ? ` × ${edge.count}` : "";
        return `${edgeName(edge)}${count}`;
      });

      const labelLayer = root.append("g").attr("class", "network-edge-labels");
      const edgeLabels = origin === "simulation"
        ? labelLayer.selectAll("text").data(links).join("text")
          .attr("class", "edge-label")
          .text((edge) => edgeName(edge))
        : labelLayer.selectAll("text");

      const groups = root.append("g").selectAll("g")
        .data(nodes)
        .join("g")
        .attr("class", (node) => `network-node type-${node.node_type}${node.id === selectedId ? " is-selected" : ""}`)
        .call(d3.drag()
          .on("start", (event, node) => {
            if (!event.active) layout.alphaTarget(0.25).restart();
            node.fx = node.x;
            node.fy = node.y;
          })
          .on("drag", (event, node) => {
            node.fx = event.x;
            node.fy = event.y;
          })
          .on("end", (event, node) => {
            if (!event.active) layout.alphaTarget(0);
            node.fx = null;
            node.fy = null;
          }));

      groups.append("circle")
        .attr("r", radius)
        .style("fill", (node) => node.color || null)
        .on("click", (event, node) => {
          event.stopPropagation();
          selectedId = node.id;
          groups.classed("is-selected", (item) => item.id === node.id);
          lines.classed("is-selected", false);
          onSelect({ kind: "node", data: node });
        });
      groups.filter(labeled).append("text")
        .attr("x", 14)
        .attr("y", 4)
        .attr("class", "network-label")
        .text((node) => nodeLabel(node));
      groups.append("title").text((node) => explanation(node));

      svg.on("click", () => {
        selectedId = null;
        groups.classed("is-selected", false);
        lines.classed("is-selected", false);
        onSelect(null);
      });

      layout = d3.forceSimulation(nodes)
        .force("link", d3.forceLink(links).id((node) => node.id).distance(origin === "real" ? 72 : 130))
        .force("charge", d3.forceManyBody().strength(origin === "real" ? -90 : -260))
        .force("center", d3.forceCenter(width / 2, height / 2))
        .force("collision", d3.forceCollide().radius((node) => radius(node) + (labeled(node) ? 16 : 8)))
        .force("x", d3.forceX(width / 2).strength(0.05))
        .force("y", d3.forceY(height / 2).strength(0.05));

      layout.on("tick", () => {
        lines.attr("d", linkPath);
        groups.attr("transform", (node) => `translate(${node.x},${node.y})`);
        if (origin === "simulation") {
          edgeLabels.attr("x", (edge) => {
            if (edge.source.id === edge.target.id) return edge.source.x;
            const point = endpoints(edge);
            return (point.x1 + point.x2) / 2;
          }).attr("y", (edge) => {
            if (edge.source.id === edge.target.id) return edge.source.y - 48;
            const point = endpoints(edge);
            return (point.y1 + point.y2) / 2 - 4;
          });
        }
      });
    }

    const observer = new ResizeObserver(() => draw(current));
    observer.observe(container);

    return {
      render(data) {
        current = data;
        selectedId = null;
        draw(data);
      },
      resize() {
        draw(current);
      },
      destroy() {
        observer.disconnect();
        if (layout) layout.stop();
      },
    };
  }

  window.GraphView = {
    mount,
    renderDetail,
    actionLabels: ACTION_LABELS,
    typeLabels: TYPE_LABELS,
  };
})();
