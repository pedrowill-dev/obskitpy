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
  const emptyState = () => {
    state.emptyShown = true;
    const others = state.services.filter((s) => s !== state.service);
    const hint = others.length
      ? `<p class="other">Mas há dados de outros projetos: ${others.map((s) => `<a href="#overview?service=${encodeURIComponent(s)}" data-svc="${esc(s)}">${esc(s)}</a>`).join(" · ")}.<br>Se o seu código envia como um deles, ajuste o nome em <code>obskitpy.setup("${esc(state.service)}")</code> ou escolha o projeto no seletor do topo.</p>`
      : "";
    return `<div class="empty"><h3>Ainda sem dados para <code>${esc(state.service)}</code></h3><p>Rode a sua aplicação (com <code>obskitpy.setup("${esc(state.service)}")</code>) ou gere um exemplo com <code>python obskitpy_demo.py</code>.<br>Os dados aparecem aqui em segundos.</p>${hint}</div>`;
  };

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


  /* ---------- Integrações (portal de alertas) ---------- */
  const portal = { user: null, kinds: [], data: null, form: false, kind: "telegram" };
  async function papi(path, opts = {}) {
    let r;
    try {
      r = await fetch(`api/v1/${path}`, { method: opts.method || "GET", credentials: "same-origin",
        headers: { "content-type": "application/json", "X-Obskitpy-Csrf": "1" }, body: opts.body ? JSON.stringify(opts.body) : undefined });
    } catch { const e = new Error("sem conexão com o portal"); e.status = 0; throw e; }
    if (r.status === 204) return null;
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const e = new Error(typeof data.detail === "string" ? data.detail : data.detail?.[0]?.msg || `erro ${r.status}`);
      e.status = r.status; throw e;
    }
    return data;
  }
  function toast(msg, bad) {
    const t = $("#toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 6000);
  }
  const STATUS = { ok: ["OK", "ok"], failed: ["FALHOU", "bad"], untested: ["NÃO TESTADA", "idle"] };
  const KIND_ICON = { telegram: "✈", teams: "▦", slack: "#", email: "✉" };
  const canAdmin = () => portal.user?.role === "admin";
  const canTest = () => ["operator", "admin"].includes(portal.user?.role);

  const notEnabled = () => `<div class="empty"><h3>O portal de alertas ainda não está ativado</h3>
    <p>Ele guarda as integrações (Telegram, Teams, Slack, e-mail), destinos e contatos em um PostgreSQL local.<br>Para ativar, rode na pasta do projeto:</p>
    <p><code>obskitpy portal enable</code></p></div>`;

  const loginView = (msg = "") => `<div class="card login"><h2>Entrar no portal</h2>
    <form id="login" autocomplete="on"><label>E-mail<input name="email" type="email" required autocomplete="username"></label>
    <label>Senha<input name="password" type="password" required autocomplete="current-password"></label>
    ${msg ? `<p class="err">${esc(msg)}</p>` : ""}<button class="btn" type="submit">Entrar</button></form>
    <p class="mono hint">As credenciais do administrador ficam em <code>observability/.portal.env</code>.</p></div>`;

  function fieldHtml(f, secret) {
    const id = `${secret ? "s" : "c"}_${f.name}`;
    if (f.type === "checkbox") return `<label class="chk"><input type="checkbox" data-${secret ? "secret" : "config"}="${f.name}" ${f.default ? "checked" : ""}> ${esc(f.label)}</label>`;
    return `<label>${esc(f.label)}${f.required ? "" : " <small>(opcional)</small>"}<input id="${id}" data-${secret ? "secret" : "config"}="${f.name}" type="${f.type === "number" ? "number" : f.type === "password" ? "password" : "text"}" ${f.default != null ? `value="${esc(f.default)}"` : ""} placeholder="${esc(f.placeholder || "")}" autocomplete="off"></label>`;
  }

  function formHtml() {
    const manual = portal.kinds.filter((x) => !x.oauth);
    if (!manual.some((x) => x.kind === portal.kind)) portal.kind = manual[0].kind;
    const k = manual.find((x) => x.kind === portal.kind);
    return `<div class="card pform"><h2>Nova integração</h2><form id="newint">
      <label>Provedor<select id="kind">${manual.map((x) => `<option value="${x.kind}" ${x.kind === k.kind ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select></label>
      <p class="mono hint">${esc(k.help)}</p>
      <label>Nome da conexão<input id="intname" required placeholder="Ex.: Bot de operações"></label>
      ${k.config_fields.map((f) => fieldHtml(f, false)).join("")}${k.secret_fields.map((f) => fieldHtml(f, true)).join("")}
      <div class="row"><button class="btn" type="submit">Salvar</button><button class="btn ghost" type="button" data-act="cancel-form">Cancelar</button></div></form></div>`;
  }

  const integrationCard = (c, dests) => {
    const [label, cls] = STATUS[c.status] || STATUS.untested;
    const mine = dests.filter((d) => d.connection_id === c.id);
    const test = canTest() ? `<div class="row"><select data-dest-for="${c.id}" aria-label="Destino do teste"><option value="">Só validar credenciais</option>${mine.map((d) => `<option value="${d.id}">Enviar para ${esc(d.name)}</option>`).join("")}</select>
      <button class="btn small" data-act="test" data-id="${c.id}">Testar</button>${canAdmin() ? `<button class="btn small ghost danger" data-act="del-int" data-id="${c.id}">Remover</button>` : ""}</div>` : "";
    return `<div class="pcard"><div class="ptop"><span class="picon">${KIND_ICON[c.kind] || "•"}</span><div><b>${esc(c.name)}</b><small>${esc(c.label)}</small></div><span class="pstat ${cls}">${label}</span></div>
      <p class="pmeta">${c.destinations} destino(s)${c.last_tested_at ? ` · testada em ${fmtDateTime(c.last_tested_at)}` : ""}</p>
      ${c.last_error ? `<p class="perr">${esc(c.last_error)}</p>` : ""}${test}</div>`;
  };

  const SLACK_ERR = { access_denied: "Você cancelou a conexão com o Slack.", invalid_code: "O código do Slack expirou: clique em Conectar de novo.",
    bad_client_secret: "O Client Secret está incorreto. Confira em Basic Information → App Credentials.", invalid_client_id: "O Client ID está incorreto.",
    redirect_uri_mismatch: "A URL de retorno do app não confere. Crie o app pelo botão do manifesto (ele já traz a URL certa).",
    "sessão inválida": "Sua sessão expirou. Entre de novo e tente conectar." };
  const SLACK_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M9.6 3a2 2 0 1 0 0 4h2V5a2 2 0 0 0-2-2Zm0 5.3H4.4a2 2 0 1 0 0 4h5.2a2 2 0 1 0 0-4Zm10 2a2 2 0 1 0-4 0v2h2a2 2 0 0 0 2-2Zm-5.3 0V5a2 2 0 1 0-4 0v5.3a2 2 0 1 0 4 0ZM14.4 21a2 2 0 1 0 0-4h-2v2a2 2 0 0 0 2 2Zm0-5.3h5.2a2 2 0 1 0 0-4h-5.2a2 2 0 1 0 0 4ZM4.4 13.7a2 2 0 1 0 4 0v-2h-2a2 2 0 0 0-2 2Zm5.3 0V19a2 2 0 1 0 4 0v-5.3a2 2 0 1 0-4 0Z"/></svg>';

  const slackSetupHtml = (st) => `<div class="card pform slacksetup"><h2>Conectar com Slack — configuração de uma vez só</h2>
    <p class="mono hint">O Slack exige que cada instalação tenha o seu próprio app (é rápido, e você faz isso uma única vez). Depois, conectar é só um clique.</p>
    <ol class="steps2"><li><b>Crie o app do Slack.</b> O link abre o Slack com o nome, as permissões e a URL de retorno já preenchidos; é só confirmar em <i>Create</i>.<br>
      <a class="btn small" href="${esc(st.manifest_url)}" target="_blank" rel="noopener">Criar o app no Slack ↗</a></li>
      <li><b>Copie as credenciais</b> da página do app (<i>Basic Information → App Credentials</i>) e cole aqui:
        <form id="slackapp" class="grid-form"><label>Client ID<input id="sclientid" required autocomplete="off" placeholder="1234567890.1234567890" value="${esc(st.client_id)}"></label>
        <label>Client Secret<input id="sclientsecret" type="password" required autocomplete="off" placeholder="cole o Client Secret"></label>
        <div class="row"><button class="btn" type="submit">Salvar e conectar com Slack</button><button class="btn ghost" type="button" data-act="slack-cancel">Cancelar</button></div></form></li></ol>
    <p class="mono hint">${st.mode === "relay" ? `O Slack só aceita retorno por HTTPS, então a volta passa por <code>${esc(st.redirect_uri)}</code>, uma página estática que apenas devolve você para este endereço local (<code>${esc(location.origin)}</code>). Nada é guardado lá.` : `A volta do Slack usa o endereço do portal: <code>${esc(st.redirect_uri)}</code>.`}</p></div>`;

  const slackCard = (c, dests) => {
    const [label, cls] = STATUS[c.status] || STATUS.untested;
    const mine = dests.filter((d) => d.connection_id === c.id);
    const admin = canAdmin();
    return `<div class="pcard slackcard" data-slack="${c.id}"><div class="ptop"><span class="picon slack">#</span><div><b>${esc(c.name)}</b><small>Slack · conectado${c.config.team_name ? ` a ${esc(c.config.team_name)}` : ""}</small></div><span class="pstat ${cls}">${label}</span></div>
      <div class="slackform"><label>Nome do robô<input data-slackcfg="bot_name" value="${esc(c.config.bot_name || "obskitpy")}" maxlength="80" ${admin ? "" : "disabled"}></label>
      <label>Ícone (emoji)<input data-slackcfg="icon_emoji" value="${esc(c.config.icon_emoji || "")}" placeholder=":eyes:" ${admin ? "" : "disabled"}></label>
      <label class="wide">Canal<select data-chan-for="${c.id}" ${admin ? "" : "disabled"}><option value="">${mine.length ? "Carregando canais…" : "Carregando canais…"}</option></select></label></div>
      ${c.last_error ? `<p class="perr">${esc(c.last_error)}</p>` : ""}
      <p class="pmeta">O robô aparece no Slack com o nome e o ícone acima${c.last_tested_at ? ` · testado em ${fmtDateTime(c.last_tested_at)}` : ""}</p>
      <div class="row">${admin ? `<button class="btn small" data-act="slack-save" data-id="${c.id}">Salvar</button>` : ""}${canTest() ? `<button class="btn small" data-act="slack-test" data-id="${c.id}">Salvar e enviar teste</button>` : ""}
      ${admin ? `<button class="btn small ghost" data-act="slack-connect" title="Conectar de novo (troca o token)">Reconectar</button><button class="btn small ghost danger" data-act="del-int" data-id="${c.id}">Remover</button>` : ""}</div></div>`;
  };

  const destForm = (ints) => canAdmin() && ints.length ? `<form id="newdest" class="inline"><select id="dconn">${ints.map((c) => `<option value="${c.id}">${esc(c.name)} (${esc(c.label)})</option>`).join("")}</select>
    <input id="dname" required placeholder="Nome do destino (ex.: Operações)"><input id="dtarget" placeholder="ID do chat / e-mail (se o provedor pedir)"><button class="btn small" type="submit">Adicionar destino</button></form>` : "";

  async function integrationsTab() {
    try { await papi("health"); } catch { return notEnabled(); }
    try { portal.user = await papi("auth/me"); } catch (e) { portal.user = null; if (e.status !== 401) return notEnabled(); }
    if (!portal.user) return loginView();
    const q = new URLSearchParams(location.hash.split("?")[1] || "");
    if (q.has("connected") || q.has("slack_error")) {
      if (q.has("connected")) toast("Slack conectado! Agora escolha o canal e o nome do robô.");
      else toast(SLACK_ERR[q.get("slack_error")] || `O Slack respondeu: ${q.get("slack_error")}`, true);
      history.replaceState(null, "", "#integrations");
    }
    const [kinds, ints, dests, contacts, groups, slackSt] = await Promise.all([papi("integrations/kinds"), papi("integrations"), papi("destinations"), papi("contacts"), papi("contact-groups"), papi("oauth/slack/status")]);
    portal.slack = slackSt; portal.ints = ints; portal.dests = dests;
    const audit = canAdmin() ? await papi("audit?limit=30") : [];
    portal.kinds = kinds;
    const rows = (list, cols) => list.length ? `<table><thead><tr>${cols.map((c) => `<th>${c[0]}</th>`).join("")}</tr></thead><tbody>${list.map((x) => `<tr>${cols.map((c) => `<td>${c[1](x)}</td>`).join("")}</tr>`).join("")}</tbody></table>` : '<p class="mono hint">Nada cadastrado ainda.</p>';
    return `<div class="pbar"><span>Logado como <b>${esc(portal.user.email)}</b> <span class="sev INFO">${esc(portal.user.role)}</span></span><button class="btn small ghost" data-act="logout">Sair</button></div>
      <div class="psec"><div class="phead"><h2>Conectar com um clique</h2></div>
        <div class="connect">${canAdmin() ? `<button class="btn slackbtn" data-act="slack-connect">${SLACK_ICON} Conectar com Slack</button>` : '<span class="mono hint">Peça a um administrador para conectar o Slack.</span>'}
        <span class="mono hint">Depois de conectar, você escolhe o canal e o nome do robô.${slackSt.configured ? "" : " (Na primeira vez, criamos o app do Slack com você: leva 1 minuto.)"}</span>${canAdmin() && slackSt.configured ? '<button class="btn small ghost" data-act="slack-setup">Trocar credenciais do app</button>' : ""}</div>
        ${portal.slackSetup ? slackSetupHtml(slackSt) : ""}</div>
      <div class="psec"><div class="phead"><h2>Integrações</h2>${canAdmin() ? '<button class="btn small" data-act="new-form">+ Nova integração (webhook, bot, e-mail)</button>' : ""}</div>
        ${portal.form ? formHtml() : ""}
        <div class="pgrid">${ints.length ? ints.map((c) => (c.kind === "slack_app" ? slackCard(c, dests) : integrationCard(c, dests))).join("") : '<div class="card"><p class="mono hint">Nenhuma integração ainda. Conecte o Telegram, o Teams, o Slack ou um e-mail para receber alertas.</p></div>'}</div></div>
      <div class="psec card"><div class="phead"><h2>Destinos</h2></div>${destForm(ints)}
        ${rows(dests, [["Destino", (d) => esc(d.name)], ["Integração", (d) => esc(d.connection)], ["Alvo", (d) => `<code>${esc(d.target || "—")}</code>`], ["", (d) => canAdmin() ? `<button class="btn small ghost danger" data-act="del-dest" data-id="${d.id}">Remover</button>` : ""]])}</div>
      <div class="psec grid2"><div class="card"><div class="phead"><h2>Contatos</h2></div>${canAdmin() ? `<form id="newcontact" class="inline"><input id="cname" required placeholder="Nome"><input id="cemail" type="email" placeholder="E-mail (opcional)"><button class="btn small" type="submit">Adicionar</button></form>` : ""}
        ${rows(contacts, [["Nome", (c) => esc(c.name)], ["E-mail", (c) => esc(c.email || "—")], ["", (c) => canAdmin() ? `<button class="btn small ghost danger" data-act="del-contact" data-id="${c.id}">Remover</button>` : ""]])}</div>
        <div class="card"><div class="phead"><h2>Grupos</h2></div>${canAdmin() ? `<form id="newgroup" class="inline"><input id="gname" required placeholder="Ex.: Equipe SAP"><button class="btn small" type="submit">Criar grupo</button></form>` : ""}
        ${rows(groups, [["Grupo", (g) => esc(g.name)], ["Membros", (g) => g.member_ids.length], ["", (g) => canAdmin() ? `<button class="btn small ghost danger" data-act="del-group" data-id="${g.id}">Remover</button>` : ""]])}</div></div>
      ${canAdmin() ? `<div class="psec card"><div class="phead"><h2>Auditoria</h2></div>${rows(audit, [["Quando", (a) => fmtDateTime(a.at)], ["Quem", (a) => esc(a.actor)], ["Ação", (a) => `<code>${esc(a.action)}</code>`]])}</div>` : ""}`;
  }

  async function slackConnect(afterSave) {
    if (!portal.slack?.configured && !afterSave) { portal.slackSetup = true; state.lastHtml = ""; return render(false); }
    try {
      const r = await papi("oauth/slack/start", { method: "POST", body: { origin: location.origin } });
      toast("Abrindo o Slack para você autorizar…");
      location.href = r.url;
    } catch (e) { toast(e.message, true); }
  }
  async function loadChannels(sel) {
    const id = +sel.dataset.chanFor, current = (portal.dests || []).find((d) => d.connection_id === id);
    try {
      const chans = await papi(`integrations/${id}/slack/channels`);
      sel.innerHTML = `<option value="">Escolha um canal…</option>` + chans.map((c) => `<option value="${esc(c.id)}" data-name="${esc(c.name)}" ${current && current.target === c.id ? "selected" : ""}>${c.private ? "🔒 " : "#"}${esc(c.name)}${c.member || c.private ? "" : " (o robô entra sozinho)"}</option>`).join("");
    } catch (e) { sel.innerHTML = `<option value="">Não consegui listar os canais</option>`; toast(e.message, true); }
  }
  async function slackSave(id, thenTest, app) {
    const card = $(`[data-slack="${id}"]`, app), btn = $(`[data-act="${thenTest ? "slack-test" : "slack-save"}"]`, card);
    const cfg = Object.fromEntries($$("[data-slackcfg]", card).map((i) => [i.dataset.slackcfg, i.value.trim()]));
    if (!cfg.icon_emoji) delete cfg.icon_emoji;
    const sel = $("[data-chan-for]", card), channel = sel.value, name = sel.selectedOptions[0]?.dataset.name;
    btn.disabled = true;
    try {
      if (canAdmin()) await papi(`integrations/${id}`, { method: "PUT", body: { config: cfg } });
      let dest = (portal.dests || []).find((d) => d.connection_id === +id && d.target === channel);
      if (channel && !dest && canAdmin()) dest = await papi("destinations", { method: "POST", body: { connection_id: +id, name: `#${name}`, target: channel } });
      if (thenTest) {
        if (!dest) throw new Error("Escolha um canal para enviar o teste.");
        const r = await papi(`integrations/${id}/test`, { method: "POST", body: { destination_id: dest.id } });
        toast(r.ok ? `Mensagem enviada para #${name}. Confira no Slack!` : r.detail, !r.ok);
      } else toast("Configuração do Slack salva.");
    } catch (e) { toast(e.message, true); }
    state.lastHtml = ""; render(false);
  }

  function portalWire(app) {
    const run = async (fn, okMsg) => { try { await fn(); if (okMsg) toast(okMsg); } catch (e) { if (e.status === 401) portal.user = null; toast(e.message, true); } state.lastHtml = ""; render(false); };
    const sa = $("#slackapp", app);
    if (sa) sa.addEventListener("submit", (e) => {
      e.preventDefault();
      run(async () => { await papi("oauth/slack/app", { method: "PUT", body: { client_id: $("#sclientid", sa).value, client_secret: $("#sclientsecret", sa).value } }); portal.slackSetup = false; await slackConnect(true); });
    });
    $$("[data-chan-for]", app).forEach((sel) => loadChannels(sel));
    const form = $("#login", app);
    if (form) form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await papi("auth/login", { method: "POST", body: { email: form.email.value, password: form.password.value } }); state.lastHtml = ""; render(false); }
      catch (err) { $(".card.login", app).outerHTML = loginView(err.message); portalWire($("#app")); }
    });
    $$("[data-act]", app).forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.id, act = b.dataset.act;
      if (act === "logout") return run(() => papi("auth/logout", { method: "POST" }).then(() => (portal.user = null)));
      if (act === "slack-connect") return slackConnect();
      if (act === "slack-setup") { portal.slackSetup = true; state.lastHtml = ""; return render(false); }
      if (act === "slack-cancel") { portal.slackSetup = false; state.lastHtml = ""; return render(false); }
      if (act === "slack-save" || act === "slack-test") return slackSave(id, act === "slack-test", app);
      if (act === "new-form") { portal.form = true; state.lastHtml = ""; return render(false); }
      if (act === "cancel-form") { portal.form = false; state.lastHtml = ""; return render(false); }
      if (act === "test") {
        const dest = $(`[data-dest-for="${id}"]`, app).value;
        b.disabled = true; b.textContent = "Testando…";
        return run(async () => { const r = await papi(`integrations/${id}/test`, { method: "POST", body: { destination_id: dest ? +dest : null } }); toast(r.detail + (r.retry_after ? ` (tente de novo em ${r.retry_after}s)` : ""), !r.ok); });
      }
      const confirmDel = (what) => confirm(`Remover ${what}? Esta ação não pode ser desfeita.`);
      if (act === "del-int" && confirmDel("esta integração e os seus destinos")) return run(() => papi(`integrations/${id}`, { method: "DELETE" }), "Integração removida");
      if (act === "del-dest" && confirmDel("este destino")) return run(() => papi(`destinations/${id}`, { method: "DELETE" }), "Destino removido");
      if (act === "del-contact" && confirmDel("este contato")) return run(() => papi(`contacts/${id}`, { method: "DELETE" }), "Contato removido");
      if (act === "del-group" && confirmDel("este grupo")) return run(() => papi(`contact-groups/${id}`, { method: "DELETE" }), "Grupo removido");
    }));
    const kind = $("#kind", app);
    if (kind) kind.addEventListener("change", () => { portal.kind = kind.value; state.lastHtml = ""; render(false); });
    const ni = $("#newint", app);
    if (ni) ni.addEventListener("submit", (e) => {
      e.preventDefault();
      const grab = (attr) => Object.fromEntries($$(`[data-${attr}]`, ni).map((el) => [el.dataset[attr], el.type === "checkbox" ? el.checked : el.type === "number" ? (el.value === "" ? null : +el.value) : el.value.trim()]));
      run(async () => { await papi("integrations", { method: "POST", body: { kind: portal.kind, name: $("#intname", ni).value, config: grab("config"), secrets: grab("secret") } }); portal.form = false; }, "Integração salva. Use “Testar” para confirmar.");
    });
    const nd = $("#newdest", app);
    if (nd) nd.addEventListener("submit", (e) => { e.preventDefault(); run(() => papi("destinations", { method: "POST", body: { connection_id: +$("#dconn", nd).value, name: $("#dname", nd).value, target: $("#dtarget", nd).value } }), "Destino adicionado"); });
    const nc = $("#newcontact", app);
    if (nc) nc.addEventListener("submit", (e) => { e.preventDefault(); run(() => papi("contacts", { method: "POST", body: { name: $("#cname", nc).value, email: $("#cemail", nc).value } }), "Contato adicionado"); });
    const ng = $("#newgroup", app);
    if (ng) ng.addEventListener("submit", (e) => { e.preventDefault(); run(() => papi("contact-groups", { method: "POST", body: { name: $("#gname", ng).value } }), "Grupo criado"); });
  }

  /* ---------- orquestração ---------- */
  const TABS = { overview, logs: logsTab, traces: tracesTab, metrics: metricsTab, integrations: integrationsTab };
  let busy = false;
  async function render(showSpin = true) {
    if (busy) return; busy = true; state.emptyShown = false;
    const btn = $("#refresh"); if (showSpin) { btn.classList.remove("spin"); void btn.offsetWidth; btn.classList.add("spin"); }
    $$("#tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === state.tab));
    document.body.dataset.tab = state.tab;
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
    if (state.tab === "integrations") return portalWire(app);
    $$("[data-svc]", app).forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); selectService(a.dataset.svc); }));
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
      if (document.hidden || state.tab === "integrations") return schedule(); // formulários não são recarregados sozinhos
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
    const before = state.services.join("|");
    state.services = [...found].sort();
    paintServices();
    if (state.emptyShown && before !== state.services.join("|")) render(false); // o estado vazio passa a sugerir os outros projetos
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
