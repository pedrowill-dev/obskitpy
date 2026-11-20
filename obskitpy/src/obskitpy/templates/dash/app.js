/* dash-obskitpy — dashboard leve, JS puro. Consulta o Elasticsearch por /es/ (proxy somente leitura do nginx). */
(() => {
  const CFG = window.OBSKITPY || { service: "app" };
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const IDX = { logs: "logs-generic.otel-*", traces: "traces-generic.otel-*", metrics: "metrics-generic.otel-*" };
  const UNIT = { s: ["segundos", 1e3, "s"], m: ["minutos", 6e4, "min"], h: ["horas", 36e5, "h"], d: ["dias", 864e5, "d"] };
  const STEPS = [["1s", 1e3], ["5s", 5e3], ["10s", 1e4], ["30s", 3e4], ["1m", 6e4], ["5m", 3e5], ["10m", 6e5], ["30m", 18e5], ["1h", 36e5], ["3h", 108e5], ["6h", 216e5], ["12h", 432e5], ["1d", 864e5]];
  const spanLabel = (t) => `${t.n} ${UNIT[t.u][2]}`;
  const spanMs = (t) => t.n * UNIT[t.u][1];
  const dateMath = (t) => `${t.n}${t.u}`; // 90s, 5m, 2h, 7d (formato do Elasticsearch)
  const bucket = (t) => (STEPS.find(([, v]) => v >= spanMs(t) / 45) || STEPS[STEPS.length - 1])[0]; // ~45 colunas no gráfico
  const loadSpan = (key, fallback) => { try { const v = JSON.parse(localStorage.getItem(key)); return v && v.n > 0 && UNIT[v.u] ? v : fallback; } catch { return fallback; } };
  const SEV_COLORS = { ERROR: "#ff7b7b", FATAL: "#ff7b7b", WARN: "#ffb81c", INFO: "#19d3b6", DEBUG: "#6ea8ff" };
  const PALETTE = ["#19d3b6", "#ffb81c", "#6ea8ff", "#c2a8ff", "#ff7b7b", "#5bd68a"];
  const state = { service: localStorage.getItem("dash.service") || CFG.service, services: [], range: loadSpan("dash.range", { n: 1, u: "h" }), refresh: loadSpan("dash.refresh", { n: 10, u: "s" }), auto: localStorage.getItem("dash.auto") !== "0", rt: false, saved: null, seen: null, lastHtml: "", tab: "overview", sev: "", q: "", errType: "", trace: null, open: null };

  /* ---------- acesso ao Elasticsearch ---------- */
  async function es(index, endpoint, body) {
    const r = await fetch(`es/${index}/${endpoint}`, { method: body ? "POST" : "GET", headers: body ? { "content-type": "application/json" } : {}, body: body && JSON.stringify(body) });
    if (r.status === 404) return null; // data stream ainda não existe
    if (!r.ok) throw new Error(`Elasticsearch respondeu ${r.status}`);
    return r.json();
  }
  const svcFilter = () => ({ term: { "resource.attributes.service.name": state.service } });
  const timeFilter = () => ({ range: { "@timestamp": { gte: `now-${dateMath(state.range)}` } } });
  const base = (...extra) => ({ bool: { filter: [svcFilter(), timeFilter(), ...extra] } });
  const ROOT = { bool: { must: [{ exists: { field: "name" } }], must_not: [{ exists: { field: "parent_span_id" } }] } };

  /* ---------- formatação ---------- */
  const nf = new Intl.NumberFormat("pt-BR");
  const fmtN = (n) => nf.format(Math.round(n || 0));
  const fmtDur = (ns) => ns == null ? "–" : ns >= 6e10 ? `${(ns / 6e10).toFixed(1)} min` : ns >= 1e9 ? `${(ns / 1e9).toFixed(2)} s` : ns >= 1e6 ? `${(ns / 1e6).toFixed(1)} ms` : `${Math.round(ns / 1e3)} µs`;
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString("pt-BR");
  const fmtDateTime = (iso) => new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const getPath = (o, path) => path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
  const flat = (o, p = "", out = {}) => { for (const [k, v] of Object.entries(o || {})) (v && typeof v === "object" && !Array.isArray(v)) ? flat(v, p + k + ".", out) : (out[p + k] = v); return out; };

  /* ---------- gráficos SVG ---------- */
  const tip = document.createElement("div"); tip.className = "tip"; document.body.appendChild(tip);
  const showTip = (e, html) => { tip.innerHTML = html; tip.style.display = "block"; tip.style.left = Math.min(e.clientX + 14, innerWidth - 230) + "px"; tip.style.top = e.clientY + 14 + "px"; };
  const hideTip = () => (tip.style.display = "none");

  function stackedChart(buckets, series, colors) {
    const W = 760, H = 230, pl = 36, pb = 24, pt = 10, pr = 8;
    if (!buckets.length) return '<div class="empty" style="padding:30px">Sem dados no período.</div>';
    const totals = buckets.map((b) => series.reduce((s, k) => s + (b.v[k] || 0), 0));
    const max = Math.max(...totals, 1);
    const bw = (W - pl - pr) / buckets.length, gap = Math.min(3, bw * 0.2);
    let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Série temporal">`;
    for (let i = 0; i <= 4; i++) {
      const y = pt + ((H - pt - pb) * i) / 4;
      svg += `<line class="grid" x1="${pl}" x2="${W - pr}" y1="${y}" y2="${y}"/><text x="${pl - 6}" y="${y + 4}" text-anchor="end">${fmtN(max * (1 - i / 4))}</text>`;
    }
    buckets.forEach((b, i) => {
      let y = H - pb;
      series.forEach((k) => {
        const h = ((b.v[k] || 0) / max) * (H - pt - pb);
        if (h > 0) { y -= h; svg += `<rect x="${pl + i * bw + gap / 2}" y="${y}" width="${Math.max(bw - gap, 1)}" height="${h}" rx="2" fill="${colors[k]}"/>`; }
      });
      const tipHtml = `<b>${fmtDateTime(b.t)}</b><br>` + series.filter((k) => b.v[k]).map((k) => `<span style="color:${colors[k]}">●</span> ${esc(k)}: ${fmtN(b.v[k])}`).join("<br>");
      svg += `<rect x="${pl + i * bw}" y="${pt}" width="${bw}" height="${H - pt - pb}" fill="transparent" data-tip='${esc(tipHtml).replace(/'/g, "&#39;")}'/>`;
    });
    const step = Math.max(1, Math.ceil(buckets.length / 6));
    buckets.forEach((b, i) => { if (i % step === 0) svg += `<text x="${pl + i * bw}" y="${H - 6}">${fmtTime(b.t).slice(0, 5)}</text>`; });
    svg += "</svg>";
    return svg + `<div class="legend">${series.map((k) => `<span><i style="background:${colors[k]}"></i>${esc(k)}</span>`).join("")}</div>`;
  }
  function wireTips(root) {
    $$("[data-tip]", root).forEach((el) => {
      el.addEventListener("mousemove", (e) => showTip(e, el.dataset.tip));
      el.addEventListener("mouseleave", hideTip);
    });
  }
  const bucketsFrom = (agg, keyOf) => agg.buckets.map((b) => ({ t: b.key_as_string || b.key, v: Object.fromEntries(b.k.buckets.map((x) => [x.key, x.doc_count])) }));

  /* ---------- telas ---------- */
  const emptyState = () => `<div class="empty"><h3>Ainda sem dados para <code>${esc(state.service)}</code></h3><p>Rode a sua aplicação (com <code>obskitpy.setup("${esc(state.service)}")</code>) ou gere um exemplo com <code>python obskitpy_demo.py</code>.<br>Os dados aparecem aqui em segundos.</p></div>`;

  async function overview() {
    const [logs, traces] = await Promise.all([
      es(IDX.logs, "_search", { size: 0, query: base(), aggs: {
        sev: { terms: { field: "severity_text", size: 10 } },
        time: { date_histogram: { field: "@timestamp", fixed_interval: bucket(state.range), min_doc_count: 0, extended_bounds: { min: `now-${dateMath(state.range)}`, max: "now" } }, aggs: { k: { terms: { field: "severity_text", size: 10 } } } },
        errs: { filter: { term: { severity_text: "ERROR" } }, aggs: { t: { terms: { field: "attributes.error.type", size: 8 }, aggs: { o: { terms: { field: "attributes.error.origin", size: 1 } } } } } } } }),
      es(IDX.traces, "_search", { size: 0, query: base({ exists: { field: "name" } }), aggs: {
        roots: { filter: ROOT, aggs: { avg: { avg: { field: "duration" } }, failed: { filter: { term: { "status.code": "Error" } } } } },
        ops: { terms: { field: "name", size: 12, order: { avg: "desc" } }, aggs: { avg: { avg: { field: "duration" } }, max: { max: { field: "duration" } } } } } }),
    ]);
    const total = logs?.hits.total.value || 0, spans = traces?.hits.total.value || 0;
    if (!total && !spans) return emptyState();
    const sev = Object.fromEntries((logs?.aggregations.sev.buckets || []).map((b) => [b.key, b.doc_count]));
    const errors = (sev.ERROR || 0) + (sev.FATAL || 0), warns = sev.WARN || 0;
    const roots = traces?.aggregations.roots, failed = roots?.failed.doc_count || 0;
    const kpi = (label, value, cls = "", sub = "") => `<div class="kpi ${cls}"><small>${label}</small><b>${value}</b>${sub ? `<em>${sub}</em>` : ""}</div>`;
    const sevOrder = ["ERROR", "WARN", "INFO", "DEBUG"].filter((k) => logs?.aggregations.time.buckets.some((b) => b.k.buckets.some((x) => x.key === k)));
    const chart = stackedChart(bucketsFrom(logs.aggregations.time), sevOrder, SEV_COLORS);
    const errRows = (logs?.aggregations.errs.t.buckets || []).map((b) => `<tr class="click" data-err="${esc(b.key)}"><td><code>${esc(b.key)}</code></td><td><code>${esc(b.o.buckets[0]?.key || "–")}</code></td><td class="n">${fmtN(b.doc_count)}</td></tr>`).join("");
    const ops = traces?.aggregations.ops.buckets || [], maxAvg = Math.max(...ops.map((o) => o.avg.value || 0), 1);
    const opRows = ops.map((o) => `<tr><td>${esc(o.key)}</td><td><div class="bar-cell"><span><i style="width:${Math.max(2, ((o.avg.value || 0) / maxAvg) * 100)}%"></i></span>${fmtDur(o.avg.value)}</div></td><td class="n">${fmtDur(o.max.value)}</td><td class="n">${fmtN(o.doc_count)}</td></tr>`).join("");
    return `<div class="kpis">
      ${kpi("Logs", fmtN(total))}
      ${kpi("Erros", fmtN(errors), errors ? "bad" : "good", total ? `${((errors / total) * 100).toFixed(1)}% dos logs` : "")}
      ${kpi("Avisos", fmtN(warns))}
      ${kpi("Execuções", fmtN(roots?.doc_count || 0), "", "traces raiz")}
      ${kpi("Execuções com erro", fmtN(failed), failed ? "bad" : "good")}
      ${kpi("Duração média", fmtDur(roots?.avg.value), "", "por execução")}
    </div>
    <div class="grid"><div class="card"><h2>Logs por severidade</h2>${chart}</div>
      <div class="card"><h2>Erros por tipo e origem</h2>${errRows ? `<table><thead><tr><th>Tipo</th><th>Origem</th><th class="n">Qtd</th></tr></thead><tbody>${errRows}</tbody></table><p class="mono" style="color:var(--faint);margin:10px 0 0">Clique numa linha para ver os logs.</p>` : '<div class="empty" style="padding:30px">Nenhum erro no período 🎉</div>'}</div></div>
    <div class="card full"><h2>Operações (spans) por duração média</h2>${opRows ? `<table><thead><tr><th>Operação</th><th>Média</th><th class="n">Máxima</th><th class="n">Qtd</th></tr></thead><tbody>${opRows}</tbody></table>` : '<div class="empty" style="padding:30px">Sem spans no período.</div>'}</div>`;
  }

  async function logsTab() {
    const filters = [];
    if (state.sev) filters.push({ term: { severity_text: state.sev } });
    if (state.errType) filters.push({ term: { "attributes.error.type": state.errType } });
    if (/^[0-9a-f]{32}$/i.test(state.q)) filters.push({ term: { trace_id: state.q.toLowerCase() } });
    else if (state.q) filters.push({ match: { "body.text": { query: state.q, operator: "and" } } });
    const r = await es(IDX.logs, "_search", { size: 100, sort: [{ "@timestamp": "desc" }], query: base(...filters), track_total_hits: true });
    const hits = r?.hits.hits || [];
    const ids = hits.map((h) => h._id);
    const fresh = state.seen ? new Set(ids.filter((id) => !state.seen.has(id))) : new Set();
    state.seen = new Set(ids);
    const pills = ["", "ERROR", "WARN", "INFO", "DEBUG"].map((s) => `<button data-sev="${s}" class="${state.sev === s ? "on" : ""}">${s || "Todos"}</button>`).join("");
    const rows = hits.map((h, i) => {
      const s = h._source, a = s.attributes || {}, open = state.open === h._id;
      const extra = a["error.type"] ? `<code style="color:var(--red)">${esc(a["error.type"])}</code> <span style="color:var(--faint)">${esc(a["error.origin"] || "")}</span>` : "";
      let detail = "";
      if (open) {
        const kv = Object.entries(flat(a)).filter(([k]) => !["exception.stacktrace", "error.chain"].includes(k)).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
        const st = a["exception.stacktrace"];
        detail = `<tr class="detail"><td colspan="3"><dl class="kv"><dt>trace_id</dt><dd>${esc(s.trace_id || "–")}</dd><dt>span_id</dt><dd>${esc(s.span_id || "–")}</dd>${kv}</dl>${st ? `<pre>${esc(st)}</pre>` : ""}${s.trace_id ? `<button class="btn" data-trace="${esc(s.trace_id)}">Ver trace completo →</button>` : ""}</td></tr>`;
      }
      return `<tr class="logrow click${fresh.has(h._id) ? " new" : ""}" data-id="${esc(h._id)}"><td>${fmtDateTime(s["@timestamp"])}</td><td><span class="sev ${esc(s.severity_text)}">${esc(s.severity_text || "")}</span></td><td>${esc(getPath(s, "body.text") ?? "")} ${extra}</td></tr>${detail}`;
    }).join("");
    return `<div class="filters"><div class="pills">${pills}</div><input type="search" id="q" placeholder="Buscar na mensagem ou colar um trace_id…" value="${esc(state.q)}">${state.errType ? `<span class="chipx">tipo: ${esc(state.errType)} <button data-clear="err" aria-label="Remover filtro">✕</button></span>` : ""}</div>
      <div class="card">${hits.length ? `<table><thead><tr><th>Quando</th><th>Nível</th><th>Mensagem</th></tr></thead><tbody>${rows}</tbody></table><p class="mono" style="color:var(--faint);margin:10px 0 0">${fmtN(r.hits.total.value)} registros · mostrando os 100 mais recentes · clique para detalhar</p>` : '<div class="empty" style="padding:40px">Nenhum log com esses filtros.</div>'}</div>`;
  }

  async function tracesTab() {
    const list = await es(IDX.traces, "_search", { size: 40, sort: [{ "@timestamp": "desc" }], query: base(ROOT), _source: ["name", "duration", "trace_id", "status", "@timestamp"] });
    const hits = list?.hits.hits || [];
    const rows = hits.map((h) => { const s = h._source, err = s.status?.code === "Error"; return `<tr class="click" data-trace="${esc(s.trace_id)}"><td class="mono">${fmtDateTime(s["@timestamp"])}</td><td>${esc(s.name)}</td><td class="n">${fmtDur(s.duration)}</td><td>${err ? '<span class="sev ERROR">ERRO</span>' : '<span class="sev INFO">OK</span>'}</td><td class="mono" style="color:var(--faint)">${esc((s.trace_id || "").slice(0, 12))}…</td></tr>`; }).join("");
    let wf = "";
    if (state.trace) wf = await waterfall(state.trace);
    return `<div class="card"><h2>Execuções recentes (traces raiz)</h2>${hits.length ? `<table><thead><tr><th>Quando</th><th>Operação</th><th class="n">Duração</th><th>Status</th><th>trace_id</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty" style="padding:40px">Nenhum trace no período.</div>'}</div>${wf}`;
  }

  async function waterfall(traceId) {
    const r = await es(IDX.traces, "_search", { size: 500, sort: [{ "@timestamp": "asc" }], query: { bool: { filter: [{ term: { trace_id: traceId } }, { exists: { field: "name" } }] } }, _source: ["name", "duration", "span_id", "parent_span_id", "status", "@timestamp"] });
    const spans = (r?.hits.hits || []).map((h) => ({ ...h._source, t: Date.parse(h._source["@timestamp"].replace(/(\.\d{3})\d*Z$/, "$1Z")) }));
    if (!spans.length) return '<div class="card" style="margin-top:16px"><div class="empty" style="padding:30px">Spans desse trace não encontrados.</div></div>';
    const t0 = Math.min(...spans.map((s) => s.t)), end = Math.max(...spans.map((s) => s.t + s.duration / 1e6)), total = Math.max(end - t0, 0.001);
    const byParent = {}; spans.forEach((s) => (byParent[s.parent_span_id || ""] ||= []).push(s));
    const known = new Set(spans.map((s) => s.span_id));
    const order = [];
    const walk = (s, d) => { order.push([s, d]); (byParent[s.span_id] || []).forEach((c) => walk(c, d + 1)); };
    spans.filter((s) => !s.parent_span_id || !known.has(s.parent_span_id)).forEach((s) => walk(s, 0));
    const rows = order.map(([s, d]) => {
      const left = ((s.t - t0) / total) * 100, width = Math.max((s.duration / 1e6 / total) * 100, 0.4), err = s.status?.code === "Error";
      return `<div class="wf-row" title="${esc(s.status?.message || "")}"><div class="wf-name" style="padding-left:${d * 16}px">${err ? "✖ " : ""}${esc(s.name)}</div><div class="wf-track"><div class="wf-bar ${err ? "err" : ""}" style="left:${left}%;width:${width}%"></div></div><div class="wf-d">${fmtDur(s.duration)}</div></div>`;
    }).join("");
    return `<div class="card" style="margin-top:16px"><h2>Trace <span class="mono" style="text-transform:none;letter-spacing:0">${esc(traceId)}</span></h2><div class="mono" style="color:var(--muted);margin-bottom:6px">${spans.length} spans · ${fmtDur(total * 1e6)} no total</div><div class="wf">${rows}</div></div>`;
  }

  async function metricsTab() {
    const caps = await es(IDX.metrics, "_field_caps?fields=metrics.*");
    const names = Object.keys(caps?.fields || {}).filter((f) => f.startsWith("metrics.") && Object.values(caps.fields[f]).some((t) => ["long", "double", "float", "integer"].includes(t.type))).map((f) => f.slice(8)).sort();
    if (!names.length) return '<div class="empty"><h3>Nenhuma métrica ainda</h3><p>Crie uma com <code>obskitpy.meter("app").create_counter("pedidos.processados")</code> e registre valores com <code>.add(1, {"status": "success"})</code>.</p></div>';
    const sel = names.includes(state.metric) ? state.metric : names[0]; state.metric = sel;
    const r = await es(IDX.metrics, "_search", { size: 0, query: base({ exists: { field: `metrics.${sel}` } }), aggs: { time: { date_histogram: { field: "@timestamp", fixed_interval: bucket(state.range), min_doc_count: 0, extended_bounds: { min: `now-${dateMath(state.range)}`, max: "now" } }, aggs: { k: { terms: { field: "attributes.status", size: 6, missing: "total" }, aggs: { v: { sum: { field: `metrics.${sel}` } } } } } } } });
    const buckets = (r?.aggregations.time.buckets || []).map((b) => ({ t: b.key_as_string, v: Object.fromEntries(b.k.buckets.map((x) => [x.key, x.v.value])) }));
    const keys = [...new Set(buckets.flatMap((b) => Object.keys(b.v)))];
    const colors = Object.fromEntries(keys.map((k, i) => [k, k === "failure" ? "#ff7b7b" : k === "success" ? "#19d3b6" : PALETTE[i % PALETTE.length]]));
    const totals = Object.fromEntries(keys.map((k) => [k, buckets.reduce((s, b) => s + (b.v[k] || 0), 0)]));
    return `<div class="filters"><select id="metric">${names.map((n) => `<option ${n === sel ? "selected" : ""}>${esc(n)}</option>`).join("")}</select><span class="mono" style="color:var(--muted)">${keys.map((k) => `${esc(k)}: <b style="color:${colors[k]}">${fmtN(totals[k])}</b>`).join(" · ")}</span></div><div class="card"><h2>${esc(sel)}</h2>${stackedChart(buckets, keys, colors)}</div>`;
  }

  /* ---------- orquestração ---------- */
  const TABS = { overview, logs: logsTab, traces: tracesTab, metrics: metricsTab };
  let busy = false;
  async function render(showSpin = true) {
    if (busy) return; busy = true;
    const btn = $("#refresh"); if (showSpin) { btn.classList.remove("spin"); void btn.offsetWidth; btn.classList.add("spin"); }
    $$("#tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === state.tab));
    try {
      const html = await TABS[state.tab]();
      if (html !== state.lastHtml) { $("#app").innerHTML = html; wire(); state.lastHtml = html; } // sem piscar quando nada mudou
      $("#status").innerHTML = `<i class="dot ok"></i> ${state.rt ? "ao vivo" : "atualizado"} ${new Date().toLocaleTimeString("pt-BR")}`;
    } catch (e) {
      state.lastHtml = "";
      $("#app").innerHTML = `<div class="alert"><b>Não consegui falar com o Elasticsearch.</b> ${esc(e.message)}<br>A stack está no ar? Rode <code>obskitpy status</code>.</div>`;
      $("#status").innerHTML = '<i class="dot bad"></i> sem conexão';
    } finally { busy = false; }
  }
  function wire() {
    const app = $("#app");
    wireTips(app);
    $$("[data-err]", app).forEach((tr) => tr.addEventListener("click", () => { state.errType = tr.dataset.err; state.seen = null; go("logs"); }));
    $$("[data-sev]", app).forEach((b) => b.addEventListener("click", () => { state.sev = b.dataset.sev; state.seen = null; render(false); }));
    $$("[data-clear]", app).forEach((b) => b.addEventListener("click", () => { state.errType = ""; state.seen = null; render(false); }));
    $$(".logrow", app).forEach((tr) => tr.addEventListener("click", () => { state.open = state.open === tr.dataset.id ? null : tr.dataset.id; render(false); }));
    $$("[data-trace]", app).forEach((el) => el.addEventListener("click", (e) => { e.stopPropagation(); state.trace = el.dataset.trace; go("traces", el.dataset.trace); }));
    const q = $("#q"); if (q) { let t; q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { state.q = q.value.trim(); state.seen = null; render(false).then(() => { const n = $("#q"); n.focus(); n.setSelectionRange(n.value.length, n.value.length); }); }, 350); }); }
    const m = $("#metric"); if (m) m.addEventListener("change", () => { state.metric = m.value; render(false); });
  }
  function go(tab, id) { const h = id ? `${tab}/${id}` : tab; if (location.hash.slice(1) === h) { state.tab = tab; render(false); } else location.hash = h; }
  function fromHash() {
    const [path, qs] = location.hash.slice(1).split("?");
    const [t, id] = path.split("/");
    state.tab = TABS[t] ? t : "overview"; if (id) state.trace = id;
    const wanted = qs && (qs.match(/(?:^|&)service=([^&]+)/) || [])[1];
    if (wanted && decodeURIComponent(wanted) !== state.service) selectService(decodeURIComponent(wanted), false);
    if (qs && /(^|&)realtime(=1|=true)?(&|$)/.test(qs)) setRealtime(true);
    render(false);
  }

  /* ---------- período, atualização e Realtime ---------- */
  const popHtml = (kind) => {
    const range = kind === "range", cur = range ? state.range : state.refresh;
    const presets = range ? [[5, "m"], [15, "m"], [1, "h"], [6, "h"], [24, "h"], [7, "d"]] : [[1, "s"], [5, "s"], [10, "s"], [30, "s"], [1, "m"], [5, "m"]];
    const units = Object.entries(UNIT).filter(([u]) => range || u !== "d");
    return `<h5>${range ? "Período mostrado" : "Atualizar a cada"}</h5>
      <div class="presets">${presets.map(([n, u]) => `<button data-n="${n}" data-u="${u}" class="${cur.n === n && cur.u === u ? "on" : ""}">${n} ${UNIT[u][2]}</button>`).join("")}</div>
      <label class="custom">Personalizado
        <span><input type="number" min="1" step="1" value="${cur.n}" aria-label="Quantidade"><select aria-label="Unidade">${units.map(([u, [l]]) => `<option value="${u}" ${u === cur.u ? "selected" : ""}>${l}</option>`).join("")}</select><button class="apply" type="button">Aplicar</button></span>
      </label>
      ${range ? "" : `<label class="chk"><input type="checkbox" data-auto ${state.auto ? "checked" : ""}> Atualização automática</label>`}`;
  };
  function paint() {
    document.body.classList.toggle("is-realtime", state.rt);
    $("#rt").setAttribute("aria-pressed", state.rt);
    $("#range-label").textContent = `últimos ${spanLabel(state.range)}`;
    $("#refresh-label").textContent = state.auto ? `a cada ${spanLabel(state.refresh)}` : "manual";
    $("#live").hidden = !state.rt;
  }
  const persist = () => { if (!state.rt) { localStorage.setItem("dash.range", JSON.stringify(state.range)); localStorage.setItem("dash.refresh", JSON.stringify(state.refresh)); localStorage.setItem("dash.auto", state.auto ? "1" : "0"); } };
  let timer;
  const paused = () => state.tab === "logs" && state.open;
  function schedule() {
    clearTimeout(timer);
    if (!state.auto) return;
    timer = setTimeout(() => {
      if (document.hidden) return schedule();
      if (paused()) { $("#status").innerHTML = '<i class="dot"></i> ⏸ pausado: detalhe de log aberto (feche para voltar ao vivo)'; return schedule(); }
      render(false).finally(schedule);
    }, Math.max(spanMs(state.refresh), 1000));
  }
  function setRealtime(on) {
    if (on === state.rt) return;
    if (on) { state.saved = { range: state.range, refresh: state.refresh, auto: state.auto }; state.range = { n: 5, u: "m" }; state.refresh = { n: 1, u: "s" }; state.auto = true; }
    else if (state.saved) { ({ range: state.range, refresh: state.refresh, auto: state.auto } = state.saved); state.saved = null; }
    state.rt = on; state.seen = null; paint(); render(false); schedule();
  }
  const pops = { range: $("#range-pop"), refresh: $("#refresh-pop") };
  const closePops = () => Object.values(pops).forEach((p) => (p.hidden = true));
  Object.entries(pops).forEach(([kind, pop]) => {
    $(`#${kind}-btn`).addEventListener("click", (e) => { e.stopPropagation(); const open = pop.hidden; closePops(); if (open) { pop.innerHTML = popHtml(kind); pop.hidden = false; } });
    pop.addEventListener("click", (e) => {
      e.stopPropagation();
      const set = (t) => { if (kind === "range") state.range = t; else state.refresh = t; persist(); paint(); closePops(); render(); schedule(); };
      const preset = e.target.closest(".presets button");
      if (preset) return set({ n: +preset.dataset.n, u: preset.dataset.u });
      if (e.target.closest(".apply")) {
        const n = Math.floor(+$("input[type=number]", pop).value), u = $("select", pop).value;
        if (!(n >= 1)) return $("input[type=number]", pop).focus();
        set({ n, u });
      }
    });
    pop.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.matches("input[type=number]")) $(".apply", pop).click(); });
    pop.addEventListener("change", (e) => { if (e.target.matches("[data-auto]")) { state.auto = e.target.checked; persist(); paint(); schedule(); } });
  });
  document.addEventListener("click", closePops);
  $("#rt").addEventListener("click", () => setRealtime(!state.rt));
  document.addEventListener("keydown", (e) => {
    if (/input|textarea|select/i.test(document.activeElement?.tagName || "") || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "r" || e.key === "R") setRealtime(!state.rt);
    if (e.key === "Escape") closePops();
  });

  /* ---------- projetos (serviços) ---------- */
  function paintServices() {
    const sel = $("#svc"), list = [...new Set([state.service, ...state.services])].sort();
    sel.innerHTML = list.map((s) => `<option value="${esc(s)}" ${s === state.service ? "selected" : ""}>${esc(s)}</option>`).join("");
    sel.title = list.length > 1 ? "Escolha o projeto (automação) que você quer ver" : "Projeto (automação) que você está vendo";
    document.title = `dash-obskitpy · ${state.service}`;
  }
  function selectService(name, doRender = true) {
    state.service = name; localStorage.setItem("dash.service", name);
    Object.assign(state, { seen: null, open: null, trace: null, q: "", errType: "", sev: "", lastHtml: "" });
    paintServices();
    if (doRender) render(false);
  }
  async function loadServices() {
    const found = new Set([CFG.service, ...(CFG.services || [])]);
    for (const idx of [IDX.logs, IDX.traces, IDX.metrics]) {
      try {
        const r = await es(idx, "_search", { size: 0, aggs: { s: { terms: { field: "resource.attributes.service.name", size: 100 } } } });
        (r?.aggregations.s.buckets || []).forEach((b) => found.add(b.key));
      } catch { /* a stack pode ainda não ter dados */ }
    }
    state.services = [...found].sort();
    paintServices();
  }
  $("#svc").addEventListener("change", (e) => selectService(e.target.value));
  $("#svc").addEventListener("mousedown", loadServices); // atualiza a lista ao abrir
  setInterval(loadServices, 30000);
  paintServices(); loadServices();
  if (CFG.kibana) { const k = $("#kibana"); k.href = CFG.kibana; k.hidden = false; }
  $("#refresh").addEventListener("click", () => render());
  addEventListener("hashchange", fromHash);
  paint(); schedule();
  fromHash();
})();
