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
    const detail = typeof payload.detail === "string"
      ? payload.detail
      : JSON.stringify(payload.detail || payload);
    throw new Error(detail || `请求失败（HTTP ${response.status}）`);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[char]);
}

function safeExternalUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(String(value));
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function formatNumber(value) {
  return Number.isFinite(value) ? Number(value).toFixed(2) : "—";
}

function formatPercent(value) {
  return Number.isFinite(value) ? `${Math.round(Number(value) * 100)}%` : "—";
}

function plainList(values, fallback) {
  if (!Array.isArray(values) || values.length === 0) return `<p>${escapeHtml(fallback)}</p>`;
  return `<ul class="plain-list">${values
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("")}</ul>`;
}

function evidenceList(evidence) {
  if (!Array.isArray(evidence) || evidence.length === 0) {
    return "<p>本次没有返回可展示的文献记录，请调整检索词后重试。</p>";
  }
  return `<ol class="evidence-list">${evidence.slice(0, 8).map((item) => {
    const title = escapeHtml(item.title || "未命名文献");
    const url = safeExternalUrl(item.url);
    const titleMarkup = url
      ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${title}</a>`
      : `<strong>${title}</strong>`;
    const authors = Array.isArray(item.authors) && item.authors.length
      ? item.authors.slice(0, 3).join("、")
      : "作者信息缺失";
    const year = item.year || "年份未知";
    const doi = item.doi || item.identifier;
    const identifier = doi ? ` · DOI ${doi}` : "";
    const sources = Array.isArray(item.providers) && item.providers.length
      ? item.providers
      : [item.provider || "未知来源"];
    const sourceMarkup = sources
      .map((source) => `<span class="evidence-source">${escapeHtml(source)}</span>`)
      .join("");
    const openAccessUrl = safeExternalUrl(item.open_access_url);
    const openAccessMarkup = openAccessUrl
      ? `<a class="open-access-link" href="${escapeHtml(openAccessUrl)}" target="_blank" rel="noreferrer">开放版本 ↗</a>`
      : "";
    const summary = item.has_abstract && item.summary
      ? `<p class="evidence-summary">${escapeHtml(item.summary)}</p>`
      : "";
    return `<li>
      <div class="evidence-title-row">${titleMarkup}${openAccessMarkup}</div>
      <span class="evidence-meta">${escapeHtml(authors)} · ${escapeHtml(year)}${escapeHtml(identifier)}</span>
      <div class="evidence-sources">${sourceMarkup}</div>
      ${summary}
    </li>`;
  }).join("")}</ol>`;
}

function providerStatusList(provider) {
  const sources = Array.isArray(provider.sources) ? provider.sources : [];
  if (sources.length === 0) return "";
  return `<ul class="provider-status-list">${sources.map((source) => {
    const label = source.status === "ok" ? "可用" : source.status === "partial" ? "部分可用" : "不可用";
    return `<li><strong>${escapeHtml(source.name || "数据源")}</strong><span>${escapeHtml(label)} · ${source.result_count ?? 0} 条原始记录</span></li>`;
  }).join("")}</ul>`;
}

function metric(label, value) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderTrace(provenance) {
  const target = byId("provenance-list");
  if (!Array.isArray(provenance) || provenance.length === 0) {
    target.innerHTML = "<p>没有返回调用轨迹。</p>";
    return;
  }
  target.innerHTML = provenance.map((event, index) => `
    <div class="trace-row">
      <span>${String(index + 1).padStart(2, "0")}</span>
      <strong>${escapeHtml(event.step || event.agent_slug || "Agent")}</strong>
      <span class="trace-aic" title="${escapeHtml(event.agent_aic || "")}">${escapeHtml(event.agent_aic || "身份未返回")}</span>
      <span class="trace-time">${formatNumber(event.duration_ms)} ms</span>
    </div>`).join("");
}

function renderReport(report) {
  const literature = report.literature || {};
  const experiment = report.experiment || {};
  const analysis = report.analysis || {};
  const review = report.review || {};
  const provider = literature.provider || {};
  const provenance = report.provenance || [];
  const reviewFindings = Array.isArray(review.findings)
    ? review.findings.map((item) => `${item.severity === "error" ? "需修改" : "提醒"}：${item.message}`)
    : [];

  byId("result-summary").textContent = `研究任务已完成。共记录 ${provenance.length} 次智能体调用，会话编号 ${report.session_id || "未返回"}。`;
  byId("result-cards").innerHTML = `
    <article class="result-card">
      <header class="result-card-head">
        <span class="agent-no">AGENT 01</span>
        <h3>文献证据</h3>
        <p class="result-meta">${provider.available_source_count ?? 0}/${provider.source_count ?? 0} 个数据源可用 · ${literature.count ?? 0} 条去重记录</p>
      </header>
      <div class="result-body">
        ${providerStatusList(provider)}
        ${evidenceList(literature.evidence)}
      </div>
    </article>

    <article class="result-card">
      <header class="result-card-head">
        <span class="agent-no">AGENT 02</span>
        <h3>实验设计</h3>
        <p class="result-meta">假设、控制条件与执行步骤</p>
      </header>
      <div class="result-body">
        <p><strong>研究假设：</strong>${escapeHtml(experiment.hypothesis || "没有生成假设")}</p>
        <p><strong>控制条件</strong></p>
        ${plainList(experiment.controls, "没有返回控制条件")}
        <p><strong>建议步骤</strong></p>
        ${plainList(experiment.steps, "没有返回实验步骤")}
      </div>
    </article>

    <article class="result-card">
      <header class="result-card-head">
        <span class="agent-no">AGENT 03</span>
        <h3>证据分析</h3>
        <p class="result-meta">来源覆盖与可追溯性检查</p>
      </header>
      <div class="result-body">
        <div class="metric-grid">
          ${metric("证据记录", analysis.record_count ?? "—")}
          ${metric("外部文献", analysis.external_count ?? "—")}
          ${metric("数据源", analysis.provider_count ?? "—")}
          ${metric("DOI 覆盖", formatPercent(analysis.doi_coverage))}
          ${metric("摘要覆盖", formatPercent(analysis.abstract_coverage))}
          ${metric("开放获取", formatPercent(analysis.open_access_coverage))}
          ${metric("最早年份", analysis.year_min ?? "—")}
          ${metric("最新年份", analysis.year_max ?? "—")}
        </div>
      </div>
    </article>

    <article class="result-card">
      <header class="result-card-head">
        <span class="agent-no">AGENT 04</span>
        <h3>规范复核</h3>
        <p class="result-meta">${review.passed ? "检查通过" : "建议修改"}</p>
      </header>
      <div class="result-body">
        <p class="review-decision">${escapeHtml(review.decision || "没有返回复核结论")}</p>
        ${plainList(reviewFindings, "未发现阻断问题；正式使用前仍建议人工复核原始证据。")}
      </div>
    </article>`;

  renderTrace(provenance);
  byId("raw-result").textContent = JSON.stringify(report, null, 2);
  byId("result-panel").hidden = false;
  byId("result-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function updateSystemStatus() {
  const status = byId("system-status");
  try {
    const health = await readJson(await fetch("/health", { headers: { Accept: "application/json" } }));
    const mode = health.mode === "platform" ? "可信平台模式" : "本地演示模式";
    byId("system-status-text").textContent = `${mode} · ${health.agents ?? 4} 个协作智能体`;
    status.classList.add("online");
  } catch {
    byId("system-status-text").textContent = "服务状态暂不可用";
    status.classList.add("offline");
  }
}

byId("research-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = event.submitter || byId("research-submit");
  const buttonLabel = submit.querySelector(".button-label");
  const status = byId("research-status");
  submit.disabled = true;
  submit.setAttribute("aria-busy", "true");
  buttonLabel.textContent = "智能体正在协作";
  setStatus(status, "正在多源检索文献、设计实验、分析证据并进行规范复核…");

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
      constraints,
    };
    const response = await fetch("/research/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const report = await readJson(response);
    renderReport(report);
    setStatus(status, "四智能体闭环执行完成", "success");
  } catch (error) {
    setStatus(status, errorMessage(error), "error");
  } finally {
    submit.disabled = false;
    submit.removeAttribute("aria-busy");
    buttonLabel.textContent = "运行四智能体协作";
  }
});

updateSystemStatus();
