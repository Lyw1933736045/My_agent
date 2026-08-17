(() => {
  const POLL_MS = 2000;

  const els = {
    form: document.getElementById("query-form"),
    input: document.getElementById("query-input"),
    submitBtn: document.getElementById("submit-btn"),
    toggleAdvanced: document.getElementById("toggle-advanced"),
    advancedPanel: document.getElementById("advanced-panel"),
    requireReview: document.getElementById("require-review"),
    sourceOptions: document.querySelectorAll("[data-source]"),
    planSection: document.getElementById("plan-section"),
    planTopic: document.getElementById("plan-topic"),
    queryList: document.getElementById("query-list"),
    addQuery: document.getElementById("add-query"),
    approveBtn: document.getElementById("approve-btn"),
    progressSection: document.getElementById("progress-section"),
    progressText: document.getElementById("progress-text"),
    cancelBtn: document.getElementById("cancel-btn"),
    errorSection: document.getElementById("error-section"),
    errorText: document.getElementById("error-text"),
    errorSources: document.getElementById("error-sources"),
    retryBtn: document.getElementById("retry-btn"),
    reportSection: document.getElementById("report-section"),
    reportTitle: document.getElementById("report-title"),
    reportBody: document.getElementById("report-body"),
    qaPanel: document.getElementById("qa-panel"),
    qaMode: document.getElementById("qa-mode"),
    qaQuestion: document.getElementById("qa-question"),
    qaSubmit: document.getElementById("qa-submit"),
    qaStatus: document.getElementById("qa-status"),
    qaAnswer: document.getElementById("qa-answer"),
    qaCitations: document.getElementById("qa-citations"),
    reportSources: document.getElementById("report-sources"),
    providerQueryPanel: document.getElementById("provider-query-panel"),
    tavilyQueryInput: document.getElementById("tavily-query-input"),
    weiboQueryInput: document.getElementById("weibo-query-input"),
    rerunQueryBtn: document.getElementById("rerun-query-btn"),
    appendSourceOptions: document.querySelectorAll("[data-append-source]"),
    generateBriefBtn: document.getElementById("generate-brief-btn"),
    analysisReadyMessage: document.getElementById("analysis-ready-message"),
    copyBtn: document.getElementById("copy-btn"),
    downloadMd: document.getElementById("download-md"),
    exportPdf: document.getElementById("export-pdf"),
    viewHtml: document.getElementById("view-html"),
    newBtn: document.getElementById("new-btn"),
    evaluationPanel: document.getElementById("evaluation-panel"),
    referenceInput: document.getElementById("reference-input"),
    evaluateBtn: document.getElementById("evaluate-btn"),
    evaluationProgress: document.getElementById("evaluation-progress"),
    evaluationResult: document.getElementById("evaluation-result"),
    workspaceLinks: document.getElementById("workspace-links"),
    openGraph: document.getElementById("open-graph"),
    openSimulation: document.getElementById("open-simulation"),
  };

  let currentRunId = null;
  let currentCaseId = null;
  let latestMarkdown = "";
  let pollTimer = null;
  let evaluationTimer = null;

  function show(el) {
    el.hidden = false;
  }

  function hide(el) {
    el.hidden = true;
  }

  function setBusy(busy) {
    els.submitBtn.disabled = busy;
    els.approveBtn.disabled = busy;
    els.input.readOnly = busy;
  }

  async function cancelRun() {
    if (!currentRunId) return;
    els.cancelBtn.disabled = true;
    try {
      await api(`/api/v1/runs/${currentRunId}/cancel`, { method: "POST" });
      stopPolling();
      els.progressText.textContent = "任务已终止";
      els.cancelBtn.hidden = true;
      setBusy(false);
    } catch (error) {
      els.progressText.textContent = error.message || String(error);
      els.cancelBtn.disabled = false;
    }
  }

  function resetStages() {
    hide(els.planSection);
    hide(els.progressSection);
    hide(els.errorSection);
    hide(els.reportSection);
    hide(els.errorSources);
    hide(els.reportSources);
    els.errorSources.innerHTML = "";
    els.reportSources.innerHTML = "";
    stopPolling();
    els.cancelBtn.hidden = true;
    els.generateBriefBtn.hidden = true;
    els.generateBriefBtn.disabled = false;
    els.analysisReadyMessage.hidden = true;
    if (evaluationTimer) clearTimeout(evaluationTimer);
    els.referenceInput.value = "";
    els.evaluationResult.hidden = true;
    els.evaluationResult.innerHTML = "";
    els.evaluationProgress.hidden = true;
    currentCaseId = null;
    hide(els.workspaceLinks);
    hide(els.qaPanel);
    hide(els.qaAnswer);
    hide(els.qaCitations);
    hide(els.qaStatus);
    els.qaAnswer.textContent = "";
    els.qaCitations.innerHTML = "";
  }

  function showError(message, sources) {
    hide(els.progressSection);
    hide(els.planSection);
    hide(els.reportSection);
    els.errorText.textContent = message;
    renderSources(els.errorSources, sources || []);
    show(els.errorSection);
    setBusy(false);
  }

  function stopPolling() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let data = null;
    const text = await response.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { detail: text };
      }
    }
    if (!response.ok) {
      const detail = data && data.detail;
      const message =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((item) => item.msg || JSON.stringify(item)).join("；")
            : `请求失败（${response.status}）`;
      throw new Error(message);
    }
    return data;
  }

  function renderQueryList(queries) {
    els.queryList.innerHTML = "";
    const values = queries.length ? queries : [""];
    values.forEach((query) => {
      const li = document.createElement("li");
      li.className = "query-item";
      const input = document.createElement("input");
      input.type = "text";
      input.value = query;
      input.maxLength = 200;
      input.placeholder = "检索词";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove";
      remove.textContent = "删除";
      remove.addEventListener("click", () => {
        if (els.queryList.children.length <= 1) {
          input.value = "";
          input.focus();
          return;
        }
        li.remove();
      });
      li.append(input, remove);
      els.queryList.append(li);
    });
  }

  function collectQueries() {
    return Array.from(els.queryList.querySelectorAll("input"))
      .map((input) => input.value.trim())
      .filter(Boolean);
  }

  function sourceItem(item) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "source-name";
    name.textContent = `${item.provider} / ${item.name}`;
    const meta = document.createElement("span");
    meta.className = "source-meta";
    meta.textContent = item.detail || (item.ok ? "成功" : "失败");
    li.append(name, meta);
    return li;
  }

  function renderSources(container, sources) {
    container.innerHTML = "";
    if (!sources || !sources.length) {
      hide(container);
      return;
    }

    const success = sources.filter((item) => item.ok);
    const failed = sources.filter((item) => !item.ok);

    const summary = document.createElement("p");
    summary.className = "source-summary";
    summary.textContent = `来源请求：成功 ${success.length}，失败 ${failed.length}，共 ${sources.length}`;

    const columns = document.createElement("div");
    columns.className = "source-columns";

    const okCol = document.createElement("div");
    okCol.className = "ok";
    const okTitle = document.createElement("h3");
    okTitle.textContent = `成功（${success.length}）`;
    okCol.append(okTitle);
    if (success.length) {
      const okList = document.createElement("ul");
      success.forEach((item) => okList.append(sourceItem(item)));
      okCol.append(okList);
    } else {
      const empty = document.createElement("p");
      empty.className = "source-empty";
      empty.textContent = "暂无成功源";
      okCol.append(empty);
    }

    const badCol = document.createElement("div");
    badCol.className = "bad";
    const badTitle = document.createElement("h3");
    badTitle.textContent = `失败（${failed.length}）`;
    badCol.append(badTitle);
    if (failed.length) {
      const badList = document.createElement("ul");
      failed.forEach((item) => badList.append(sourceItem(item)));
      badCol.append(badList);
    } else {
      const empty = document.createElement("p");
      empty.className = "source-empty";
      empty.textContent = "没有失败源";
      badCol.append(empty);
    }

    columns.append(okCol, badCol);
    container.append(summary, columns);
    show(container);
  }

  async function updateWorkspaceLinks() {
    if (!els.workspaceLinks) return;
    show(els.workspaceLinks);
    let caseRef = "case1";
    try {
      const cases = await api("/api/v1/simulation/cases");
      if (Array.isArray(cases) && cases.length) {
        const matched = cases.find((item) => item.case_ref === "case1") || cases[0];
        caseRef = matched.case_ref;
      }
    } catch (_) {
      // Keep the default case1 links if the simulation runtime is unavailable.
    }
    const query = `?case=${encodeURIComponent(caseRef)}`;
    document.querySelectorAll("[data-workspace='graph']").forEach((link) => {
      link.href = `/assets/graph.html${query}`;
    });
    document.querySelectorAll("[data-workspace='simulation']").forEach((link) => {
      link.href = `/assets/simulation.html${query}`;
    });
    show(els.workspaceLinks);
  }

  function setExportLinks(id, isCase = false) {
    const prefix = isCase ? `/api/v1/cases/${id}` : `/api/v1/runs/${id}`;
    els.downloadMd.href = isCase ? `${prefix}/report.md` : `${prefix}/report.md`;
    els.exportPdf.href = isCase ? `${prefix}/report/view?print=true` : `${prefix}/report.pdf`;
    els.viewHtml.href = `${prefix}/report/view`;
  }

  function items(value) {
    return Array.isArray(value) ? value : [];
  }

  function appendCitations(container, sourceIds, sourceMap) {
    items(sourceIds).forEach((sourceId) => {
      const source = sourceMap.get(sourceId);
      if (!source || !source.url) return;
      const link = document.createElement("a");
      link.className = "citation";
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = sourceId;
      link.title = `${source.source_name || "来源"}｜${source.title || ""}`;
      container.append(" ", link);
    });
  }

  function evidenceList(values, sourceMap) {
    const list = document.createElement("ul");
    list.className = "evidence-list";
    items(values).forEach((item) => {
      if (!item || !item.text) return;
      const li = document.createElement("li");
      li.append(document.createTextNode(item.text));
      appendCitations(li, item.source_ids, sourceMap);
      list.append(li);
    });
    return list;
  }

  function topicCard(topic, sourceMap) {
    const card = document.createElement("details");
    card.className = "topic-card";
    card.open = true;
    const heading = document.createElement("summary");
    heading.textContent = topic.title || "主要关注";
    card.append(heading);
    if (topic.summary) {
      const summary = document.createElement("p");
      summary.textContent = topic.summary;
      appendCitations(summary, topic.source_ids, sourceMap);
      card.append(summary);
    }
    const views = [...items(topic.supporting_views), ...items(topic.social_views)];
    if (views.length) {
      const list = document.createElement("ul");
      list.className = "view-list";
      views.forEach((view) => {
        if (!view || !view.point) return;
        const li = document.createElement("li");
        const attribution = view.account || [view.speaker, view.organization].filter(Boolean).join(" / ");
        li.append(document.createTextNode(`${attribution ? `${attribution}：` : ""}${view.point}`));
        const metrics = [["赞", view.likes], ["转发", view.shares], ["评论", view.comments]]
          .filter(([, value]) => value !== null && value !== undefined)
          .map(([label, value]) => `${label} ${value}`);
        if (metrics.length) li.append(document.createTextNode(`（${metrics.join("，")}）`));
        appendCitations(li, view.source_id ? [view.source_id] : [], sourceMap);
        list.append(li);
      });
      card.append(list);
    }
    return card;
  }

  function layerSection(title, section, sourceMap) {
    if (!section || (!section.overview && !items(section.topics).length)) return null;
    const block = document.createElement("section");
    block.className = "dashboard-block";
    const heading = document.createElement("h3");
    heading.textContent = title;
    block.append(heading);
    if (section.overview) {
      const overview = document.createElement("p");
      overview.className = "layer-overview";
      overview.textContent = section.overview;
      block.append(overview);
    }
    const grid = document.createElement("div");
    grid.className = "topic-grid";
    items(section.topics).forEach((topic) => grid.append(topicCard(topic, sourceMap)));
    if (grid.children.length) block.append(grid);
    return block;
  }

  function renderDashboard(data) {
    const root = document.createElement("div");
    root.className = "report-dashboard";
    const sourceList = items(data.sources);
    const sourceMap = new Map(sourceList.map((source) => [source.id, source]));

    const meta = document.createElement("div");
    meta.className = "dashboard-meta";
    const counts = { official: 0, media: 0, social: 0 };
    sourceList.forEach((source) => { if (counts[source.source_type] !== undefined) counts[source.source_type] += 1; });
    [["材料", sourceList.length], ["官方", counts.official], ["媒体", counts.media], ["社交", counts.social]]
      .forEach(([label, value]) => {
        const item = document.createElement("div");
        item.innerHTML = `<strong>${value}</strong><span>${label}</span>`;
        meta.append(item);
      });
    if (data.generated_at) {
      const updated = document.createElement("p");
      updated.className = "dashboard-updated";
      updated.textContent = `更新时间：${data.generated_at}`;
      root.append(updated);
    }
    root.append(meta);

    if (items(data.executive_summary).length) {
      const section = document.createElement("section");
      section.className = "dashboard-block summary-block";
      section.innerHTML = "<h3>核心摘要</h3>";
      section.append(evidenceList(data.executive_summary, sourceMap));
      root.append(section);
    }

    if (items(data.key_metrics).length) {
      const section = document.createElement("section");
      section.className = "dashboard-block";
      section.innerHTML = "<h3>关键数据</h3>";
      const grid = document.createElement("div");
      grid.className = "metric-grid";
      data.key_metrics.forEach((metric) => {
        const card = document.createElement("div");
        card.className = "metric-card";
        const label = document.createElement("span");
        label.textContent = metric.label || "指标";
        const value = document.createElement("strong");
        value.textContent = metric.value || "-";
        const context = document.createElement("p");
        context.textContent = metric.context || "";
        appendCitations(context, metric.source_ids, sourceMap);
        card.append(label, value, context);
        grid.append(card);
      });
      section.append(grid);
      root.append(section);
    }

    if (items(data.timeline).length) {
      const section = document.createElement("section");
      section.className = "dashboard-block";
      section.innerHTML = "<h3>事件时间线</h3>";
      const timeline = document.createElement("ol");
      timeline.className = "event-timeline";
      data.timeline.forEach((event) => {
        const li = document.createElement("li");
        const date = document.createElement("time");
        date.textContent = event.date || "时间未明确";
        const text = document.createElement("p");
        text.textContent = event.event || "";
        appendCitations(text, event.source_ids, sourceMap);
        li.append(date, text);
        timeline.append(li);
      });
      section.append(timeline);
      root.append(section);
    }

    const official = layerSection("一、官方层面", data.official, sourceMap);
    if (official) root.append(official);

    const media = data.media || {};
    if (media.overview || items(media.domestic?.topics).length || items(media.overseas?.topics).length) {
      const block = document.createElement("section");
      block.className = "dashboard-block";
      block.innerHTML = "<h3>二、媒体层面</h3>";
      if (media.overview) {
        const overview = document.createElement("p");
        overview.className = "layer-overview";
        overview.textContent = media.overview;
        block.append(overview);
      }
      const domestic = layerSection("境内媒体", media.domestic, sourceMap);
      const overseas = layerSection("境外及港澳媒体", media.overseas, sourceMap);
      if (domestic) block.append(domestic);
      if (overseas) block.append(overseas);
      root.append(block);
    }

    const opinion = layerSection("三、社会舆论层面", data.public_opinion, sourceMap);
    if (opinion) root.append(opinion);

    const synthesis = data.synthesis || {};
    const synthesisGroups = [["主要共识", synthesis.consensus], ["主要差异", synthesis.differences],
      ["争议与风险", synthesis.risks], ["后续观察", synthesis.watch_points]];
    if (synthesisGroups.some(([, values]) => items(values).length)) {
      const section = document.createElement("section");
      section.className = "dashboard-block";
      section.innerHTML = "<h3>四、综合研判</h3>";
      const grid = document.createElement("div");
      grid.className = "synthesis-grid";
      synthesisGroups.forEach(([title, values]) => {
        if (!items(values).length) return;
        const card = document.createElement("div");
        card.className = "synthesis-card";
        const heading = document.createElement("h4");
        heading.textContent = title;
        card.append(heading, evidenceList(values, sourceMap));
        grid.append(card);
      });
      section.append(grid);
      root.append(section);
    }

    if (sourceList.length) {
      const section = document.createElement("section");
      section.className = "dashboard-block source-browser";
      section.innerHTML = "<h3>来源</h3>";
      const filters = document.createElement("div");
      filters.className = "source-filters";
      const list = document.createElement("div");
      list.className = "dashboard-source-list";
      const draw = (filter) => {
        list.innerHTML = "";
        sourceList.filter((source) => filter === "all" || source.source_type === filter).forEach((source) => {
          const row = document.createElement("a");
          row.href = source.url;
          row.target = "_blank";
          row.rel = "noopener noreferrer";
          row.dataset.type = source.source_type;
          const title = document.createElement("strong");
          title.textContent = `${source.id}｜${source.title || "未命名来源"}`;
          const meta = document.createElement("span");
          meta.textContent = [source.source_name, source.published_at].filter(Boolean).join(" · ");
          row.append(title, meta);
          list.append(row);
        });
      };
      [["all", "全部"], ["official", "官方"], ["media", "媒体"], ["social", "社交"]].forEach(([value, label]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.addEventListener("click", () => draw(value));
        filters.append(button);
      });
      draw("all");
      section.append(filters, list);
      root.append(section);
    }
    return root;
  }

  function renderReport(markdown, reportData, topic, id, sources, isCase = false) {
    latestMarkdown = markdown || "";
    els.reportTitle.textContent = topic || "简报";
    setExportLinks(id, isCase);
    renderSources(els.reportSources, sources || []);
    els.reportBody.innerHTML = "";
    if (reportData && Object.keys(reportData).length) {
      els.reportBody.append(renderDashboard(reportData));
    } else if (window.marked && window.DOMPurify) {
      const html = marked.parse(latestMarkdown);
      els.reportBody.innerHTML = DOMPurify.sanitize(html);
    } else {
      els.reportBody.textContent = latestMarkdown;
    }
    hide(els.progressSection);
    els.cancelBtn.hidden = true;
    hide(els.errorSection);
    show(els.reportSection);
    updateWorkspaceLinks();
    if (isCase) {
      show(els.qaPanel);
    } else {
      hide(els.qaPanel);
    }
    els.reportSection.scrollIntoView({ behavior: "smooth", block: "start" });
    setBusy(false);
  }

  function renderQACitations(result) {
    els.qaCitations.innerHTML = "";
    const citations = Array.isArray(result.citations) ? result.citations : [];
    const evidence = Array.isArray(result.evidence) ? result.evidence : [];
    const evidenceBySource = new Map(evidence.map((item) => [item.source_id, item]));
    citations.forEach((item) => {
      if (!item.url) return;
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      const title = document.createElement("strong");
      title.textContent = `${item.source_id}｜${item.title || "未命名来源"}`;
      const meta = document.createElement("small");
      const evidenceItem = evidenceBySource.get(item.source_id);
      meta.textContent = [item.source_name, item.claim, evidenceItem?.quote]
        .filter(Boolean)
        .join(" · ");
      link.append(title, meta);
      els.qaCitations.append(link);
    });
    if (els.qaCitations.children.length) show(els.qaCitations);
    else hide(els.qaCitations);
  }

  async function askCaseQuestion() {
    const question = els.qaQuestion.value.trim();
    if (!currentCaseId || question.length < 2) {
      els.qaStatus.textContent = "请先输入问题，并确保当前已经打开一个案例报告。";
      show(els.qaStatus);
      return;
    }
    els.qaSubmit.disabled = true;
    els.qaStatus.textContent = "正在检索当前案例并生成回答……";
    show(els.qaStatus);
    hide(els.qaAnswer);
    hide(els.qaCitations);
    try {
      const result = await api(`/api/v1/cases/${currentCaseId}/chat`, {
        method: "POST",
        body: JSON.stringify({ question, mode: els.qaMode.value }),
      });
      els.qaAnswer.textContent = result.answer || "没有得到有效回答。";
      show(els.qaAnswer);
      renderQACitations(result);
      els.qaStatus.textContent = `已完成｜${result.mode}｜使用 ${result.retrieved_count || 0} 组证据`;
    } catch (error) {
      els.qaStatus.textContent = error.message || String(error);
    } finally {
      els.qaSubmit.disabled = false;
    }
  }

  function setProviderQueries(queries) {
    const tavily = queries && queries.tavily;
    els.tavilyQueryInput.value = Array.isArray(tavily) ? tavily.join("\n") : (tavily || "");
    els.weiboQueryInput.value = (queries && queries.weibo) || "";
  }

  function renderEvaluation(summary) {
    const rows = [
      ["最终得分", summary.overall_score],
      ["报告覆盖率", summary.report_coverage],
      ["新闻材料覆盖率", summary.retrieval_coverage],
      ["核心 rubric 报告覆盖", summary.core_report_coverage],
      ["重要 rubric 报告覆盖", summary.important_report_coverage],
    ];
    els.evaluationResult.innerHTML = "";
    rows.forEach(([label, value]) => {
      const row = document.createElement("div");
      row.className = "evaluation-row";
      const name = document.createElement("span");
      name.textContent = label;
      const score = document.createElement("strong");
      score.textContent = value ?? "-";
      row.append(name, score);
      els.evaluationResult.append(row);
    });

    const hits = Array.isArray(summary.reference_hits) ? summary.reference_hits : [];
    const hitSection = document.createElement("section");
    hitSection.className = "reference-hits";
    const hitTitle = document.createElement("h4");
    const total = Number.isInteger(summary.reference_total_count)
      ? summary.reference_total_count
      : hits.length;
    hitTitle.textContent = `Reference 命中要点（${hits.length}/${total}）`;
    hitSection.append(hitTitle);

    if (!hits.length) {
      const empty = document.createElement("p");
      empty.className = "stage-meta";
      empty.textContent = "最终报告暂未命中 Reference 中的有效信息点。";
      hitSection.append(empty);
    } else {
      const importanceLabels = { core: "核心", important: "重要", bonus: "补充" };
      const list = document.createElement("ol");
      hits.forEach((item) => {
        const li = document.createElement("li");
        const point = document.createElement("span");
        point.textContent = item.criterion || item.rubric_id || "未命名信息点";
        const meta = document.createElement("small");
        const importance = importanceLabels[item.importance] || item.importance || "未分类";
        const coverage = item.report_score === 1 ? "完整命中" : "部分命中";
        meta.textContent = `${importance} · ${coverage}`;
        li.append(point, meta);
        list.append(li);
      });
      hitSection.append(list);
    }
    els.evaluationResult.append(hitSection);
    els.evaluationResult.hidden = false;
  }

  async function pollEvaluation(runId) {
    try {
      const evaluation = await api(`/api/v1/runs/${runId}/evaluation`);
      if (evaluation.status === "completed") {
        els.evaluationProgress.hidden = true;
        renderEvaluation(evaluation.summary || {});
        els.evaluateBtn.disabled = false;
        return;
      }
      if (evaluation.status === "failed") {
        els.evaluationProgress.textContent = `评测失败：${evaluation.error || "未知错误"}`;
        els.evaluateBtn.disabled = false;
        return;
      }
      els.evaluationProgress.textContent = "正在生成 rubric 并评测……";
      els.evaluationProgress.hidden = false;
      evaluationTimer = setTimeout(() => pollEvaluation(runId), POLL_MS);
    } catch (error) {
      els.evaluationProgress.textContent = error.message || String(error);
      els.evaluationProgress.hidden = false;
      els.evaluateBtn.disabled = false;
    }
  }

  async function pollRun(runId) {
    try {
      const run = await api(`/api/v1/runs/${runId}`);
      els.progressText.textContent = run.progress || statusLabel(run.status);

      if (run.status === "completed") {
        const payload = run.report ? run : await api(`/api/v1/runs/${runId}/report`);
        const report = payload.report || "";
        renderReport(report, payload.report_data || run.report_data || {}, run.topic, runId, run.sources || []);
        setProviderQueries({
          tavily: run.tavily_queries || [],
          weibo: run.weibo_query || "",
        });
        els.generateBriefBtn.hidden = true;
        els.generateBriefBtn.disabled = false;
        els.analysisReadyMessage.hidden = true;
        return;
      }
      if (run.status === "analysis_ready") {
        if (run.report) {
          renderReport(run.report, run.report_data || {}, run.topic, runId, run.sources || []);
        } else {
          hide(els.progressSection);
          hide(els.errorSection);
          els.reportTitle.textContent = run.topic || "结构化分析已完成";
          els.reportBody.textContent = "数据抓取、正文复核和 MediaNode 结构化分析已经完成。";
          show(els.reportSection);
        }
        setProviderQueries({
          tavily: run.tavily_queries || [],
          weibo: run.weibo_query || "",
        });
        els.analysisReadyMessage.textContent = run.report_stale
          ? "已追加新材料，当前展示的是旧报告。请生成最新简报。"
          : "结构化分析已经完成，可以生成简报。";
        els.analysisReadyMessage.hidden = false;
        els.generateBriefBtn.hidden = false;
        els.generateBriefBtn.disabled = false;
        els.cancelBtn.hidden = true;
        setBusy(false);
        return;
      }
      if (run.status === "failed") {
        showError(run.error || "研究任务失败", run.sources || []);
        return;
      }
      if (run.status === "canceled") {
        els.progressText.textContent = "任务已终止";
        els.cancelBtn.hidden = true;
        setBusy(false);
        return;
      }
      pollTimer = setTimeout(() => pollRun(runId), POLL_MS);
    } catch (error) {
      showError(error.message || String(error));
    }
  }

  function caseSources(caseData) {
    return (caseData.child_runs || []).flatMap((run) => run.sources || []);
  }

  async function pollCase(caseId) {
    try {
      const researchCase = await api(`/api/v1/cases/${caseId}`);
      els.progressText.textContent = researchCase.progress || statusLabel(researchCase.status);
      if (researchCase.status === "completed") {
        const payload = researchCase.report
          ? researchCase
          : await api(`/api/v1/cases/${caseId}/report`);
        renderReport(
          payload.report || "",
          payload.report_data || researchCase.report_data || {},
          researchCase.topic,
          caseId,
          caseSources(researchCase),
          true,
        );
        els.generateBriefBtn.hidden = true;
        els.generateBriefBtn.disabled = false;
        els.analysisReadyMessage.hidden = true;
        return;
      }
      if (researchCase.status === "case_open") {
        els.progressText.textContent = "案例正在等待子 run 的结构化分析……";
      }
      if (researchCase.status === "failed") {
        showError(researchCase.error || "统一简报生成失败", caseSources(researchCase));
        return;
      }
      pollTimer = setTimeout(() => pollCase(caseId), POLL_MS);
    } catch (error) {
      showError(error.message || String(error));
    }
  }

  function statusLabel(status) {
    const map = {
      waiting_for_review: "等待审核检索词",
      running: "正在聚合与生成简报",
      analysis_ready: "结构化分析完成，等待生成简报",
      completed: "已完成",
      failed: "失败",
      canceled: "已终止",
    };
    return map[status] || status;
  }

  async function startRunning(runId) {
    currentRunId = runId;
    hide(els.planSection);
    hide(els.errorSection);
    hide(els.reportSection);
    els.progressText.textContent = "任务已启动，正在聚合来源…";
    show(els.progressSection);
    els.cancelBtn.hidden = false;
    els.cancelBtn.disabled = false;
    els.progressSection.scrollIntoView({ behavior: "smooth", block: "start" });
    pollRun(runId);
  }

  async function startCaseRunning(caseId) {
    currentCaseId = caseId;
    hide(els.planSection);
    hide(els.errorSection);
    hide(els.reportSection);
    els.progressText.textContent = "正在生成统一案例简报……";
    show(els.progressSection);
    els.cancelBtn.hidden = true;
    els.progressSection.scrollIntoView({ behavior: "smooth", block: "start" });
    pollCase(caseId);
  }

  async function createPlan(query) {
    const sources = {};
    els.sourceOptions.forEach((input) => { sources[input.dataset.source] = input.checked; });
    return api("/api/v1/plans", {
      method: "POST",
      body: JSON.stringify({ query, sources }),
    });
  }

  async function lookupExistingCases(query) {
    const result = await api("/api/v1/cases/lookup", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
    return Array.isArray(result.matches) ? result.matches : [];
  }

  function chooseExistingCase(matches) {
    const candidates = matches.filter((item) => item.can_reuse);
    if (!candidates.length) return null;
    const lines = candidates.slice(0, 8).map((item, index) => {
      const type = item.match_type === "exact" ? "精确匹配" : "关键词匹配";
      const terms = item.matched_terms?.length
        ? `；命中：${item.matched_terms.join("、")}`
        : "";
      const updated = item.updated_at
        ? new Date(item.updated_at).toLocaleString()
        : "未知时间";
      return `${index + 1}. ${item.case_key}｜${item.topic}｜${type}` +
        `｜${item.prepared_insight_count} 条结果｜${updated}${terms}`;
    });
    const answer = window.prompt(
      "发现可能已有案例，请选择要复用的案例：\n\n" +
      lines.join("\n") +
      "\n\n输入编号直接查看/生成报告；取消则重新搜索。",
      "1",
    );
    const index = Number.parseInt(answer || "", 10) - 1;
    return Number.isInteger(index) && index >= 0 && index < candidates.length
      ? candidates[index]
      : null;
  }

  async function openExistingCase(match) {
    const researchCase = await api(`/api/v1/cases/${match.case_id}`);
    currentCaseId = researchCase.case_id;
    currentRunId = researchCase.child_run_ids?.[0] || null;
    const firstRun = researchCase.child_runs?.[0];
    setProviderQueries({
      tavily: firstRun?.tavily_queries || [],
      weibo: firstRun?.weibo_query || "",
    });
    if (researchCase.status === "completed" && researchCase.report) {
      renderReport(
        researchCase.report,
        researchCase.report_data || {},
        researchCase.topic,
        researchCase.case_id,
        caseSources(researchCase),
        true,
      );
      return;
    }
    if (!match.can_reuse) {
      throw new Error("已有案例尚未完成结构化分析，需要重新执行完整流程");
    }
    await api(`/api/v1/cases/${researchCase.case_id}/brief`, {
      method: "POST",
      body: JSON.stringify({ save_markdown_file: true }),
    });
    await startCaseRunning(researchCase.case_id);
  }

  async function approvePlan(runId, queries) {
    return api(`/api/v1/plans/${runId}/approve`, {
      method: "POST",
        body: JSON.stringify({ approved_tavily_queries: queries }),
    });
  }

  els.toggleAdvanced.addEventListener("click", () => {
    const open = els.advancedPanel.hidden;
    els.advancedPanel.hidden = !open;
    els.toggleAdvanced.setAttribute("aria-expanded", open ? "true" : "false");
    els.toggleAdvanced.textContent = open
      ? "收起高级选项"
      : "高级：审核检索词";
  });

  els.addQuery.addEventListener("click", () => {
    renderQueryList([...collectQueries(), ""]);
    const inputs = els.queryList.querySelectorAll("input");
    inputs[inputs.length - 1]?.focus();
  });

  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = els.input.value.trim();
    if (query.length < 2) {
      showError("请输入至少两个字符的问题");
      return;
    }

    resetStages();
    setBusy(true);
    els.progressText.textContent = "正在生成检索计划…";
    show(els.progressSection);

    try {
      const matches = await lookupExistingCases(query);
      const reusable = chooseExistingCase(matches);
      if (reusable) {
        await openExistingCase(reusable);
        return;
      }
      const plan = await createPlan(query);
      currentRunId = plan.run_id;
      currentCaseId = plan.case_id || null;

      if (els.requireReview.checked) {
        hide(els.progressSection);
        els.planTopic.textContent = plan.topic || plan.query;
        renderQueryList(plan.tavily_queries || []);
        show(els.planSection);
        els.planSection.scrollIntoView({ behavior: "smooth", block: "start" });
        setBusy(false);
        return;
      }

      const queries = (plan.tavily_queries || []).filter(Boolean);
      await approvePlan(plan.run_id, queries);
      await startRunning(plan.run_id);
    } catch (error) {
      showError(error.message || String(error));
    }
  });

  els.approveBtn.addEventListener("click", async () => {
    const queries = collectQueries();
    if (!currentRunId) {
      showError("缺少任务 ID，请重新提交问题");
      return;
    }
    setBusy(true);
    try {
      await approvePlan(currentRunId, queries);
      await startRunning(currentRunId);
    } catch (error) {
      showError(error.message || String(error));
    }
  });

  els.cancelBtn.addEventListener("click", cancelRun);

  els.rerunQueryBtn.addEventListener("click", async () => {
    if (!currentRunId) return;
    const sources = {};
    els.appendSourceOptions.forEach((input) => { sources[input.dataset.appendSource] = input.checked; });
    if (!Object.values(sources).some(Boolean)) {
      showError("至少选择一个追加数据源");
      return;
    }
    const tavilyQueries = els.tavilyQueryInput.value.split("\n").map((item) => item.trim()).filter(Boolean);
    els.rerunQueryBtn.disabled = true;
    try {
      const rerun = await api(`/api/v1/runs/${currentRunId}/sources`, {
        method: "POST",
        body: JSON.stringify({ sources, tavily_queries: tavilyQueries }),
      });
      await startRunning(rerun.run_id);
    } catch (error) {
      els.rerunQueryBtn.disabled = false;
      showError(error.message || String(error));
    }
  });

  els.generateBriefBtn.addEventListener("click", async () => {
    if (!currentRunId) return;
    els.generateBriefBtn.disabled = true;
    try {
      if (!currentCaseId) {
        const run = await api(`/api/v1/runs/${currentRunId}/brief`, {
          method: "POST",
          body: JSON.stringify({ save_markdown_file: true }),
        });
        await startRunning(run.run_id);
        return;
      }
      const researchCase = await api(`/api/v1/cases/${currentCaseId}/brief`, {
        method: "POST",
        body: JSON.stringify({ save_markdown_file: true }),
      });
      await startCaseRunning(researchCase.case_id);
    } catch (error) {
      els.generateBriefBtn.disabled = false;
      showError(error.message || String(error));
    }
  });

  els.copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(latestMarkdown);
      els.copyBtn.textContent = "已复制";
      setTimeout(() => {
        els.copyBtn.textContent = "复制 Markdown";
      }, 1500);
    } catch {
      els.copyBtn.textContent = "复制失败";
      setTimeout(() => {
        els.copyBtn.textContent = "复制 Markdown";
      }, 1500);
    }
  });

  els.qaSubmit.addEventListener("click", askCaseQuestion);
  els.qaQuestion.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      askCaseQuestion();
    }
  });

  els.evaluateBtn.addEventListener("click", async () => {
    const reference = els.referenceInput.value.trim();
    if (!currentRunId || reference.length < 2) {
      els.evaluationProgress.textContent = "请先粘贴 reference";
      els.evaluationProgress.hidden = false;
      return;
    }
    els.evaluateBtn.disabled = true;
    els.evaluationProgress.textContent = "正在提交评测……";
    els.evaluationProgress.hidden = false;
    els.evaluationResult.hidden = true;
    try {
      await api(`/api/v1/runs/${currentRunId}/evaluate`, {
        method: "POST",
        body: JSON.stringify({ reference }),
      });
      await pollEvaluation(currentRunId);
    } catch (error) {
      els.evaluationProgress.textContent = error.message || String(error);
      els.evaluateBtn.disabled = false;
    }
  });

  els.retryBtn.addEventListener("click", () => {
    hide(els.errorSection);
    els.input.focus();
  });

  els.newBtn.addEventListener("click", () => {
    resetStages();
    setBusy(false);
    els.input.focus();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
})();
