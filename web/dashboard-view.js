(() => {
  "use strict";

  const COLORS = ["#5f7f98", "#d98b45", "#759b88", "#9a789e", "#b7a45b"];

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function resetSvg(element, height) {
    const width = Math.max(320, Math.floor(element.parentElement.clientWidth - 36));
    const svg = d3.select(element);
    svg.selectAll("*").remove();
    svg.attr("viewBox", `0 0 ${width} ${height}`).attr("height", height);
    return { svg, width, height };
  }

  function renderCards(container, analysis) {
    const summary = analysis.summary || {};
    const cards = [
      ["智能体", summary.agent_count, "个"],
      ["模拟轮次", summary.round_count, "轮"],
      ["行为总量", summary.action_count, "次"],
      ["有效互动", summary.interaction_count, "次"],
    ];
    container.innerHTML = cards.map(([label, value, unit]) => `
      <div><span>${label}</span><strong>${Number(value || 0)}</strong><small>${unit}</small></div>
    `).join("");
  }

  function renderActions(element, analysis) {
    const rows = analysis.round_metrics || [];
    const labels = analysis.action_labels || {};
    const keys = Object.keys(labels);
    const { svg, width, height } = resetSvg(element, 300);
    const margin = { top: 42, right: 20, bottom: 42, left: 42 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;
    const root = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
    const x = d3.scaleBand().domain(rows.map((row) => String(row.round))).range([0, innerWidth]).padding(0.26);
    const stacked = d3.stack().keys(keys).value((row, key) => row.actions?.[key] || 0)(rows);
    const maxValue = d3.max(rows, (row) => row.total_actions) || 1;
    const y = d3.scaleLinear().domain([0, maxValue]).nice().range([innerHeight, 0]);
    root.append("g").attr("class", "chart-grid").call(d3.axisLeft(y).ticks(5).tickSize(-innerWidth).tickFormat(""));
    root.selectAll("g.action-layer").data(stacked).join("g")
      .attr("class", "action-layer")
      .attr("fill", (_, index) => COLORS[index % COLORS.length])
      .selectAll("rect").data((layer) => layer).join("rect")
      .attr("x", (row) => x(String(row.data.round)))
      .attr("y", (row) => y(row[1]))
      .attr("height", (row) => Math.max(0, y(row[0]) - y(row[1])))
      .attr("width", x.bandwidth())
      .attr("rx", 3);
    root.append("g").attr("transform", `translate(0,${innerHeight})`).call(
      d3.axisBottom(x).tickFormat((round) => round === "0" ? "初始" : `第${round}轮`),
    );
    root.append("g").call(d3.axisLeft(y).ticks(5).tickFormat(d3.format("d")));
    const legend = svg.append("g").attr("class", "chart-legend").attr("transform", `translate(${margin.left},16)`);
    keys.forEach((key, index) => {
      const item = legend.append("g").attr("transform", `translate(${index * Math.max(62, innerWidth / keys.length)},0)`);
      item.append("circle").attr("r", 4).attr("fill", COLORS[index]);
      item.append("text").attr("x", 8).attr("y", 4).text(labels[key]);
    });
  }

  function renderGroups(element, analysis) {
    const rows = (analysis.group_metrics || []).filter((row) => row.expression_mean !== null);
    const height = Math.max(260, rows.length * 46 + 82);
    const { svg, width } = resetSvg(element, height);
    const margin = { top: 26, right: 24, bottom: 42, left: 90 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;
    const root = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
    const x = d3.scaleLinear().domain([-1, 1]).range([0, innerWidth]);
    const y = d3.scaleBand().domain(rows.map((row) => row.label)).range([0, innerHeight]).padding(0.4);
    root.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", 0).attr("y2", innerHeight).attr("class", "zero-line");
    root.selectAll("line.group-track").data(rows).join("line")
      .attr("class", "group-track").attr("x1", x(-1)).attr("x2", x(1))
      .attr("y1", (row) => y(row.label) + y.bandwidth() / 2).attr("y2", (row) => y(row.label) + y.bandwidth() / 2);
    rows.forEach((row, rowIndex) => {
      svg.append("g").selectAll("circle").data(row.individual_values || []).join("circle")
        .attr("cx", (value) => margin.left + x(value))
        .attr("cy", margin.top + y(row.label) + y.bandwidth() / 2)
        .attr("r", 4).attr("fill", COLORS[rowIndex % COLORS.length]).attr("opacity", 0.35);
    });
    root.selectAll("circle.group-mean").data(rows).join("circle")
      .attr("class", "group-mean").attr("cx", (row) => x(row.expression_mean))
      .attr("cy", (row) => y(row.label) + y.bandwidth() / 2).attr("r", 7)
      .attr("fill", (_, index) => COLORS[index % COLORS.length]);
    root.selectAll("text.group-value").data(rows).join("text")
      .attr("class", "group-value").attr("x", (row) => x(row.expression_mean) + 11)
      .attr("y", (row) => y(row.label) + y.bandwidth() / 2 + 4)
      .text((row) => `${row.expression_mean.toFixed(2)}｜${row.agent_count}人`);
    root.append("g").call(d3.axisLeft(y).tickSize(0)).call((axis) => axis.select(".domain").remove());
    root.append("g").attr("transform", `translate(0,${innerHeight})`).call(
      d3.axisBottom(x).tickValues([-1, -0.5, 0, 0.5, 1]).tickFormat(d3.format(".1f")),
    );
  }

  function renderTopics(element, analysis) {
    const topics = analysis.topic_evolution || [];
    const rounds = analysis.round_metrics || [];
    const height = Math.max(260, topics.length * 38 + 82);
    const { svg, width } = resetSvg(element, height);
    const margin = { top: 24, right: 18, bottom: 46, left: 124 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;
    const root = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
    const x = d3.scaleBand().domain(rounds.map((row) => String(row.round))).range([0, innerWidth]).padding(0.08);
    const y = d3.scaleBand().domain(topics.map((row) => row.topic)).range([0, innerHeight]).padding(0.08);
    const color = d3.scaleSequential().domain([0, 1]).interpolator(d3.interpolateRgb("#edf1ef", "#2f756b"));
    const cells = topics.flatMap((topic) => topic.rounds.map((round) => ({ topic: topic.topic, ...round })));
    root.selectAll("rect").data(cells).join("rect")
      .attr("x", (cell) => x(String(cell.round))).attr("y", (cell) => y(cell.topic))
      .attr("width", x.bandwidth()).attr("height", y.bandwidth()).attr("rx", 4)
      .attr("fill", (cell) => color(cell.attention));
    root.selectAll("text.cell-value").data(cells).join("text")
      .attr("class", "cell-value").attr("x", (cell) => x(String(cell.round)) + x.bandwidth() / 2)
      .attr("y", (cell) => y(cell.topic) + y.bandwidth() / 2 + 4)
      .attr("fill", (cell) => cell.attention > 0.55 ? "white" : "#31465a")
      .text((cell) => cell.mentions || "");
    root.append("g").call(d3.axisLeft(y).tickSize(0)).call((axis) => axis.select(".domain").remove());
    root.append("g").attr("transform", `translate(0,${innerHeight})`).call(
      d3.axisBottom(x).tickFormat((round) => round === "0" ? "初始" : `第${round}轮`),
    );
  }

  function keywordsForScope(analysis, scope) {
    const hotspots = analysis.keyword_hotspots || {};
    if (scope === "all") return hotspots.all || [];
    const round = (hotspots.rounds || []).find((item) => String(item.round) === String(scope));
    return round?.keywords || [];
  }

  function defaultKeywordScope(analysis) {
    const rounds = analysis.keyword_hotspots?.rounds || analysis.round_metrics || [];
    if (!rounds.length) return "all";
    return String(rounds[rounds.length - 1].round);
  }

  function renderKeywordScope(container, analysis, scope, onChange) {
    if (!container) return;
    const rounds = analysis.keyword_hotspots?.rounds || analysis.round_metrics || [];
    const items = [
      ...rounds.map((row) => [String(row.round), row.round === 0 ? "初始" : `第${row.round}轮`]),
      ["all", "总推演"],
    ];
    container.innerHTML = "";
    items.forEach(([value, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.dataset.scope = value;
      button.classList.toggle("is-active", value === String(scope));
      button.addEventListener("click", () => onChange(value));
      container.append(button);
    });
  }

  function renderKeywordBubbles(element, tooltip, analysis, scope) {
    const keywords = keywordsForScope(analysis, scope);
    const { svg, width, height } = resetSvg(element, 360);
    if (!keywords.length) {
      svg.append("text")
        .attr("x", width / 2)
        .attr("y", height / 2)
        .attr("text-anchor", "middle")
        .text("该范围文本不足以统计关键词");
      return;
    }
    const packed = d3.pack().size([width - 16, height - 16]).padding(5)(
      d3.hierarchy({ children: keywords }).sum((item) => item.count || 0)
    );
    const extent = d3.extent(keywords, (item) => item.count);
    const color = d3.scaleLinear()
      .domain([extent[0] || 1, extent[1] || 1])
      .range(["#d7e4e0", "#2f756b"]);
    const nodes = svg.append("g").attr("transform", "translate(8,8)")
      .selectAll("g")
      .data(packed.leaves())
      .join("g")
      .attr("transform", (node) => `translate(${node.x},${node.y})`);
    nodes.append("circle")
      .attr("class", "keyword-bubble")
      .attr("r", (node) => node.r)
      .attr("fill", (node) => color(node.data.count))
      .attr("fill-opacity", 0.92)
      .on("mousemove", (event, node) => {
        if (!tooltip) return;
        tooltip.hidden = false;
        tooltip.innerHTML = `<strong>${escapeHtml(node.data.keyword)}</strong>出现：${Number(node.data.count) || 0} 次<br>涉及用户：${Number(node.data.agent_count) || 0} 人`;
        const card = element.closest(".keyword-card") || element.parentElement;
        const bounds = card.getBoundingClientRect();
        tooltip.style.left = `${Math.min(bounds.width - 168, Math.max(8, event.clientX - bounds.left + 12))}px`;
        tooltip.style.top = `${Math.max(8, event.clientY - bounds.top - 48)}px`;
      })
      .on("mouseleave", () => {
        if (tooltip) tooltip.hidden = true;
      });
    nodes.append("text")
      .attr("class", "keyword-label")
      .attr("dy", "0.35em")
      .attr("fill", (node) => node.data.count >= (d3.median(keywords, (item) => item.count) || 0) ? "#f7faf9" : "#21303c")
      .style("font-size", (node) => `${Math.max(9, Math.min(16, node.r / 2.4))}px`)
      .text((node) => node.r < 14 ? "" : node.data.keyword);
  }

  function mount(options) {
    let current = null;
    let scope = null;
    const applyScope = (next) => {
      scope = next;
      if (!current) return;
      renderKeywordScope(options.keywordScope, current, scope, applyScope);
      renderKeywordBubbles(options.keywordChart, options.keywordTooltip, current, scope);
    };
    const render = (analysis) => {
      if (!analysis || !window.d3) return;
      current = analysis;
      const available = new Set([
        "all",
        ...(analysis.keyword_hotspots?.rounds || []).map((item) => String(item.round)),
      ]);
      if (!scope || !available.has(String(scope))) scope = defaultKeywordScope(analysis);
      renderCards(options.cards, analysis);
      renderActions(options.actionChart, analysis);
      renderGroups(options.groupChart, analysis);
      renderTopics(options.topicChart, analysis);
      applyScope(scope);
      options.disclaimer.textContent = analysis.disclaimer || "以下指标只描述本次模拟结果。";
      options.finding.textContent = analysis.summary?.finding || "当前没有足够数据生成结论。";
    };
    const observer = new ResizeObserver(() => current && render(current));
    observer.observe(options.container);
    return { render, destroy: () => observer.disconnect() };
  }

  window.DashboardView = { mount };
})();
