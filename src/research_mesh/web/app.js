"use strict";

const byId = (id) => document.getElementById(id);

function setStatus(element, message, kind = "") {
  element.textContent = message;
  element.className = `status ${kind}`.trim();
}

function errorMessage(error) {
  if (error instanceof Error) return error.message;
  return String(error);
}

async function readJson(response) {
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`服务返回了不可解析的响应（HTTP ${response.status}）`);
  }
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
    throw new Error(detail || `请求失败（HTTP ${response.status}）`);
  }
  return payload;
}

function parseNumbers(raw) {
  const values = raw
    .split(/[\s,，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map(Number);
  if (values.length < 2 || values.some((value) => !Number.isFinite(value))) {
    throw new Error("数值数据至少需要两个有效数字");
  }
  return values;
}

function listItems(values, fallback) {
  if (!Array.isArray(values) || values.length === 0) return `<p>${fallback}</p>`;
  return `<ul>${values.slice(0, 6).map((item) => `<li>${escapeHtml(String(item))}</li>`).join("")}</ul>`;
}

function escapeHtml(value) {
  return value.replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[char]);
}

function renderReport(report) {
  const literature = report.literature || {};
  const experiment = report.experiment || {};
  const analysis = report.analysis || {};
  const review = report.review || {};
  const provider = literature.provider || {};

  byId("result-summary").textContent = `会话 ${report.session_id} 已完成，记录 ${report.provenance?.length || 0} 次 Agent 调用。`;
  byId("result-cards").innerHTML = `
    <article class="result-card">
      <h3>文献证据 / ${literature.count ?? 0}</h3>
      <p>来源：${escapeHtml(provider.name || "用户输入")} · 状态：${escapeHtml(provider.status || "完成")}</p>
      ${listItems((literature.evidence || []).map((item) => item.title), "没有返回文献")}
    </article>
    <article class="result-card">
      <h3>实验设计</h3>
      <p>${escapeHtml(experiment.hypothesis || "没有生成假设")}</p>
      ${listItems(experiment.controls, "没有控制条件")}
    </article>
    <article class="result-card">
      <h3>数据分析 / N=${analysis.n ?? "—"}</h3>
      <p>均值 ${formatNumber(analysis.mean)} · 中位数 ${formatNumber(analysis.median)}</p>
      <p>范围 ${formatNumber(analysis.minimum)} — ${formatNumber(analysis.maximum)}</p>
    </article>
    <article class="result-card">
      <h3>规范复核 / ${review.passed ? "通过" : "需修改"}</h3>
      <p>${escapeHtml(review.decision || "没有复核结论")}</p>
      ${listItems((review.findings || []).map((item) => item.message), "未发现阻断问题")}
    </article>`;
  byId("raw-result").textContent = JSON.stringify(report, null, 2);
  byId("result-panel").hidden = false;
  byId("result-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function formatNumber(value) {
  return Number.isFinite(value) ? Number(value).toFixed(2) : "—";
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((panel) => {
      panel.classList.remove("active");
      panel.hidden = true;
    });
    tab.classList.add("active");
    const panel = byId(tab.dataset.target);
    panel.hidden = false;
    panel.classList.add("active");
  });
});

byId("research-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = event.submitter;
  const status = byId("research-status");
  submit.disabled = true;
  setStatus(status, "正在调用四个 Agent…");

  try {
    const constraints = byId("constraints").value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
    const payload = {
      question: byId("question").value.trim(),
      objective: byId("objective").value.trim(),
      literature_query: byId("literature-query").value.trim() || null,
      max_literature_results: Number(byId("max-results").value),
      documents: [],
      dataset: {
        measure: byId("measure").value.trim() || "研究指标",
        values: parseNumbers(byId("values").value),
        unit: byId("unit").value.trim() || null,
      },
      constraints,
    };
    const response = await fetch("/research/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const report = await readJson(response);
    renderReport(report);
    setStatus(status, "闭环执行完成", "success");
  } catch (error) {
    setStatus(status, errorMessage(error), "error");
  } finally {
    submit.disabled = false;
  }
});

byId("llm-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = event.submitter;
  const status = byId("llm-status");
  submit.disabled = true;
  setStatus(status, "正在进行单次连接测试…");

  try {
    const payload = {
      base_url: byId("llm-base-url").value.trim(),
      model: byId("llm-model").value.trim(),
      api_key: byId("llm-api-key").value || null,
      prompt: byId("llm-prompt").value.trim(),
    };
    const response = await fetch("/ui/llm/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await readJson(response);
    const tokens = result.usage?.total_tokens ?? "—";
    setStatus(
      status,
      `连接成功 / ${result.model} / ${tokens} tokens / ${result.content}`,
      "success",
    );
  } catch (error) {
    setStatus(status, errorMessage(error), "error");
  } finally {
    submit.disabled = false;
  }
});

byId("toggle-key").addEventListener("click", () => {
  const input = byId("llm-api-key");
  const reveal = input.type === "password";
  input.type = reveal ? "text" : "password";
  byId("toggle-key").textContent = reveal ? "隐藏" : "显示";
  byId("toggle-key").setAttribute("aria-label", reveal ? "隐藏 API Key" : "显示 API Key");
});

byId("clear-key").addEventListener("click", () => {
  byId("llm-api-key").value = "";
  byId("llm-api-key").type = "password";
  byId("toggle-key").textContent = "显示";
  setStatus(byId("llm-status"), "密钥已从页面清空");
});
