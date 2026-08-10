(() => {
  const POLL_MS = 2000;

  const els = {
    form: document.getElementById("query-form"),
    input: document.getElementById("query-input"),
    submitBtn: document.getElementById("submit-btn"),
    toggleAdvanced: document.getElementById("toggle-advanced"),
    advancedPanel: document.getElementById("advanced-panel"),
    requireReview: document.getElementById("require-review"),
    planSection: document.getElementById("plan-section"),
    planTopic: document.getElementById("plan-topic"),
    queryList: document.getElementById("query-list"),
    addQuery: document.getElementById("add-query"),
    approveBtn: document.getElementById("approve-btn"),
    progressSection: document.getElementById("progress-section"),
    progressText: document.getElementById("progress-text"),
    errorSection: document.getElementById("error-section"),
    errorText: document.getElementById("error-text"),
    errorSources: document.getElementById("error-sources"),
    retryBtn: document.getElementById("retry-btn"),
    reportSection: document.getElementById("report-section"),
    reportTitle: document.getElementById("report-title"),
    reportBody: document.getElementById("report-body"),
    reportSources: document.getElementById("report-sources"),
    copyBtn: document.getElementById("copy-btn"),
    downloadMd: document.getElementById("download-md"),
    exportPdf: document.getElementById("export-pdf"),
    viewHtml: document.getElementById("view-html"),
    newBtn: document.getElementById("new-btn"),
  };

  let currentRunId = null;
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
    els.input.readOnly = busy;
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

  function setExportLinks(runId) {
    els.downloadMd.href = `/api/v1/runs/${runId}/report.md`;
    els.exportPdf.href = `/api/v1/runs/${runId}/report.pdf`;
    els.viewHtml.href = `/api/v1/runs/${runId}/report/view`;
  }

  function renderReport(markdown, topic, runId, sources) {
    latestMarkdown = markdown || "";
    els.reportTitle.textContent = topic || "简报";
    setExportLinks(runId);
    renderSources(els.reportSources, sources || []);
    if (window.marked && window.DOMPurify) {
      const html = marked.parse(latestMarkdown);
      els.reportBody.innerHTML = DOMPurify.sanitize(html);
    } else {
      els.reportBody.textContent = latestMarkdown;
    }
    hide(els.progressSection);
    hide(els.errorSection);
    show(els.reportSection);
    els.reportSection.scrollIntoView({ behavior: "smooth", block: "start" });
    setBusy(false);
  }

  async function pollRun(runId) {
    try {
      const run = await api(`/api/v1/runs/${runId}`);
      els.progressText.textContent = run.progress || statusLabel(run.status);

      if (run.status === "completed") {
        const report =
          run.report ||
          (await api(`/api/v1/runs/${runId}/report`)).report ||
          "";
        renderReport(report, run.topic, runId, run.sources || []);
        return;
      }
      if (run.status === "failed") {
        showError(run.error || "研究任务失败", run.sources || []);
        return;
      }
      pollTimer = setTimeout(() => pollRun(runId), POLL_MS);
    } catch (error) {
      showError(error.message || String(error));
    }
  }

  function statusLabel(status) {
    const map = {
      waiting_for_review: "等待审核检索词",
      running: "正在聚合与生成简报",
      completed: "已完成",
      failed: "失败",
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
    els.progressSection.scrollIntoView({ behavior: "smooth", block: "start" });
    pollRun(runId);
  }

  async function createPlan(query) {
    return api("/api/v1/plans", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
  }

  async function approvePlan(runId, queries) {
    return api(`/api/v1/plans/${runId}/approve`, {
      method: "POST",
      body: JSON.stringify({ approved_queries: queries }),
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
      const plan = await createPlan(query);
      currentRunId = plan.run_id;

      if (els.requireReview.checked) {
        hide(els.progressSection);
        els.planTopic.textContent = plan.topic || plan.query;
        renderQueryList(plan.proposed_queries || []);
        show(els.planSection);
        els.planSection.scrollIntoView({ behavior: "smooth", block: "start" });
        setBusy(false);
        return;
      }

      const queries = (plan.proposed_queries || []).filter(Boolean);
      if (!queries.length) {
        throw new Error("未生成可用检索词，请开启高级审核后手动填写");
      }
      await approvePlan(plan.run_id, queries);
      await startRunning(plan.run_id);
    } catch (error) {
      showError(error.message || String(error));
    }
  });

  els.approveBtn.addEventListener("click", async () => {
    const queries = collectQueries();
    if (!queries.length) {
      showError("至少保留一个检索词");
      return;
    }
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
