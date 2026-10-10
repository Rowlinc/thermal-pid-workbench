"use strict";
const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const clone = (o) => JSON.parse(JSON.stringify(o));
const groupName = (n) =>
  ({
    original_zn: "原辨识公式参考（未调优）",
    legacy_route: "旧版完整调优路线",
    corrected_zn: "修正 Z-N PID 路线",
    zn_pi_route: "修正 Z-N PI 路线",
    simc_route: "SIMC PI 路线",
    user_route: "用户 PID 路线",
    selected: "最终跨路线选优",
  })[n] || n;
const failureText = (n) =>
  ({
    max_overshoot_pct: "超调超过上限",
    max_tail_error_c: "末段偏差超过上限",
    max_temperature_c: "被控量超过上限",
    min_temperature_c: "被控量低于下限",
    unsettled: "尚未稳定",
    max_settling_time_s: "稳定时间超过上限",
    max_output_variation: "输出动作过多",
    max_saturation_fraction: "输出饱和过久",
    simulation_aborted: "仿真已触发停止条件",
  })[n] || n;
let state = null,
  job = null,
  polling = null,
  page = "setup",
  chartField = "value",
  hiddenSeries = new Set(),
  csvColumns = [];
const titles = {
  setup: "任务与对象",
  model: "模型与历史数据",
  constraints: "控制器与评价",
  llm: "大模型调优",
  device: "设备接入",
  legacy: "原项目完整流程",
  results: "响应与结果",
  history: "运行历史",
  advanced: "全部配置与方案",
  help: "使用说明",
};
const words = {
  test: "测试 · 只输出参数和仿真",
  use: "使用 · 接入已配置的设备",
  temperature: "温度",
  pressure: "压力",
  flow: "流量",
  level: "液位",
  speed: "转速",
  custom: "自定义",
  fopdt: "FOPDT · 一阶惯性加纯滞后",
  integrating: "积分模型 · IPDT",
  heating: "专用加热模型（℃）",
  parameters: "已知模型参数",
  csv: "历史 CSV 辨识（FOPDT）",
  probe: "离线模型阶跃辨识",
  manual: "用户初始 PID · 直接仿真／可选 LLM",
  parallel: "并联式 Kp / Ki / Kd",
  ideal: "理想式 Kp / Ti / Td",
  measurement: "对测量值微分",
  error: "对误差微分",
  zero: "积分从零开始",
  tracking: "跟踪初始输出",
  auto: "自动",
  relative: "始终限制相对增幅",
  disabled: "关闭",
  enabled: "开启",
  provider_default: "服务默认",
  simulated: "模拟设备",
  tcp: "TCP 网关",
  serial: "串口 JSONL 网关",
  s: "秒",
  ms: "毫秒",
  min: "分钟",
  openai: "OpenAI 兼容 API",
  anthropic: "Anthropic API",
  none: "不初始化",
  zn: "修正辨识 Z-N",
  "original-zn": "原辨识 Z-N",
};
const statusWords = {
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已停止",
  interrupted: "上次运行中断",
};
async function api(path, body, raw = false) {
  const options = { headers: { "X-Session-Token": window.__SESSION__ } };
  if (body !== undefined) {
    options.method = "POST";
    options.body = raw ? body : JSON.stringify(body);
    if (!raw) options.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, options);
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || "请求失败");
  return d;
}
function notice(message, error = false) {
  const n = $("#notice");
  n.hidden = false;
  n.className = error ? "error" : "";
  n.textContent = message;
}
window.appNotice = notice;
function get(path) {
  const parts = path.split(".");
  let value = parts[0] === "legacy" ? state.legacy : state.project;
  if (parts[0] === "legacy") parts.shift();
  for (const k of parts) value = value?.[k];
  return value;
}
function put(path, value) {
  const parts = path.split(".");
  let obj = parts[0] === "legacy" ? state.legacy : state.project;
  if (parts[0] === "legacy") parts.shift();
  for (const k of parts.slice(0, -1)) obj = obj[k];
  obj[parts.at(-1)] = value;
}
function sectionOf(f) {
  return f.path.split(".")[0];
}
function field(f) {
  if (f.path.startsWith("history.columns.") && csvColumns.length)
    f = { ...f, choices: csvColumns };
  const value = get(f.path),
    id = "field-" + f.path.replaceAll(".", "-");
  const choiceText = c => f.path === 'tuning.selection_priority' ? ({accuracy:'累计误差优先',smooth:'平稳优先',speed:'响应速度优先'})[c] || c : words[c] || c;
  let control = "";
  if (f.type === "boolean") {
    control = `<input id="${id}" data-field="${esc(f.path)}" type="checkbox" ${value ? "checked" : ""}>`;
  } else if (f.type === "array" && f.choices) {
    control = `<select id="${id}" data-field="${esc(f.path)}" multiple>${f.choices.map((c) => `<option value="${esc(c)}" ${value.includes(c) ? "selected" : ""}>${esc(words[c] || c)}</option>`).join("")}</select>`;
  } else if (f.choices) {
    control = `<select id="${id}" data-field="${esc(f.path)}">${f.choices.map((c) => `<option value="${esc(c)}" ${value === c ? "selected" : ""}>${esc(choiceText(c))}</option>`).join("")}</select>`;
  } else if (f.type === "array") {
    control = `<textarea id="${id}" data-field="${esc(f.path)}" rows="3" placeholder="每行一个值">${esc((value || []).join("\n"))}</textarea>`;
  } else {
    control = `<input id="${id}" data-field="${esc(f.path)}" type="${f.type === "number" ? "number" : "text"}" ${f.type === "number" ? 'step="any"' : ""} value="${esc(f.type === "array" ? JSON.stringify(value) : (value ?? ""))}" ${value === null ? "disabled" : ""}>`;
    if (f.nullable)
      control += `<span class="nullable"><input type="checkbox" data-null="${esc(f.path)}" ${value !== null ? "checked" : ""}>填写此值（关闭使用自动／不限制）</span>`;
  }
  let label = f.label.replaceAll("被控量单位", state.project.process.unit);
  if (
    f.type === "number" &&
    f.path.endsWith("_c") &&
    !label.includes(state.project.process.unit)
  )
    label += "（" + state.project.process.unit + "）";
  return `<label class="field" data-path="${esc(f.path)}"><span>${esc(label)}</span>${control}<small>${esc(f.description.replaceAll("被控量单位", state.project.process.unit))}</small></label>`;
}
function renderForms() {
  if (state.history_columns?.length) csvColumns = state.history_columns;
  const grouped = {};
  for (const f of state.fields) (grouped[sectionOf(f)] ??= []).push(f);
  for (const box of $$(".generated")) {
    box.innerHTML = box.dataset.sections
      .split(",")
      .map(
        (s) =>
          `<div class="section-title">${esc(state.sections[s] || { mode: "运行模式", task: "目标任务" }[s] || s)}</div><div class="form-grid">${(grouped[s] || []).map(field).join("")}</div>`,
      )
      .join("");
  }
  $("#experiment-name").value = state.project.name;
  $("#key-status").textContent = state.key_configured
    ? "已配置 · 不显示密钥"
    : "未配置";
  $("#workspace-info").textContent = state.workspace;
  $("#profiles").innerHTML = state.profiles
    .map((p) => `<option>${esc(p)}</option>`)
    .join("");
  renderAllFields();
  updateContext();
}
function renderAllFields() {
  const q = $("#field-search").value.toLowerCase();
  $("#all-fields").innerHTML = state.fields
    .filter((f) =>
      (f.label + " " + f.description + " " + f.path).toLowerCase().includes(q),
    )
    .map(
      (f) =>
        `<div class="field-detail"><strong>${esc(f.label)}</strong><p>${esc(f.description)}</p><button class="subtle" data-jump="${esc(f.path)}">前往编辑 →</button></div>`,
    )
    .join("");
}
function updateContext() {
  const p = state.project,
    workflow = $("#workflow").value;
  $("#write-confirm").hidden = !(
    workflow === "hardware" ||
    (workflow === "project" && p.mode === "use")
  );
  $("#response-title").textContent =
    p.process.name + "响应（" + p.process.unit + "）";
  if (!job || job.status !== "running") {
    $("#state-badge").textContent =
      p.mode === "test" ? "测试模式" : "设备使用模式";
    $("#state-badge").className = "badge";
  }
  for (const label of $$("[data-path]")) {
    const k = label.dataset.path;
    if (
      k.startsWith("model.heater_") ||
      k === "model.heat_transfer_per_s" ||
      k === "model.cooling_per_s" ||
      k === "task.ambient_temperature_c" ||
      k === "task.initial_heater_temperature_c"
    )
      label.hidden = p.model.type !== "heating";
    else if (k === "model.custom_factory")
      label.hidden = p.model.type !== "custom";
    else if (k === "model.tau_s") label.hidden = p.model.type === "integrating";
  }
}
function go(target) {
  page = target;
  for (const el of $$(".page"))
    el.classList.toggle("active", el.id === "page-" + target);
  for (const el of $$("nav button"))
    el.classList.toggle("active", el.dataset.page === target);
  $("#page-title").textContent = titles[target];
  if (target === "history") loadHistory().catch((e) => notice(e.message, true));
  if (target === "results") renderChart();
  window.scrollTo(0, 0);
}
function payload() {
  state.project.name = $("#experiment-name").value.trim() || "PID experiment";
  return {
    project: state.project,
    legacy: state.legacy,
    api_key: $("#api-key").value.trim(),
    clear_key: $("#clear-key").checked,
  };
}
async function save() {
  state = await api("/api/settings", payload());
  $("#api-key").value = "";
  $("#clear-key").checked = false;
  renderForms();
  notice("配置已保存。下次打开继续使用；导出的配置不包含密钥。");
}
async function start() {
  await save();
  let preferences;
  try {
    preferences = JSON.parse($("#prompt-overrides").value || "{}");
  } catch {
    throw new Error("附加提示上下文不是有效 JSON");
  }
  job = await api("/api/start", {
    ...payload(),
    workflow: $("#workflow").value,
    confirm_write: $("#confirm-write").checked,
    prompt_overrides: preferences,
  });
  $("#confirm-write").checked = false;
  hiddenSeries.clear();
  go("results");
  drawJob(job);
  beginPoll();
}
function beginPoll() {
  clearInterval(polling);
  polling = setInterval(async () => {
    try {
      if (!job) return;
      job = await api("/api/job/" + job.id);
      drawJob(job);
      if (job.status !== "running") {
        clearInterval(polling);
        loadHistory();
      }
    } catch (e) {
      clearInterval(polling);
      notice(e.message, true);
    }
  }, 900);
}
function num(v, d = 3) {
  return typeof v === "number"
    ? Number(v.toFixed(d)).toLocaleString("zh-CN", { maximumFractionDigits: d })
    : v == null
      ? "—"
      : String(v);
}
function pidText(p) {
  return p ? ["p", "i", "d"].map((k) => num(p[k], 6)).join(" / ") : "—";
}
function drawJob(j) {
  $("#state-badge").textContent = statusWords[j.status] || j.status;
  $("#state-badge").className = "badge " + j.status;
  $("#job-info").textContent =
    `${j.id} · ${statusWords[j.status] || j.status} · ${num(j.elapsed_s, 1)} 秒${j.error ? " · " + j.error : ""}`;
  const active = j.status === "running";
  $("#run").disabled = active;
  $("#stop").disabled = !active;
  $("#pause").disabled = !active || j.workflow === "project";
  $("#resume").disabled = !active || j.workflow === "project";
  const selected = j.metrics.find((m) => m.name === "selected"),
    unit = j.process?.unit || state.project.process.unit;
  $("#result-summary").innerHTML = [
    ["最终选中路线", j.selection?.selected_label || j.selected_method || "原流程／旧记录"],
    [
      "最终建议",
      j.final_pid ? pidText(j.final_pid) : active ? "运行中…" : "无合格建议",
    ],
    ["累计偏差 IAE", selected ? num(selected.iae ?? selected.iae_c_s) : "—"],
    ["输出变化 TV", selected ? num(selected.output_tv) : "—"],
  ]
    .map(
      ([label, val]) =>
        `<div class="metric-box"><label>${esc(label)}</label><strong>${esc(val)}</strong><small>${label.includes("IAE") ? esc(unit) + "·s" : label === "最终建议" ? "p / i / d · 秒制并联式幅值" : "本次配置模型上的结果"}</small></div>`,
    )
    .join("");
  if (j.final_export_pid) {
    const p = j.final_export_pid;
    const keys =
      p.controller_form === "ideal" ? ["Kp", "Ti", "Td"] : ["Kp", "Ki", "Kd"];
    let exported = document.querySelector("#exported-pid");
    if (!exported) {
      exported = document.createElement("div");
      exported.id = "exported-pid";
      exported.className = "callout";
      $("#result-summary").after(exported);
    }
    exported.textContent =
      "可用于所选参数形式的最终参数：" +
      keys.map((k) => k + "=" + num(p[k], 8)).join("，") +
      "；" +
      (p.controller_form === "ideal" ? "理想式" : "并联式") +
      "；时间单位：" +
      (p.parameter_time_unit === "min" ? "分钟" : "秒") +
      "。过程方向已计入。";
  } else document.querySelector("#exported-pid")?.remove();
  let routeNote = $("#selection-note");
  if (j.selection) {
    if (!routeNote) { routeNote = document.createElement("p"); routeNote.id = "selection-note"; $("#result-summary").after(routeNote); }
    routeNote.textContent = `${j.selection.used_legacy_route ? "本次沿用旧版路线。" : ""}${j.selection.reason} 旧版对照状态：${({completed:"完整调优已完成",llm_disabled:"LLM关闭，仅初始化",unavailable:"不可用",disabled:"未开启",incomplete:"未完成"})[j.selection.legacy_status] || j.selection.legacy_status}；选优标准：${({accuracy:"累计误差优先",smooth:"平稳优先",speed:"响应速度优先"})[j.selection.priority] || j.selection.priority}。`;
  } else routeNote?.remove();
  $("#downloads").innerHTML = j.result_available
    ? '<button class="secondary" id="restore-run">载入本次配置再次测试</button><details><summary>可选导出 / 分享</summary> ' +
      [
        ["report.html", "分享用 HTML 报告 ↗"],
        ["pid.json", "导出 PID"],
        ["metrics.csv", "指标 CSV"],
        ["summary.json", "完整记录 JSON"],
        ["selected.csv", "响应 CSV"],
      ]
        .filter(([f]) => j.workflow === "project" || f === "report.html")
        .map(
          ([f, label]) =>
            `<a href="/result/${encodeURIComponent(j.id)}/${f}" target="_blank" rel="noopener">${label}</a>`,
        )
        .join("") +
      `<button class="subtle" id="open-result">打开结果文件夹 ↗</button></details>`
    : "";
  if ($("#restore-run"))
    $("#restore-run").onclick = () =>
      safe(async () => {
        const d = await api("/api/job-config/" + j.id);
        state.project = d.project;
        state.legacy = d.legacy;
        $("#workflow").value = d.workflow;
        $("#confirm-write").checked = false;
        renderForms();
        go("setup");
        notice("已载入本次运行配置。修改后开始运行会创建新的记录。");
      });
  if ($("#open-result"))
    $("#open-result").onclick = () =>
      safe(() => api("/api/open-folder", { id: j.id }));
  $("#metrics").innerHTML =
    `<thead><tr>${["组别", "初始方法", "p / i / d", "超调 %", "IAE " + unit + "·s", "末段 MAE " + unit, "调节时间 s", "输出 TV", "评价"].map((s) => "<th>" + esc(s) + "</th>").join("")}</tr></thead><tbody>` +
    j.metrics
      .map(
        (m) =>
          `<tr><td>${esc(groupName(m.name))}</td><td>${esc(m.initial_method)}</td><td>${esc(pidText(m.pid))}</td><td>${num(m.overshoot_pct)}</td><td>${num(m.iae ?? m.iae_c_s)}</td><td>${num(m.tail_mae ?? m.tail_mae_c, 6)}</td><td>${m.settling_time_s == null ? "未稳定" : num(m.settling_time_s)}</td><td>${num(m.output_tv)}</td><td class="${m.eligible ? "status-good" : "status-bad"}">${m.eligible ? "达标" : esc(m.failures.map(failureText).join("，"))}</td></tr>`,
      )
      .join("") +
    "</tbody>";
  $("#candidates").innerHTML =
    `<tr><th>候选</th><th>公式计算 p / i / d</th><th>护栏后 p / i / d</th><th>IAE</th><th>评价 / 护栏记录</th></tr>` +
    (j.candidates || [])
      .map(
        (c) =>
          `<tr><td>${esc(c.name)}</td><td>${esc(pidText(c.requested_pid))}</td><td>${esc(pidText(c.pid))}</td><td>${num(c.iae ?? c.iae_c_s)}</td><td>${c.eligible ? "达标" : esc(c.failures.map(failureText).join("，"))}<br>${esc(c.guard_notes.join("；"))}</td></tr>`,
      )
      .join("");
  const cfg = j.config || {};
  const pcfg = j.process || state.project.process;
  $("#response-title").textContent = pcfg.name + "响应（" + pcfg.unit + "）";
  const info = [
    ["实验", j.experiment_name],
    ["被控量", pcfg.name + " · " + pcfg.unit],
    ["模型", cfg.model?.type + " / " + cfg.model?.source],
    ["过程增益 K", cfg.model?.K],
    [
      "τ / θ（秒）",
      (cfg.model?.tau_s ?? "—") + " / " + (cfg.model?.theta_s ?? "—"),
    ],
    [
      "初始值 → 目标值",
      (cfg.task?.initial_value ?? "—") +
        " → " +
        (cfg.task?.target_value ?? "—"),
    ],
    [
      "执行器范围",
      cfg.actuator?.min + " ~ " + cfg.actuator?.max + " " + cfg.actuator?.unit,
    ],
    ["LLM", cfg.llm?.enabled ? "开启 · " + cfg.llm.model : "关闭"],
  ];
  if (j.workflow !== "project") {
    info.splice(
      2,
      5,
      ["运行流程", j.workflow],
      ["原流程设定值", j.target ?? "由硬件遥测给出"],
      ["已完成轮数", j.original_rounds ?? "运行中"],
    );
  }
  $("#run-config").innerHTML =
    "<table>" +
    info
      .map(
        ([k, v]) => "<tr><th>" + esc(k) + "</th><td>" + esc(v) + "</td></tr>",
      )
      .join("") +
    "</table>";
  const lines = j.events.map(
    (e) =>
      e.message ||
      e.phase ||
      (e.type === "candidate"
        ? "候选 " + e.name + "：" + pidText(e.pid)
        : e.type === "arm"
          ? "组别 " + e.name + " 完成评价"
          : JSON.stringify(e)),
  );
  for (const h of j.histories || [])
    for (const r of h.rounds)
      lines.push(
        `${h.name} · 第${r.round}轮 · ${r.event || "已评价"} · ${r.rollback ? "回滚／保留最佳" : "记录候选"}\n${r.applied_pid ? "p/i/d " + pidText(r.applied_pid) : ""} ${r.reason || ""}\n${(r.guard_notes || []).join("；")}`,
      );
  $("#logs").textContent = lines.join("\n");
  if (j.original_final_metrics) {
    const m = j.original_final_metrics;
    $("#metrics").innerHTML =
      "<tr><th>原流程窗口指标</th><th>数值</th></tr>" +
      Object.entries(m)
        .map(
          ([k, v]) =>
            "<tr><td>" +
            esc(
              {
                avg_error: "平均绝对偏差",
                steady_state_error: "稳态偏差",
                overshoot: "超调",
                status: "窗口状态",
              }[k] || k,
            ) +
            "</td><td>" +
            esc(num(v, 6)) +
            "</td></tr>",
        )
        .join("");
    $("#candidates").innerHTML =
      "<tr><td>原流程保留其窗口评价和参数机制，完整记录见下方；不与通用工作台的完整任务指标混算。</td></tr>";
    $("#logs").textContent += "\n原流程最终指标\n" + JSON.stringify(m, null, 2);
  }
  renderChart();
}
const colors = ["#177c69", "#e28a54", "#8671bb", "#5e92b5"];
function renderChart() {
  if (!job) return;
  const series = job.series || {},
    keys = Object.keys(series);
  $("#legend").innerHTML = keys
    .map(
      (k, i) =>
        `<button class="${hiddenSeries.has(k) ? "hidden" : ""}" data-series="${esc(k)}" style="color:${colors[i % colors.length]}">● ${esc(groupName(k))}</button>`,
    )
    .join("");
  const rows = keys
    .filter((k) => !hiddenSeries.has(k))
    .flatMap((k) => series[k]);
  if (!rows.length) {
    $("#chart").innerHTML = '<p class="empty">等待数据，或点击图例显示曲线</p>';
    return;
  }
  const field = chartField === "value" ? "value" : "output";
  const val = (r) =>
    field === "value" ? (r.value ?? r.temperature_c) : r.output;
  const tmin = Math.min(...rows.map((r) => r.time_s)),
    tmax = Math.max(tmin + 1, ...rows.map((r) => r.time_s));
  let low = Math.min(...rows.map(val)),
    high = Math.max(...rows.map(val));
  const target = job.target;
  if (field === "value" && target != null) {
    low = Math.min(low, target);
    high = Math.max(high, target);
  }
  const pad = Math.max((high - low) * 0.08, Math.abs(high) * 0.005, 0.00001);
  low -= pad;
  high += pad;
  const X = (t) => 65 + ((t - tmin) / (tmax - tmin)) * 905,
    Y = (v) => 300 - ((v - low) / (high - low)) * 255;
  let paths = "";
  for (let i = 0; i < 5; i++) {
    const y = 45 + (i * 255) / 4,
      v = high - (i * (high - low)) / 4;
    paths += `<path d="M65 ${y}H970" stroke="#e5ece5"/><text x="3" y="${y + 4}" fill="#8a9b8d" font-size="11">${num(v, 4)}</text>`;
  }
  if (field === "value" && target != null)
    paths += `<path d="M65 ${Y(target)}H970" stroke="#9cae9f" stroke-dasharray="5 5"/><text x="800" y="${Y(target) - 7}" font-size="11" fill="#8b9d90">目标 ${num(target, 4)}</text>`;
  keys.forEach((k, i) => {
    if (!hiddenSeries.has(k))
      paths += `<polyline fill="none" stroke="${colors[i % colors.length]}" stroke-width="2.5" points="${series[k].map((r) => X(r.time_s).toFixed(2) + "," + Y(val(r)).toFixed(2)).join(" ")}"/>`;
  });
  $("#chart").innerHTML =
    `<svg id="curve-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 340" role="img" aria-label="响应曲线">${paths}<text x="65" y="330" fill="#8a9b8d" font-size="11">${num(tmin)} s</text><text x="910" y="330" fill="#8a9b8d" font-size="11">${num(tmax)} s</text><line id="hover-line" y1="45" y2="300" stroke="#718b7b" stroke-dasharray="3 3" visibility="hidden"/><text id="hover-text" x="80" y="22" font-size="12" fill="#425e4b"></text></svg>`;
  const svg = $("#curve-svg");
  svg.onmousemove = (e) => {
    const x =
      ((e.clientX - svg.getBoundingClientRect().left) /
        svg.getBoundingClientRect().width) *
      1000;
    if (x < 65 || x > 970) return;
    const t = tmin + ((x - 65) / 905) * (tmax - tmin);
    $("#hover-line").setAttribute("x1", x);
    $("#hover-line").setAttribute("x2", x);
    $("#hover-line").setAttribute("visibility", "visible");
    const values = keys
      .filter((k) => !hiddenSeries.has(k))
      .map((k) => {
        const r = series[k].reduce((a, b) =>
          Math.abs(b.time_s - t) < Math.abs(a.time_s - t) ? b : a,
        );
        return `${groupName(k)}: ${num(val(r), 5)}`;
      });
    $("#hover-text").textContent = `${num(t, 2)} s · ${values.join(" / ")}`;
  };
  svg.onmouseleave = () => {
    $("#hover-line").setAttribute("visibility", "hidden");
    $("#hover-text").textContent = "";
  };
}
  async function loadHistory() {
    const rows = await api("/api/history");
    let stats = $("#route-statistics");
    if (!stats) {
      stats = document.createElement("p"); stats.id = "route-statistics";
      $("#history-list").before(stats);
    }
    const tagged = rows.filter(r => r.status === "completed" && r.selection?.qualified);
    const legacy = tagged.filter(r => r.selection.used_legacy_route === true).length;
    const comparable = tagged.filter(r => r.selection.baseline_comparison_available).length;
    const improved = tagged.filter(r => r.selection.strictly_improved_vs_legacy).length;
    stats.textContent = `有路线标记的合格运行 ${tagged.length} 次：旧版被选中 ${legacy} 次，新路线被选中 ${tagged.length - legacy} 次。其中完成旧版完整对照 ${comparable} 次，新路线按所选标准严格改善 ${improved} 次。不同模型和选优标准的运行分别理解；旧记录没有路线标记，不计入以上统计。`;
  $("#history-list").innerHTML = rows.length
    ? rows
        .map(
          (r) =>
             `<div class="history-row"><div><strong>${esc(r.name || r.id)} · ${esc(r.process?.name || "")} ${esc(r.target ?? "")} ${esc(r.process?.unit || "")}</strong><small>${esc(r.id)} · ${esc(r.workflow)} · ${new Date(r.started * 1000).toLocaleString()} · ${esc(statusWords[r.status] || r.status)}</small><p>${r.selection ? esc(r.selection.selected_label + " · " + r.selection.reason) : "旧记录：没有路线来源标记"}</p></div><button data-history="${esc(r.id)}" class="secondary">查看结果 →</button></div>`,
        )
        .join("")
    : '<p class="empty">第一次运行后，这里将保存你的记录。</p>';
}
async function selectHistory(id) {
  job = await api("/api/job/" + id);
  hiddenSeries.clear();
  go("results");
  drawJob(job);
  if (job.status === "running") beginPoll();
  else clearInterval(polling);
}
function download(name, data, type = "application/json") {
  if (window.pywebview?.api) {
    window.pywebview.api
      .save_file(name, data)
      .then((path) => {
        if (path) notice("文件已保存。");
      })
      .catch((e) => notice(String(e), true));
    return;
  }
  const u = URL.createObjectURL(new Blob([data], { type }));
  const a = document.createElement("a");
  a.href = u;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}
async function csv(file) {
  const d = await api(
    "/api/upload-csv?name=" + encodeURIComponent(file.name),
    file,
    true,
  );
  csvColumns = d.columns;
  state.history_columns = d.columns;
  state.project.model.type = "fopdt";
  state.project.model.source = "csv";
  state.project.history.file = d.path;
  const c = state.project.history.columns;
  const find = (patterns, fallback) =>
    d.columns.find((x) => patterns.some((p) => x.toLowerCase().includes(p))) ||
    d.columns[fallback];
  c.time = find(["时间", "time"], 0);
  c.output =
    d.columns.find((x) =>
      ["u", "output", "pwm", "control_output"].includes(x.toLowerCase()),
    ) || find(["output", "pwm", "阀位", "功率", "开度", "输出"], 1);
  c.temperature =
    d.columns.find((x) => ["y", "pv", "value"].includes(x.toLowerCase())) ||
    find(
      [
        "temperature",
        "pressure",
        "flow",
        "level",
        "温度",
        "压力",
        "压强",
        "流量",
        "液位",
        "value",
        "input",
      ],
      2,
    );
  renderForms();
  $("#csv-preview").innerHTML =
    "<table><tr>" +
    d.columns.map((c) => "<th>" + esc(c) + "</th>").join("") +
    "</tr>" +
    d.preview
      .map(
        (r) =>
          "<tr>" + r.map((c) => "<td>" + esc(c) + "</td>").join("") + "</tr>",
      )
      .join("") +
    "</table>";
  for (const key of ["time", "output", "temperature"]) {
    const el = $(`[data-field="history.columns.${key}"]`);
    const sel = document.createElement("select");
    sel.dataset.field = el.dataset.field;
    sel.id = el.id;
    sel.innerHTML = d.columns
      .map(
        (x) => `<option ${c[key] === x ? "selected" : ""}>${esc(x)}</option>`,
      )
      .join("");
    el.replaceWith(sel);
  }
  notice("CSV 已导入。请确认列对应、时间单位和被控量单位，再保存并运行。");
}
function preset(kind) {
  const p = clone(state.project);
  p.process = {
    kind,
    name: words[kind],
    unit: { temperature: "℃", pressure: "MPa", flow: "m³/h", level: "m" }[kind],
  };
  p.name = kind + "_example";
  p.mode = "test";
  p.llm.enabled = false;
  p.model.type = kind === "level" ? "integrating" : "fopdt";
  p.model.source = "parameters";
  p.simulation.measurement_noise_std_c = 0;
  p.simulation.disturbance_time_s = null;
  p.controller.initial_pid = { p: 1, i: 0.01, d: 0 };
  p.controller.initialization = "zero";
  p.device.adapter = "disabled";
  p.device.write_enabled = false;
  p.algorithms.include = ["ZN_PID", "ZN_PI", "SIMC_PI"];
  p.algorithms.simc_lambda_s = null;
  p.evaluation.max_output_variation = null;
  p.evaluation.max_saturation_fraction = null;
  const vals = {
    temperature: [
      1, 120, 10, 20, 0, 30, 100, 1, 600, 102, -20, 150, 0.3, 1, 600, 20, 20,
    ],
    pressure: [
      0.02, 12, 1, 0.3, 20, 0.3, 0.5, 0.1, 120, 0.52, 0, 1, 0.003, 0.005, 80, 5,
      10,
    ],
    flow: [2, 3, 0.2, 10, 10, 10, 60, 0.05, 60, 63, 0, 150, 0.5, 1, 40, 2, 10],
    level: [
      0.015, 1, 1, 1, 50, 1, 2, 0.2, 200, 2.1, 0, 4, 0.02, 0.02, 150, 5, 10,
    ],
  }[kind];
  const [
    K,
    tau,
    theta,
    base,
    bias,
    initial,
    target,
    dt,
    duration,
    max,
    min,
    stopmax,
    err,
    band,
    settle,
    observe,
    probe,
  ] = vals;
  Object.assign(p.model, {
    K,
    tau_s: tau,
    theta_s: theta,
    operating_temperature_c: base,
    operating_output: bias,
  });
  Object.assign(p.task, {
    initial_temperature_c: initial,
    target_temperature_c: target,
    initial_output: bias,
  });
  Object.assign(p.actuator, {
    unit: "%",
    min: 0,
    max: 100,
    max_rate_per_s: kind === "level" ? 5 : 20,
  });
  p.controller.sample_time_s = dt;
  p.simulation.duration_s = duration;
  p.simulation.temperature_stop_min_c = min;
  p.simulation.temperature_stop_max_c = stopmax;
  Object.assign(p.evaluation, {
    max_temperature_c: max,
    min_temperature_c: min,
    max_tail_error_c: err,
    settling_band_c: band,
    max_settling_time_s: settle,
    min_settled_observation_s: observe,
    max_overshoot_pct: 5,
  });
  p.device.monitor_min_temperature_c = min;
  p.device.monitor_max_temperature_c = stopmax;
  p.identification.probe_output_change = probe;
  p.identification.probe_duration_s = Math.max(10 * tau, 10);
  if (kind === "level") {
    p.algorithms.simc_lambda_s = 5;
    p.controller.initialization = "tracking";
  }
  state.project = p;
  $("#workflow").value = "project";
  $("#confirm-write").checked = false;
  renderForms();
  notice(
    `已载入${words[kind]}示例。示例模型参数不代表你的设备，请根据对象修改。`,
  );
}
function safe(fn) {
  return Promise.resolve()
    .then(fn)
    .catch((e) => notice(e.message, true));
}
document.addEventListener("change", (e) => {
  const el = e.target;
  if (el.dataset.field) {
    const f = state.fields.find((f) => f.path === el.dataset.field);
    let v =
      f.type === "boolean"
        ? el.checked
        : f.type === "number"
          ? Number(el.value)
          : f.type === "array"
            ? f.choices
              ? [...el.selectedOptions].map((o) => o.value)
              : null
            : el.value;
    try {
      if (f.type === "number" && (!el.value.trim() || !Number.isFinite(v)))
        throw new Error("请输入有效数字");
      if (f.type === "array" && !f.choices)
        v = el.value.trim().startsWith("[")
          ? JSON.parse(el.value)
          : el.value
              .split(/\r?\n/)
              .map((x) => x.trim())
              .filter(Boolean);
      put(f.path, v);
      if (f.path === "process.kind" && v !== "custom") {
        state.project.process.name = words[v] || v;
        state.project.process.unit = {
          temperature: "℃",
          pressure: "MPa",
          flow: "m³/h",
          level: "m",
          speed: "rpm",
        }[v];
        renderForms();
        notice(
          "被控量名称和单位已更新。数值不自动换算，请核对模型、目标与所有上下限。",
        );
      }
      updateContext();
    } catch (err) {
      notice(err.message, true);
    }
  } else if (el.dataset.null) {
    const f = state.fields.find((f) => f.path === el.dataset.null);
    put(f.path, el.checked ? (f.default ?? 1) : null);
    renderForms();
  } else if (el.id === "workflow") updateContext();
});
document.addEventListener("click", (e) => {
  const link = e.target.closest("a");
  if (
    link &&
    (link.getAttribute("href")?.startsWith("/result/") ||
      link.getAttribute("href")?.startsWith("/resource/"))
  ) {
    e.preventDefault();
    safe(async () => {
      const r = await fetch(link.getAttribute("href"), {
        headers: { "X-Session-Token": window.__SESSION__ },
      });
      if (!r.ok) throw new Error("此运行没有该导出文件，或文件尚未生成。");
      download(
        link.getAttribute("href").split("/").at(-1),
        await r.text(),
        r.headers.get("Content-Type") || "text/plain",
      );
    });
    return;
  }
  const button = e.target.closest("button");
  if (!button) return;
  if (button.dataset.page) go(button.dataset.page);
  if (button.dataset.preset) preset(button.dataset.preset);
  if (button.dataset.history) safe(() => selectHistory(button.dataset.history));
  if (button.dataset.chart) {
    chartField = button.dataset.chart;
    for (const b of $$("[data-chart]"))
      b.classList.toggle("active", b === button);
    renderChart();
  }
  if (button.dataset.series) {
    hiddenSeries.has(button.dataset.series)
      ? hiddenSeries.delete(button.dataset.series)
      : hiddenSeries.add(button.dataset.series);
    renderChart();
  }
  if (button.dataset.jump) {
    const section = button.dataset.jump.split(".")[0];
    const dest = {
      process: "setup",
      mode: "setup",
      task: "setup",
      model: "model",
      identification: "model",
      history: "model",
      actuator: "constraints",
      controller: "constraints",
      evaluation: "constraints",
      simulation: "constraints",
      tuning: "constraints",
      algorithms: "constraints",
      llm: "llm",
      device: "device",
      legacy: "legacy",
      output: "advanced",
    }[section];
    go(dest);
    $(`[data-path="${button.dataset.jump}"]`).scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }
});
$("#save").onclick = () => safe(save);
$("#run").onclick = () => safe(start);
for (const action of ["pause", "resume", "stop"])
  $("#" + action).onclick = () =>
    safe(async () => {
      job = await api("/api/control", { id: job.id, action });
      drawJob(job);
    });
$("#workspace").onclick = () => safe(() => api("/api/open-folder", {}));
$("#refresh-history").onclick = () => safe(loadHistory);
$("#field-search").oninput = renderAllFields;
$("#csv-file").onchange = (e) => {
  if (e.target.files[0]) safe(() => csv(e.target.files[0]));
};
$("#csv-drop").ondragover = (e) => {
  e.preventDefault();
  e.currentTarget.classList.add("drag");
};
$("#csv-drop").ondragleave = (e) => e.currentTarget.classList.remove("drag");
$("#csv-drop").ondrop = (e) => {
  e.preventDefault();
  e.currentTarget.classList.remove("drag");
  if (e.dataTransfer.files[0]) safe(() => csv(e.dataTransfer.files[0]));
};
for (const [k, id] of [
  ["project", "project-file"],
  ["legacy", "legacy-file"],
])
  $("#" + id).onchange = (e) => {
    const f = e.target.files[0];
    if (f)
      safe(async () => {
        state = await api("/api/import", { kind: k, text: await f.text() });
        renderForms();
        notice("配置已导入并保存，请核对模型路径、单位与设备设置。");
      });
  };
$("#model-file").onchange = (e) => {
  const f = e.target.files[0];
  if (f)
    safe(async () => {
      const d = await api(
        "/api/upload-model?name=" +
          encodeURIComponent(f.name) +
          "&trusted=" +
          $("#trust-model").checked,
        f,
        true,
      );
      state.project.model.type = "custom";
      state.project.model.source = "manual";
      state.project.model.custom_factory = d.factory;
      renderForms();
      notice(
        "自定义模型已导入，默认从用户初始 PID 仿真；请核对初始参数和过程方向。",
      );
    });
};
$("#model-template").onclick = () =>
  download(
    "custom_model.py",
    `"""Single-loop model example. Input/output units are defined in project configuration."""\nimport math\n\ndef create_model(config):\n    return Model(config)\n\nclass Model:\n    def __init__(self, config):\n        self.value = config['task']['initial_value']\n        self.bias = config['model']['operating_output']\n        self.base = config['model']['operating_value']\n        self.gain = config['model']['K']\n        self.tau = config['model']['tau_s']\n\n    def step(self, output, dt_s):\n        target = self.base + self.gain * (output - self.bias)\n        self.value = target + (self.value - target) * math.exp(-dt_s / self.tau)\n        return self.value\n`,
    "text/x-python",
  );
$("#ports").onclick = () =>
  safe(async () => {
    $("#port-list").textContent =
      (await api("/api/ports"))
        .map((p) => p.port + " — " + p.description)
        .join("；") || "未发现串口。原流程可填写 DEMO 运行模拟桥。";
  });
$("#pick-simulink").onclick = () =>
  safe(async () => {
    if (!window.pywebview?.api)
      throw new Error(
        "浏览器模式请直接填写本机模型的完整路径；桌面应用支持文件选择。",
      );
    const p = await window.pywebview.api.pick_model();
    if (p) {
      state.legacy.MATLAB_MODEL_PATH = p;
      $("#workflow").value = "simulink";
      renderForms();
      notice("已选择 Simulink 模型，请核对 PID 模块与信号路径。");
    }
  });
$("#pick-output-folder").onclick = () =>
  safe(async () => {
    if (!window.pywebview?.api)
      throw new Error("浏览器模式请在结果根目录中直接填写路径。");
    const folder = await window.pywebview.api.pick_folder();
    if (folder) {
      state.project.output.directory = folder;
      renderForms();
      notice("结果根目录已选择；应用历史记录仍保存在工作空间中。");
    }
  });
$("#export-project").onclick = () =>
  safe(async () => {
    await save();
    download("project.json", JSON.stringify(await api("/api/export"), null, 2));
  });
$("#export-legacy").onclick = () =>
  safe(async () => {
    await save();
    download(
      "legacy-config.json",
      JSON.stringify(await api("/api/legacy-export"), null, 2),
    );
  });
$("#profile-save").onclick = () =>
  safe(async () => {
    await save();
    state = await api("/api/profile-save", {
      ...payload(),
      name: $("#profile-name").value.trim(),
    });
    renderForms();
    notice("方案已保存，不包含密钥。");
  });
$("#profile-load").onclick = () =>
  safe(async () => {
    const d = await api("/api/profile-load", { name: $("#profiles").value });
    state.project = d.project;
    state.legacy = d.legacy;
    $("#confirm-write").checked = false;
    renderForms();
    notice("方案已载入，点击保存或开始运行。");
  });
$("#download-chart").onclick = () => {
  if ($("#curve-svg"))
    download("response.svg", $("#curve-svg").outerHTML, "image/svg+xml");
};
$("#diagnostics").onclick = () =>
  safe(async () => {
    await save();
    $("#diagnostics").disabled = true;
    try {
      const checks = await api("/api/diagnostics", {
        probe_llm: $("#probe-api").checked,
      });
      $("#diagnostic-results").innerHTML =
        "<table><tr><th>检查项</th><th>状态</th><th>说明</th></tr>" +
        checks
          .map(
            (c) =>
              "<tr><td>" +
              esc(c.name) +
              "</td><td>" +
              esc(c.status) +
              "</td><td>" +
              esc(c.detail) +
              "</td></tr>",
          )
          .join("");
    } finally {
      $("#diagnostics").disabled = false;
    }
  });
safe(async () => {
  state = await api("/api/state");
  renderForms();
  await loadHistory();
});
