(() => {
  const POLL_MS = 2000;

  const els = {
    form: document.getElementById("query-form"),
    input: document.getElementById("query-input"),
    submitBtn: document.getElementById("submit-btn"),
    listBriefsBtn: document.getElementById("list-briefs-btn"),
    briefsSection: document.getElementById("briefs-section"),
    briefsList: document.getElementById("briefs-list"),
    sourceOptions: document.querySelectorAll("[data-source]"),
    planSection: document.getElementById("plan-section"),
    queryList: document.getElementById("query-list"),
    planWeibo: document.getElementById("plan-weibo"),
    planCore: document.getElementById("plan-core"),
    planSupport: document.getElementById("plan-support"),
    approveBtn: document.getElementById("approve-btn"),
    continuePlanBtn: document.getElementById("continue-plan-btn"),
    cancelPlanBtn: document.getElementById("cancel-plan-btn"),
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
    qaThread: document.getElementById("qa-thread"),
    qaComposer: document.getElementById("qa-composer"),
    qaQuestion: document.getElementById("qa-question"),
    qaSubmit: document.getElementById("qa-submit"),
    qaStatus: document.getElementById("qa-status"),
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
    workspaceLinks: document.getElementById("workspace-links"),
    openGraph: document.getElementById("open-graph"),
    openSimulation: document.getElementById("open-simulation"),
  };

  let currentRunId = null;
  let currentCaseId = null;
  let viewingCaseId = null;
  let latestMarkdown = "";
  let pollTimer = null;

  function show(el) {
    el.hidden = false;
  }

  function hide(el) {
    el.hidden = true;
  }

  function setBusy(busy) {
    els.submitBtn.disabled = busy;
    els.approveBtn.disabled = busy;
    els.continuePlanBtn.disabled = busy;
    els.listBriefsBtn.disabled = busy;
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
    hide(els.briefsSection);
    hide(els.progressSection);
    hide(els.errorSection);
    hide(els.reportSection);
    hide(els.errorSources);
    els.errorSources.innerHTML = "";
    stopPolling();
    els.cancelBtn.hidden = true;
    els.generateBriefBtn.hidden = true;
    els.generateBriefBtn.disabled = false;
    els.analysisReadyMessage.hidden = true;
    currentCaseId = null;
    viewingCaseId = null;
    hide(els.workspaceLinks);
    hide(els.qaPanel);
    hide(els.qaStatus);
    els.qaThread.innerHTML = "";
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

  function splitTerms(text) {
    return String(text || "")
      .split(/[\n,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function fillPlan(plan) {
    renderQueryList(plan.tavily_queries || []);
    els.planWeibo.value = plan.weibo_query || "";
    els.planCore.value = (plan.newsnow_rss_core || []).join("\n");
    els.planSupport.value = (plan.newsnow_rss_support || []).join("\n");
  }

  function collectPlan() {
    return {
      approved_tavily_queries: collectQueries(),
      weibo_query: els.planWeibo.value.trim(),
      newsnow_rss_core: splitTerms(els.planCore.value),
      newsnow_rss_support: splitTerms(els.planSupport.value),
    };
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

  function workspaceCaseId() {
    return currentCaseId || viewingCaseId;
  }

  function workspaceHref(kind) {
    const caseRef = workspaceCaseId();
    return caseRef ? `/${kind}?case=${encodeURIComponent(caseRef)}` : `/${kind}`;
  }

  function updateWorkspaceLinks() {
    if (!els.workspaceLinks) return;
    const caseRef = workspaceCaseId();
    if (!caseRef) {
      hide(els.workspaceLinks);
      return;
    }
    document.querySelectorAll("[data-workspace]").forEach((link) => {
      link.href = workspaceHref(link.dataset.workspace);
    });
    show(els.workspaceLinks);
  }

  document.querySelectorAll("[data-workspace]").forEach((link) => {
    link.addEventListener("click", (event) => {
      const caseRef = workspaceCaseId();
      if (!caseRef) {
        event.preventDefault();
        showError("请先打开一份简报，再查看对应的知识图谱或推演。");
        return;
      }
      event.preventDefault();
      window.location.assign(workspaceHref(link.dataset.workspace));
    });
  });

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
    if (isCase) {
      viewingCaseId = id;
      currentCaseId = id;
      show(els.qaPanel);
    } else {
      hide(els.qaPanel);
    }
    updateWorkspaceLinks();
    els.reportSection.scrollIntoView({ behavior: "smooth", block: "start" });
    setBusy(false);
  }

  function toPlainReply(text) {
    return String(text || "")
      .replace(/!\[[^\]]*]\([^)]+\)/g, "")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/https?:\/\/\S+/gi, "")
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/__([^_]+)__/g, "$1")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/^\s*[-*•]\s+/gm, "")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function assistantSessionId() {
    const key = "caseAssistantSessionId";
    let id = sessionStorage.getItem(key);
    if (!id) {
      id = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now());
      sessionStorage.setItem(key, id);
    }
    return id;
  }

  function appendQATurn(role, text) {
    const turn = document.createElement("div");
    turn.className = `qa-turn qa-${role}`;
    const body = document.createElement("p");
    body.textContent = role === "assistant" ? toPlainReply(text) : text;
    turn.append(body);
    els.qaThread.append(turn);
    els.qaThread.scrollTop = els.qaThread.scrollHeight;
    return turn;
  }

  async function askCaseQuestion(event) {
    if (event) event.preventDefault();
    const question = els.qaQuestion.value.trim();
    if (!currentCaseId || question.length < 2) {
      els.qaStatus.textContent = "请先输入问题，并确保当前已经打开一个案例。";
      show(els.qaStatus);
      return;
    }
    els.qaSubmit.disabled = true;
    els.qaStatus.textContent = "正在回复……";
    show(els.qaStatus);
    appendQATurn("user", question);
    els.qaQuestion.value = "";
    try {
      const result = await api("/api/v1/assistant/chat", {
        method: "POST",
        body: JSON.stringify({
          message: question,
          session_id: assistantSessionId(),
          case_id: currentCaseId,
        }),
      });
      if (result.session_id) {
        sessionStorage.setItem("caseAssistantSessionId", result.session_id);
      }
      appendQATurn("assistant", result.answer || "没有得到有效回答。");
      els.qaStatus.hidden = true;
      await applyAssistantWorkspace(result);
    } catch (error) {
      els.qaStatus.textContent = error.message || String(error);
    } finally {
      els.qaSubmit.disabled = false;
      els.qaQuestion.focus();
    }
  }

  async function applyAssistantWorkspace(result) {
    const started = result && result.started_job;
    if (started && started.run_id) {
      if (started.case_id) currentCaseId = started.case_id;
      currentRunId = started.run_id;
      els.qaStatus.textContent = started.topic
        ? `已开始生成「${started.topic}」，可随时问进度。完成后会在左侧打开。`
        : "已开始生成新简报，可随时问进度。完成后会在左侧打开。";
      show(els.qaStatus);
      watchAssistantJob(started.run_id);
    }
    const openId = (result && result.open_case_id)
      || (result && result.job && result.job.status === "completed" && result.job.case_id)
      || "";
    if (openId && openId !== viewingCaseId) {
      await openCaseOnLeft(openId);
    }
  }

  async function watchAssistantJob(runId) {
    stopPolling();
    const tick = async () => {
      try {
        const run = await api(`/api/v1/runs/${runId}`);
        if (run.status === "completed") {
          if (run.case_id) {
            await openCaseOnLeft(run.case_id);
          } else {
            await openRunOnLeft(runId);
          }
          hide(els.qaStatus);
          return;
        }
        if (run.status === "failed") {
          els.qaStatus.textContent = run.error || "新简报生成失败";
          show(els.qaStatus);
          return;
        }
        if (run.status === "canceled") {
          els.qaStatus.textContent = "新简报任务已终止";
          show(els.qaStatus);
          return;
        }
        pollTimer = setTimeout(tick, POLL_MS);
      } catch (error) {
        els.qaStatus.textContent = error.message || String(error);
        show(els.qaStatus);
      }
    };
    tick();
  }

  async function openCaseOnLeft(caseId) {
    const researchCase = await api(`/api/v1/cases/${caseId}`);
    currentCaseId = researchCase.case_id;
    currentRunId = researchCase.child_run_ids?.[0] || currentRunId;
    const data = researchCase.report_data || {};
    if (researchCase.report || Object.keys(data).length) {
      renderReport(
        researchCase.report || "",
        data,
        researchCase.topic,
        researchCase.case_id,
        caseSources(researchCase),
        true,
      );
      return;
    }
    const completed = (researchCase.child_runs || []).find((item) => item.status === "completed" && item.report);
    if (completed) {
      await openRunOnLeft(completed.run_id);
    }
  }

  async function openRunOnLeft(runId) {
    const run = await api(`/api/v1/runs/${runId}`);
    const payload = run.report ? run : await api(`/api/v1/runs/${runId}/report`);
    if (run.case_id) currentCaseId = run.case_id;
    currentRunId = runId;
    renderReport(
      payload.report || "",
      payload.report_data || run.report_data || {},
      run.topic,
      run.case_id || runId,
      run.sources || [],
      Boolean(run.case_id),
    );
  }

  function setProviderQueries(queries) {
    const tavily = queries && queries.tavily;
    els.tavilyQueryInput.value = Array.isArray(tavily) ? tavily.join("\n") : (tavily || "");
    els.weiboQueryInput.value = (queries && queries.weibo) || "";
  }

  async function pollRun(runId) {
    try {
      const run = await api(`/api/v1/runs/${runId}`);
      els.progressText.textContent = run.progress || statusLabel(run.status);

      if (run.status === "completed") {
        const payload = run.report ? run : await api(`/api/v1/runs/${runId}/report`);
        const report = payload.report || "";
        if (run.case_id) currentCaseId = run.case_id;
        renderReport(
          report,
          payload.report_data || run.report_data || {},
          run.topic,
          run.case_id || runId,
          run.sources || [],
          Boolean(run.case_id),
        );
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

  async function approvePlan(runId, plan) {
    return api(`/api/v1/plans/${runId}/approve`, {
      method: "POST",
      body: JSON.stringify(plan),
    });
  }

  async function submitReviewedPlan() {
    if (!currentRunId) {
      showError("缺少任务 ID，请重新提交问题");
      return;
    }
    const plan = collectPlan();
    if (!plan.approved_tavily_queries.length) {
      showError("请填写 Tavily Query");
      return;
    }
    setBusy(true);
    try {
      await approvePlan(currentRunId, plan);
      await startRunning(currentRunId);
    } catch (error) {
      showError(error.message || String(error));
    }
  }

  function renderBriefList(items) {
    els.briefsList.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("li");
      empty.className = "briefs-empty";
      empty.textContent = "还没有已完成的简报。";
      els.briefsList.append(empty);
      return;
    }
    items.forEach((item) => {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "brief-item";
      const title = document.createElement("strong");
      title.textContent = item.topic || item.query || "未命名简报";
      const meta = document.createElement("span");
      meta.textContent = item.updated_at
        ? new Date(item.updated_at).toLocaleString()
        : "";
      button.append(title, meta);
      button.addEventListener("click", async () => {
        setBusy(true);
        try {
          hide(els.briefsSection);
          await openExistingCase(item);
        } catch (error) {
          showError(error.message || String(error));
        } finally {
          setBusy(false);
        }
      });
      li.append(button);
      els.briefsList.append(li);
    });
  }

  els.listBriefsBtn.addEventListener("click", async () => {
    els.listBriefsBtn.disabled = true;
    try {
      const result = await api("/api/v1/cases");
      renderBriefList(Array.isArray(result.cases) ? result.cases : []);
      show(els.briefsSection);
      els.briefsSection.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      showError(error.message || String(error));
    } finally {
      els.listBriefsBtn.disabled = false;
    }
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
      const plan = await createPlan(query);
      currentRunId = plan.run_id;
      currentCaseId = plan.case_id || null;
      hide(els.progressSection);
      fillPlan(plan);
      show(els.planSection);
      els.planSection.scrollIntoView({ behavior: "smooth", block: "start" });
      setBusy(false);
    } catch (error) {
      showError(error.message || String(error));
    }
  });

  els.approveBtn.addEventListener("click", submitReviewedPlan);
  els.continuePlanBtn.addEventListener("click", submitReviewedPlan);
  els.cancelPlanBtn.addEventListener("click", () => {
    hide(els.planSection);
    setBusy(false);
    els.input.focus();
  });

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

  els.qaComposer.addEventListener("submit", askCaseQuestion);
  els.qaQuestion.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      askCaseQuestion(event);
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
