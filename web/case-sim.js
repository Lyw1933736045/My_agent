(() => {
  "use strict";

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function localDateTimeValue(iso) {
    const date = iso ? new Date(iso) : new Date();
    if (Number.isNaN(date.getTime())) return localDateTimeValue();
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function toAsOf(localValue) {
    if (!localValue) throw new Error("请填写截止时间");
    return `${localValue}:00+08:00`;
  }

  function defaultQuestion(topic, hours) {
    const subject = topic || "该事件";
    return `${subject}相关信息公开后，未来 ${hours || 48} 小时内不同主体可能形成哪些关注点、分歧和传播路径？`;
  }

  window.CaseSim = {
    openedCase() {
      const row = document.cookie.split("; ").find((item) => item.startsWith("ma_open_case="));
      return row ? decodeURIComponent(row.slice("ma_open_case=".length)) : "";
    },

    caseRef() {
      const requested = (new URLSearchParams(location.search).get("case") || "").trim();
      const opened = this.openedCase();
      if (opened && requested === "case1" && opened !== "case1") return opened;
      return requested || opened;
    },

    async api(caseRef, path, options = {}) {
      const response = await fetch(`/api/v1/simulation/cases/${encodeURIComponent(caseRef)}${path}`, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || `请求失败（${response.status}）`);
      return result;
    },

    fillForm(ids, overview) {
      const scenario = overview.scenario || (overview.job && overview.job.scenario) || {};
      const hours = Number(scenario.horizon_hours || 48);
      if (ids.question && !ids.question.value) {
        ids.question.value = scenario.question || defaultQuestion(overview.topic, hours);
      } else if (ids.question && scenario.question) {
        ids.question.value = scenario.question;
      }
      if (ids.asOf) ids.asOf.value = localDateTimeValue(scenario.as_of);
      if (ids.horizon) ids.horizon.value = String(hours);
      if (ids.maxAgents) {
        const agents = Number(scenario.max_agents || 35);
        ids.maxAgents.value = String(Math.max(2, Math.min(35, agents)));
      }
    },

    collectForm(ids) {
      const question = (ids.question.value || "").trim();
      const horizon = Number(ids.horizon.value) || 48;
      let maxAgents = Number(ids.maxAgents && ids.maxAgents.value) || 35;
      maxAgents = Math.max(2, Math.min(35, maxAgents));
      if (question.length < 8) throw new Error("请填写推演问题");
      return {
        question,
        as_of: toAsOf(ids.asOf.value),
        horizon_hours: horizon,
        allow_undated_social: true,
        max_agents: maxAgents,
      };
    },

    jobRunning(overview) {
      return overview.job && overview.job.status === "running";
    },
  };
})();
