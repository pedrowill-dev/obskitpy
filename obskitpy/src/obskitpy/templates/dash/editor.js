/* obskitpy · editor de automações (fluxos visuais). JS puro, sem dependências.
   Montado pela aba "Automações" do dash: ObsAuto.mount(raiz, api). */
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const W = 214, HEAD = 62, GAP = 28;               // tamanho do cartão e espaço entre as saídas
  const ACCENT = { Gatilhos: "teal", "Lógica": "amber", "Ações": "blue" };
  const ic = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICON = {
    "trigger.error_logs": ic('<path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4M12 17h.01"/>'),
    "trigger.failed_spans": ic('<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>'),
    "trigger.no_data": ic('<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>'),
    "trigger.schedule": ic('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    "logic.if": ic('<circle cx="6" cy="5" r="2.2"/><circle cx="6" cy="19" r="2.2"/><circle cx="18" cy="9" r="2.2"/><path d="M6 7.2v9.6M18 11.2c0 4-12 2-12 5.6"/>'),
    "action.notify": ic('<path d="M21 3 10.5 13.5M21 3l-7 18-3.5-7.5L3 10l18-7Z"/>'),
  };
  const PLUS = ic('<path d="M12 5v14M5 12h14"/>'), MINUS = ic('<path d="M5 12h14"/>'), FIT = ic('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>');
  // o que cada variável de mensagem traz (texto do balão) e um exemplo para a prévia
  const VAR_HELP = {
    service: ["O nome da automação (serviço) que gerou o evento.", () => (window.__svc || "meu-robo")],
    value: ["O número medido pelo gatilho na janela: quantidade de erros, de falhas ou de dados recebidos.", () => "6"],
    window: ["A janela de tempo avaliada, em minutos.", () => "10"],
    automation: ["O nome desta automação, como você escreveu no topo do editor.", () => (window.__auto || "Minha automação")],
    event: ["O que aconteceu: disparou, lembrete, normalizou, agendado ou teste.", () => "disparou"],
    erro: ["O erro mais recente da janela: o tipo da exceção Python e a mensagem.", () => "KeyError: 'results' (resposta da API sem o campo esperado)"],
    origem: ["Onde o erro nasceu no seu código: arquivo:função:linha.", () => "main.py:buscar_noticias:42"],
    trace: ["O traceback Python completo do erro mais recente (as últimas linhas, se for grande). Dica: use em mensagens de Slack/e-mail, não em títulos.", () => 'Traceback (most recent call last):\n  File "main.py", line 42, in buscar_noticias\n    dados["results"]\nKeyError: \'results\''],
    time: ["Data e hora em que o aviso foi gerado.", () => new Date().toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).replace(",", "")],
  };
  const sample = (text) => text.replace(/\{\{\s*(\w+)\s*\}\}/g, (m, k) => (VAR_HELP[k] ? VAR_HELP[k][1]() : m));
  const uid = () => "n" + Math.random().toString(36).slice(2, 9);

  function mount(root, api) {
    const { papi, toast, esc, fmtDateTime } = api;
    const S = { catalog: [], byType: {}, variables: [], evaluator: true, dests: [], list: [], runs: [], mode: "list", cur: null, sel: null,
                view: { x: 40, y: 40, k: 1 }, tab: "editor", results: {}, states: {}, lastField: null };
    let el = {}, poll = null;
    const admin = () => api.canAdmin(), tester = () => api.canTest();

    /* ---------- dados ---------- */
    async function load() {
      const [nodes, list, dests, runs] = await Promise.all([papi("automations/nodes"), papi("automations"), papi("destinations"), papi("automations/runs?limit=30")]);
      Object.assign(S, { catalog: nodes.nodes, variables: nodes.variables, evaluator: nodes.evaluator, list, dests, runs });
      S.byType = Object.fromEntries(S.catalog.map((n) => [n.type, n]));
    }
    const destName = (id) => S.dests.find((d) => d.id === id)?.name;
    const svcLabel = (s) => s || "todos os serviços";

    /* ---------- resumo curto de cada etapa ---------- */
    function summary(n) {
      const c = n.config, svc = c.service || "todos";
      switch (n.type) {
        case "trigger.error_logs": return `≥ ${c.threshold} em ${c.window_minutes} min · ${svc}`;
        case "trigger.failed_spans": return `≥ ${c.threshold} em ${c.window_minutes} min · ${svc}`;
        case "trigger.no_data": return `sem dados ${c.window_minutes} min · ${svc}`;
        case "trigger.schedule": return `a cada ${c.every_minutes} min · ${svc}`;
        case "logic.if": return `${{ value: "valor", window: "janela", service: "serviço" }[c.field] || c.field} ${{ contains: "contém" }[c.operator] || c.operator.replace(">=", "≥").replace("<=", "≤").replace("==", "=").replace("!=", "≠")} ${c.value}`;
        case "action.notify": return destName(c.destination_id) ? `para ${destName(c.destination_id)}` : "⚠ escolha o destino";
        default: return "";
      }
    }

    /* ---------- tela: lista ---------- */
    const firing = (a) => Object.values(a.state || {}).some((s) => s.state === "firing");
    function listView() {
      const cards = S.list.map((a) => {
        const triggers = a.graph.nodes.filter((n) => n.type.startsWith("trigger.")).length;
        const [label, cls] = !a.enabled ? ["PAUSADA", "idle"] : firing(a) ? ["DISPARADA", "bad"] : ["ATIVA", "ok"];
        const last = S.runs.find((r) => r.automation_id === a.id);
        return `<div class="pcard acard" data-open="${a.id}"><div class="ptop"><span class="picon">⚡</span><div><b>${esc(a.name)}</b><small>${a.graph.nodes.length} etapas · ${triggers} gatilho(s)</small></div><span class="pstat ${cls}">${label}</span></div>
          <p class="pmeta">${last ? `Última execução: ${esc(last.event)} · ${fmtDateTime(last.at)}${last.ok ? "" : " · com falha"}` : "Ainda não executou"}</p>
          <div class="row"><button class="btn small" data-a="open" data-id="${a.id}">Abrir editor</button>${admin() ? `<button class="btn small ghost" data-a="toggle" data-id="${a.id}">${a.enabled ? "Pausar" : "Ativar"}</button><button class="btn small ghost danger" data-a="del" data-id="${a.id}">Remover</button>` : ""}</div></div>`;
      }).join("");
      return `${S.evaluator ? "" : '<div class="alert">O avaliador está desligado neste portal (sem acesso ao Elasticsearch). Rode <code>obskitpy portal enable</code> para atualizar a stack.</div>'}
        <div class="psec"><div class="phead"><h2>Automações</h2>${admin() ? '<button class="btn small" data-a="new">+ Nova automação</button>' : ""}</div>
          <p class="mono hint">Desenhe o que deve acontecer a partir dos logs: <b>gatilho</b> (o que observar) → <b>condição</b> (opcional) → <b>ação</b> (avisar um destino). As conexões ficam em <a href="#integrations">Integrações</a>.</p>
          ${S.pick ? picker() : ""}
          <div class="pgrid">${cards || (S.pick ? "" : '<div class="card"><p class="mono hint">Nenhuma automação ainda. Clique em “+ Nova automação” e comece por um modelo pronto.</p></div>')}</div></div>
        <div class="psec card"><div class="phead"><h2>Execuções recentes</h2></div>${runsTable(S.runs, true)}</div>`;
    }
    function runsTable(runs, withName) {
      if (!runs.length) return '<p class="mono hint">Nenhuma execução ainda.</p>';
      return `<table><thead><tr><th>Quando</th>${withName ? "<th>Automação</th>" : ""}<th>Evento</th><th>Valor</th><th>Resultado</th></tr></thead><tbody>${runs.map((r) =>
        `<tr><td>${fmtDateTime(r.at)}</td>${withName ? `<td>${esc(r.automation_name)}</td>` : ""}<td><code>${esc(r.event)}</code></td><td>${r.value ?? "—"}</td>
        <td>${r.steps.length ? r.steps.map((s) => `<span class="rstep ${s.ok ? "ok" : "bad"}" title="${esc(s.detail)}">${s.ok ? "✓" : "✗"} ${esc(s.label)}: ${esc(s.detail)}</span>`).join("") : '<span class="mono hint">nenhuma etapa ligada</span>'}</td></tr>`).join("")}</tbody></table>`;
    }

    /* ---------- modelos ---------- */
    const TEMPLATES = [
      { id: "erro", t: "Avisar quando der erro", d: "Gatilho de erros → mensagem no canal, e outra quando normalizar.", build: (svc, dest) => ({ name: "Avisar quando der erro",
        nodes: [N("t", "trigger.error_logs", 0, 60, { service: svc, threshold: 1, window_minutes: 5, repeat_minutes: 30 }),
                N("a", "action.notify", 330, 40, { destination_id: dest, severity: "critical", title: "🔴 {{automation}}", message: "{{value}} erro(s) em {{service}} nos últimos {{window}} min.\n\n{{erro}}\nOnde: {{origem}}\n\n{{trace}}" }),
                N("b", "action.notify", 330, 200, { destination_id: dest, severity: "info", title: "✅ Normalizou", message: "{{service}} voltou ao normal." })],
        edges: [E("t", "fired", "a"), E("t", "resolved", "b")] }) },
      { id: "parou", t: "Avisar quando a automação parar", d: "Dispara se nenhum dado chegar por um tempo (robô travado ou que não rodou).", build: (svc, dest) => ({ name: "Automação parada",
        nodes: [N("t", "trigger.no_data", 0, 60, { service: svc, window_minutes: 30, repeat_minutes: 60 }),
                N("a", "action.notify", 330, 40, { destination_id: dest, severity: "critical", title: "⏸ {{service}} parou", message: "Nenhum dado nos últimos {{window}} min." }),
                N("b", "action.notify", 330, 200, { destination_id: dest, severity: "info", title: "▶ {{service}} voltou", message: "Os dados voltaram a chegar." })],
        edges: [E("t", "fired", "a"), E("t", "resolved", "b")] }) },
      { id: "grave", t: "Separar erro leve de grave", d: "Uma condição escolhe o caminho: muitos erros = crítico, poucos = atenção.", build: (svc, dest) => ({ name: "Erro leve ou grave",
        nodes: [N("t", "trigger.error_logs", 0, 100, { service: svc, threshold: 1, window_minutes: 10, repeat_minutes: 30 }),
                N("c", "logic.if", 300, 100, { field: "value", operator: ">=", value: "10" }),
                N("a", "action.notify", 620, 20, { destination_id: dest, severity: "critical", title: "🔴 Muitos erros", message: "{{value}} erros em {{service}} ({{window}} min)." }),
                N("b", "action.notify", 620, 210, { destination_id: dest, severity: "warning", title: "🟡 Alguns erros", message: "{{value}} erro(s) em {{service}} ({{window}} min)." })],
        edges: [E("t", "fired", "c"), E("c", "yes", "a"), E("c", "no", "b")] }) },
      { id: "resumo", t: "Resumo periódico", d: "A cada intervalo, manda a contagem de erros.", build: (svc, dest) => ({ name: "Resumo de erros",
        nodes: [N("t", "trigger.schedule", 0, 60, { service: svc, every_minutes: 60 }),
                N("a", "action.notify", 330, 60, { destination_id: dest, severity: "info", title: "📊 Resumo de {{service}}", message: "{{value}} erro(s) na última hora ({{time}})." })],
        edges: [E("t", "fired", "a")] }) },
      { id: "vazio", t: "Em branco", d: "Comece do zero e monte o seu fluxo.", build: () => ({ name: "Nova automação", nodes: [], edges: [] }) },
    ];
    function N(id, type, x, y, config) { return { id, type, x, y, config: defaults(type, config) }; }
    function E(source, port, target) { return { source, port, target }; }
    function defaults(type, over = {}) {
      const cfg = {};
      for (const f of S.byType[type].fields) cfg[f.name] = over[f.name] !== undefined ? over[f.name] : f.default;
      return cfg;
    }
    const picker = () => `<div class="card pform picker"><h2>Comece por um modelo</h2><div class="pgrid">${TEMPLATES.map((t) => `<button class="tpl" data-a="tpl" data-id="${t.id}"><b>${esc(t.t)}</b><small>${esc(t.d)}</small></button>`).join("")}</div>
      <div class="row"><button class="btn ghost small" data-a="cancel-pick">Cancelar</button></div></div>`;

    /* ---------- tela: editor ---------- */
    function editorView() {
      const groups = ["Gatilhos", "Lógica", "Ações"].map((g) => `<div class="pal-g"><span class="pal-t">${g}</span>${S.catalog.filter((n) => n.group === g).map((n) =>
        `<button class="pal-i ${ACCENT[g]}" draggable="true" data-add="${n.type}" title="${esc(n.help)}"><span class="en-ic">${ICON[n.type]}</span><span>${esc(n.label)}</span></button>`).join("")}</div>`).join("");
      return `<div class="ed"><div class="ed-top">
          <button class="btn small ghost" data-a="back">← Automações</button>
          <input class="ed-name" id="ed-name" value="${esc(S.cur.name)}" maxlength="120" aria-label="Nome da automação" ${admin() ? "" : "disabled"}>
          <div class="ed-tabs"><button data-t="editor" class="${S.tab === "editor" ? "on" : ""}">Editor</button><button data-t="runs" class="${S.tab === "runs" ? "on" : ""}">Execuções</button></div>
          <label class="sw"><input type="checkbox" id="ed-active" ${S.cur.enabled ? "checked" : ""} ${admin() ? "" : "disabled"}><i></i><span>Ativa</span></label>
          ${tester() ? '<button class="btn small ghost" data-a="test" title="Roda o fluxo agora e envia os avisos de verdade (marcados com [Teste])">▶ Testar</button>' : ""}
          ${admin() ? `<button class="btn small" data-a="save" id="ed-save">Salvar</button>` : ""}
        </div>
        <div class="ed-body" ${S.tab === "runs" ? "hidden" : ""}><aside class="ed-pal"><p class="pal-h">Arraste para o canvas ou clique para adicionar</p>${groups}</aside>
          <div class="ed-canvas" id="ed-canvas" tabindex="0"><div class="ed-world" id="ed-world"><svg class="ed-edges" id="ed-edges"></svg></div>
            <div class="ed-empty" id="ed-empty" hidden><b>Canvas vazio</b><span>Adicione um <i>gatilho</i> (à esquerda) para começar.</span></div>
            <div class="ed-zoom"><button data-z="in" title="Aproximar">${PLUS}</button><button data-z="out" title="Afastar">${MINUS}</button><button data-z="fit" title="Ajustar à tela">${FIT}</button></div></div>
          <aside class="ed-insp" id="ed-insp"></aside></div>
        <div class="ed-runs card" ${S.tab === "runs" ? "" : "hidden"} id="ed-runs"></div></div>`;
    }

    /* geometria */
    const outs = (n) => S.byType[n.type].outputs;
    const outY = (n, i) => (outs(n).length === 1 ? 31 : HEAD + 14 + i * GAP);   // 1 saída: no meio do cabeçalho; várias: uma linha por saída, abaixo
    const nodeH = (n) => (outs(n).length === 1 ? HEAD : HEAD + outs(n).length * GAP + 4);
    const outPos = (n, i) => [n.x + W, n.y + outY(n, i)];
    const inPos = (n) => [n.x, n.y + 31];
    const byId = (id) => S.cur.nodes.find((n) => n.id === id);

    function nodeHtml(n) {
      const t = S.byType[n.type], acc = ACCENT[t.group], o = outs(n);
      return `<div class="enode ${acc}" data-id="${n.id}" style="left:${n.x}px;top:${n.y}px;width:${W}px;height:${nodeH(n)}px">
        <div class="en-head"><span class="en-ic">${ICON[n.type]}</span><div><b>${esc(t.label)}</b><small class="en-sum">${esc(summary(n))}</small></div></div>
        ${t.inputs ? '<span class="en-port in" data-in="1"></span>' : ""}
        ${o.map((p, i) => `<span class="en-port out" data-out="${p.port}" style="top:${outY(n, i) - 11}px"></span>${o.length > 1 ? `<span class="en-ol" style="top:${outY(n, i) - 8}px">${esc(p.label)}</span>` : ""}`).join("")}
        <span class="en-badge" hidden></span></div>`;
    }
    function drawNodes() {
      const world = el.world; world.querySelectorAll(".enode").forEach((x) => x.remove());
      world.insertAdjacentHTML("beforeend", S.cur.nodes.map(nodeHtml).join(""));
      el.empty.hidden = S.cur.nodes.length > 0;
      markSel(); badges(); drawEdges();
    }
    function drawEdges(temp) {
      const svg = el.edges; svg.innerHTML = "";
      const path = (x1, y1, x2, y2) => { const dx = Math.max(60, Math.abs(x2 - x1) / 2); return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`; };
      S.cur.edges.forEach((e, i) => {
        const a = byId(e.source), b = byId(e.target); if (!a || !b) return;
        const [x1, y1] = outPos(a, outs(a).findIndex((p) => p.port === e.port)), [x2, y2] = inPos(b), d = path(x1, y1, x2, y2);
        const sel = S.sel?.kind === "edge" && S.sel.id === i;
        svg.insertAdjacentHTML("beforeend", `<path class="ed-hit" data-edge="${i}" d="${d}"/><path class="ed-edge ${ACCENT[S.byType[a.type].group]}${sel ? " sel" : ""}" d="${d}"/>`);
      });
      if (temp) svg.insertAdjacentHTML("beforeend", `<path class="ed-edge temp" d="${path(...temp)}"/>`);
    }
    function markSel() { el.world.querySelectorAll(".enode").forEach((x) => x.classList.toggle("sel", S.sel?.kind === "node" && S.sel.id === x.dataset.id)); }
    function badges() {
      el.world.querySelectorAll(".enode").forEach((x) => {
        const n = byId(x.dataset.id), b = x.querySelector(".en-badge"), r = S.results[n.id], st = S.states[n.id];
        let text = "", cls = "";
        if (r) { text = r.ok ? "✓ " + r.detail : "✗ " + r.detail; cls = r.ok ? "ok" : "bad"; }
        else if (n.type.startsWith("trigger.") && st) {
          if (st.error) { text = "⚠ " + st.error; cls = "bad"; }
          else if (st.state === "firing") { text = `● disparado · valor ${st.value}`; cls = "bad"; }
          else if (st.value !== undefined) { text = `● ok · valor ${st.value}`; cls = "ok"; }
        }
        b.hidden = !text; b.textContent = text; b.title = text; b.className = "en-badge " + cls;
      });
    }
    const applyView = () => { const v = S.view; el.world.style.transform = `translate(${v.x}px,${v.y}px) scale(${v.k})`;
      el.canvas.style.backgroundSize = `${24 * v.k}px ${24 * v.k}px`; el.canvas.style.backgroundPosition = `${v.x}px ${v.y}px`; };
    const toWorld = (cx, cy) => { const r = el.canvas.getBoundingClientRect(); return [(cx - r.left - S.view.x) / S.view.k, (cy - r.top - S.view.y) / S.view.k]; };
    const dirty = () => { S.cur.dirty = true; const b = $("#ed-save"); if (b) b.textContent = "Salvar •"; };
    const $ = (s, r = root) => r.querySelector(s);

    /* ---------- inspetor ---------- */
    function inspector() {
      const box = el.insp, sel = S.sel;
      if (sel?.kind === "edge") {
        const e = S.cur.edges[sel.id], a = byId(e.source), b = byId(e.target);
        box.innerHTML = `<h3>Ligação</h3><p class="mono hint">${esc(S.byType[a.type].label)} (${esc(outs(a).find((p) => p.port === e.port).label)}) → ${esc(S.byType[b.type].label)}</p>${admin() ? '<button class="btn small ghost danger" data-a="del-edge">Remover ligação</button>' : ""}`;
        return;
      }
      if (sel?.kind !== "node") {
        box.innerHTML = `<h3>Como montar</h3><ol class="steps2"><li>Adicione um <b>gatilho</b> (o que observar).</li><li>Puxe a bolinha da direita até a esquerda de outra etapa para ligá-las.</li><li>Termine com uma <b>ação</b> (Enviar aviso).</li><li>Clique em <b>Salvar</b> e <b>Testar</b>.</li></ol>
          <p class="mono hint">Dica: arraste o fundo para mover, <b>Ctrl/⌘ + rolagem</b> para zoom, <b>Delete</b> para apagar o selecionado.</p>${S.dests.length ? "" : '<p class="perr">Você ainda não tem destino. Conecte um canal em <a href="#integrations">Integrações</a> para poder enviar avisos.</p>'}`;
        return;
      }
      const n = byId(sel.id), t = S.byType[n.type];
      const field = (f) => {
        const v = n.config[f.name], id = `f_${f.name}`;
        let input;
        if (f.type === "select") input = `<select id="${id}" data-f="${f.name}">${f.options.map((o) => `<option value="${esc(o[0])}" ${o[0] === v ? "selected" : ""}>${esc(o[1])}</option>`).join("")}</select>`;
        else if (f.type === "service") { const list = [...new Set([...(api.services() || []), ...(v ? [v] : [])])].sort(); input = `<select id="${id}" data-f="${f.name}"><option value="">Todos os serviços</option>${list.map((s) => `<option ${s === v ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>`; }
        else if (f.type === "destination") input = S.dests.length ? `<select id="${id}" data-f="${f.name}" data-num="1"><option value="">Escolha…</option>${S.dests.map((d) => `<option value="${d.id}" ${d.id === v ? "selected" : ""}>${esc(d.name)} (${esc(d.connection)})</option>`).join("")}</select>` : '<span class="perr">Nenhum destino: conecte um canal em Integrações.</span>';
        else if (f.type === "textarea") input = `<textarea id="${id}" data-f="${f.name}" rows="4" maxlength="${f.max || 800}">${esc(v ?? "")}</textarea>`;
        else if (f.type === "number") input = `<input id="${id}" data-f="${f.name}" data-num="1" type="number" min="${f.min}" max="${f.max}" value="${esc(v ?? "")}">`;
        else input = `<input id="${id}" data-f="${f.name}" value="${esc(v ?? "")}" maxlength="${f.max || 120}">`;
        return `<label>${esc(f.label)}${f.help ? ` <small>(${esc(f.help)})</small>` : ""}${input}</label>`;
      };
      const chips = n.type === "action.notify" ? `<div class="chips"><span class="mono hint">Variáveis (passe o mouse para ver o que trazem; clique para inserir):</span>${S.variables.map((v) => `<button type="button" class="vchip" data-var="${v}" aria-label="${esc(VAR_HELP[v]?.[0] || v)}">{{${v}}}</button>`).join("")}</div>
        <div class="preview"><span class="mono hint">Prévia com valores de exemplo</span><b id="pv-title"></b><p id="pv-text"></p></div>` : "";
      box.innerHTML = `<h3><span class="en-ic ${ACCENT[t.group]}">${ICON[n.type]}</span> ${esc(t.label)}</h3><p class="mono hint">${esc(t.help)}</p>
        <form id="insp-form" onsubmit="return false">${t.fields.map(field).join("")}</form>${chips}
        ${admin() ? '<button class="btn small ghost danger" data-a="del-node">Remover etapa</button>' : ""}`;
      box.querySelectorAll("[data-f]").forEach((inp) => {
        const upd = () => { let v = inp.value; if (inp.dataset.num) v = v === "" ? null : Number(v); n.config[inp.dataset.f] = v; dirty();
          const s = el.world.querySelector(`.enode[data-id="${n.id}"] .en-sum`); if (s) s.textContent = summary(n); };
        inp.addEventListener("input", upd); inp.addEventListener("change", upd); inp.addEventListener("focus", () => { S.lastField = inp; });
        if (!admin()) inp.disabled = true;
      });
      const preview = () => { const t = $("#pv-title", box); if (!t) return; window.__svc = n.config.service || api.service(); window.__auto = S.cur.name;
        t.textContent = sample(n.config.title || ""); $("#pv-text", box).textContent = sample(n.config.message || ""); };
      box.querySelectorAll("[data-f]").forEach((inp) => inp.addEventListener("input", preview)); preview();
      box.querySelectorAll("[data-var]").forEach((b) => {
        const show = () => tip(b, VAR_HELP[b.dataset.var]); b.addEventListener("mouseenter", show); b.addEventListener("focus", show);
        b.addEventListener("mouseleave", hideTip); b.addEventListener("blur", hideTip);
      });
      box.querySelectorAll("[data-var]").forEach((b) => b.addEventListener("click", () => {
        const inp = S.lastField && box.contains(S.lastField) && ["INPUT", "TEXTAREA"].includes(S.lastField.tagName) ? S.lastField : box.querySelector("textarea");
        const tag = `{{${b.dataset.var}}}`, i = inp.selectionStart ?? inp.value.length;
        inp.value = inp.value.slice(0, i) + tag + inp.value.slice(inp.selectionEnd ?? i); inp.focus(); inp.setSelectionRange(i + tag.length, i + tag.length); inp.dispatchEvent(new Event("input"));
      }));
    }
    /* balão flutuante (posição fixa: não é cortado pelo painel) */
    let tipEl = null;
    function tip(anchor, info) {
      if (!info) return; hideTip();
      tipEl = document.createElement("div"); tipEl.className = "vtip";
      tipEl.innerHTML = `${esc(info[0])}<br><span>Exemplo: <code>${esc(info[1]())}</code></span>`; document.body.appendChild(tipEl);
      const r = anchor.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
      tipEl.style.left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8) + "px";
      tipEl.style.top = (r.top - h - 10 < 8 ? r.bottom + 10 : r.top - h - 10) + "px";
    }
    function hideTip() { if (tipEl) { tipEl.remove(); tipEl = null; } }
    function select(sel) { hideTip(); S.sel = sel; markSel(); drawEdges(); inspector(); }

    /* ---------- edição ---------- */
    function addNode(type, at) {
      if (!admin()) return toast("Só administradores editam automações.", true);
      const [cx, cy] = at || toWorld(el.canvas.getBoundingClientRect().left + el.canvas.clientWidth / 2, el.canvas.getBoundingClientRect().top + el.canvas.clientHeight / 2);
      const n = { id: uid(), type, x: Math.round((cx - W / 2) / 10) * 10, y: Math.round((cy - 31) / 10) * 10, config: defaults(type, type.startsWith("trigger.") ? { service: api.service() } : {}) };
      const dest = S.dests[0]; if (type === "action.notify" && dest) n.config.destination_id = dest.id;
      S.cur.nodes.push(n); dirty(); drawNodes(); select({ kind: "node", id: n.id });
    }
    function wouldCycle(from, to) { // há caminho de `to` até `from`?
      const seen = new Set(), go = (id) => { if (id === from) return true; if (seen.has(id)) return false; seen.add(id); return S.cur.edges.filter((e) => e.source === id).some((e) => go(e.target)); };
      return go(to);
    }
    function connect(src, port, dst) {
      const a = byId(src), b = byId(dst);
      if (!a || !b || src === dst) return;
      if (!S.byType[b.type].inputs) return toast("Gatilhos só começam o fluxo: ligue em uma condição ou ação.", true);
      if (S.cur.edges.some((e) => e.source === src && e.port === port && e.target === dst)) return;
      if (wouldCycle(src, dst)) return toast("Essa ligação criaria um ciclo (o fluxo voltaria para uma etapa anterior).", true);
      S.cur.edges.push({ source: src, port, target: dst }); dirty(); drawEdges();
    }
    function removeSel() {
      if (!admin() || !S.sel) return;
      if (S.sel.kind === "edge") S.cur.edges.splice(S.sel.id, 1);
      else { S.cur.nodes = S.cur.nodes.filter((n) => n.id !== S.sel.id); S.cur.edges = S.cur.edges.filter((e) => e.source !== S.sel.id && e.target !== S.sel.id); }
      S.sel = null; dirty(); drawNodes(); inspector();
    }
    function fit() {
      const ns = S.cur.nodes, c = el.canvas; if (!ns.length) { S.view = { x: 40, y: 40, k: 1 }; return applyView(); }
      const x1 = Math.min(...ns.map((n) => n.x)), y1 = Math.min(...ns.map((n) => n.y)), x2 = Math.max(...ns.map((n) => n.x + W)), y2 = Math.max(...ns.map((n) => n.y + nodeH(n)));
      const k = Math.min(1.1, (c.clientWidth - 80) / (x2 - x1), (c.clientHeight - 80) / (y2 - y1));
      S.view = { k, x: (c.clientWidth - (x2 - x1) * k) / 2 - x1 * k, y: (c.clientHeight - (y2 - y1) * k) / 2 - y1 * k }; applyView();
    }
    function zoom(f, cx, cy) {
      const r = el.canvas.getBoundingClientRect(), px = (cx ?? r.left + r.width / 2) - r.left, py = (cy ?? r.top + r.height / 2) - r.top, k = Math.min(2, Math.max(0.3, S.view.k * f));
      S.view.x = px - ((px - S.view.x) / S.view.k) * k; S.view.y = py - ((py - S.view.y) / S.view.k) * k; S.view.k = k; applyView();
    }

    function wireCanvas() {
      const c = el.canvas; let drag = null;
      c.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        const port = e.target.closest(".en-port.out"), node = e.target.closest(".enode"), hit = e.target.closest(".ed-hit");
        c.focus({ preventScroll: true });
        if (port && admin()) { const n = byId(node.dataset.id), i = outs(n).findIndex((p) => p.port === port.dataset.out); drag = { kind: "link", src: n.id, port: port.dataset.out, from: outPos(n, i) }; c.classList.add("linking"); }
        else if (node) { const n = byId(node.dataset.id); select({ kind: "node", id: n.id }); const [wx, wy] = toWorld(e.clientX, e.clientY); drag = { kind: "node", n, dx: wx - n.x, dy: wy - n.y, moved: false }; }
        else if (hit) { select({ kind: "edge", id: +hit.dataset.edge }); return; }
        else { drag = { kind: "pan", sx: e.clientX, sy: e.clientY, vx: S.view.x, vy: S.view.y, moved: false }; }
        c.setPointerCapture(e.pointerId); e.preventDefault();
      });
      c.addEventListener("pointermove", (e) => {
        if (!drag) return;
        if (drag.kind === "pan") { if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 3) drag.moved = true; S.view.x = drag.vx + e.clientX - drag.sx; S.view.y = drag.vy + e.clientY - drag.sy; applyView(); }
        else if (drag.kind === "node") {
          if (!admin()) return; const [wx, wy] = toWorld(e.clientX, e.clientY);
          drag.n.x = Math.round((wx - drag.dx) / 10) * 10; drag.n.y = Math.round((wy - drag.dy) / 10) * 10; drag.moved = true;
          const d = el.world.querySelector(`.enode[data-id="${drag.n.id}"]`); d.style.left = drag.n.x + "px"; d.style.top = drag.n.y + "px"; drawEdges();
        } else if (drag.kind === "link") {
          const [wx, wy] = toWorld(e.clientX, e.clientY); drawEdges([...drag.from, wx, wy]);
          const over = document.elementFromPoint(e.clientX, e.clientY)?.closest(".enode"); el.world.querySelectorAll(".enode").forEach((x) => x.classList.toggle("target", x === over && x.dataset.id !== drag.src && !!S.byType[byId(x.dataset.id).type].inputs));
        }
      });
      const end = (e) => {
        if (!drag) return;
        if (drag.kind === "link") { const over = document.elementFromPoint(e.clientX, e.clientY)?.closest(".enode"); if (over) connect(drag.src, drag.port, over.dataset.id); drawEdges(); c.classList.remove("linking"); el.world.querySelectorAll(".target").forEach((x) => x.classList.remove("target")); }
        else if (drag.kind === "node" && drag.moved) dirty();
        else if (drag.kind === "pan" && !drag.moved) select(null);
        drag = null;
      };
      c.addEventListener("pointerup", end); c.addEventListener("pointercancel", end);
      c.addEventListener("wheel", (e) => { e.preventDefault(); if (e.ctrlKey || e.metaKey) zoom(Math.exp(-e.deltaY * 0.01), e.clientX, e.clientY); else { S.view.x -= e.deltaX; S.view.y -= e.deltaY; applyView(); } }, { passive: false });
      c.addEventListener("keydown", (e) => { if ((e.key === "Delete" || e.key === "Backspace") && !/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) { e.preventDefault(); removeSel(); } });
      c.addEventListener("dragover", (e) => e.preventDefault());
      c.addEventListener("drop", (e) => { e.preventDefault(); const t = e.dataTransfer.getData("text/plain"); if (S.byType[t]) { const [wx, wy] = toWorld(e.clientX, e.clientY); addNode(t, [wx, wy]); } });
      root.querySelectorAll("[data-z]").forEach((b) => b.addEventListener("click", () => (b.dataset.z === "fit" ? fit() : zoom(b.dataset.z === "in" ? 1.2 : 1 / 1.2))));
      root.querySelectorAll("[data-add]").forEach((b) => { b.addEventListener("click", () => addNode(b.dataset.add)); b.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", b.dataset.add)); });
    }

    /* ---------- salvar, testar, abrir ---------- */
    const payload = () => ({ name: S.cur.name.trim() || "Nova automação", enabled: S.cur.enabled,
      graph: { nodes: S.cur.nodes.map(({ id, type, x, y, config }) => ({ id, type, x, y, config })), edges: S.cur.edges.map(({ source, port, target }) => ({ source, port, target })) } });
    async function save(quiet) {
      try {
        const saved = S.cur.id ? await papi(`automations/${S.cur.id}`, { method: "PUT", body: payload() }) : await papi("automations", { method: "POST", body: payload() });
        S.cur.id = saved.id; S.cur.dirty = false; const b = $("#ed-save"); if (b) b.textContent = "Salvar";
        S.list = await papi("automations"); if (!quiet) toast("Automação salva."); return true;
      } catch (e) { toast(e.message, true); return false; }
    }
    async function test(btn) {
      if (S.cur.dirty || !S.cur.id) { if (!admin()) return toast("Há alterações não salvas.", true); if (!(await save(true))) return; }
      btn.disabled = true; btn.textContent = "Testando…";
      try {
        const r = await papi(`automations/${S.cur.id}/test`, { method: "POST" });
        S.results = Object.fromEntries(r.steps.map((s) => [s.node, s])); badges();
        const sent = r.steps.filter((s) => s.label === "Enviar aviso"), okc = sent.filter((s) => s.ok).length;
        toast(sent.length ? `Teste: ${okc} de ${sent.length} aviso(s) enviado(s). Confira no destino.` : "Teste: o fluxo não chegou a nenhuma ação (confira as ligações).", sent.length ? okc < sent.length : true);
        setTimeout(() => { S.results = {}; if (S.mode === "editor") badges(); }, 12000);
        S.runs = await papi("automations/runs?limit=30");
      } catch (e) { toast(e.message, true); }
      btn.disabled = false; btn.textContent = "▶ Testar";
    }
    function openAuto(a) {
      S.cur = { id: a.id, name: a.name, enabled: a.enabled, nodes: JSON.parse(JSON.stringify(a.graph.nodes)), edges: JSON.parse(JSON.stringify(a.graph.edges)), dirty: false };
      S.states = a.state || {}; S.sel = null; S.tab = "editor"; S.results = {}; S.mode = "editor"; paint(); fit();
      clearInterval(poll); poll = setInterval(async () => { if (!root.isConnected || S.mode !== "editor" || !S.cur.id) return clearInterval(poll);
        try { const l = await papi("automations"); S.list = l; S.states = l.find((x) => x.id === S.cur.id)?.state || {}; badges(); } catch { /* sem rede: tenta no próximo ciclo */ } }, 8000);
    }

    /* ---------- pintura e eventos ---------- */
    function paint() {
      if (S.mode === "list") { clearInterval(poll); root.innerHTML = listView(); }
      else {
        root.innerHTML = editorView();
        el = { canvas: $("#ed-canvas"), world: $("#ed-world"), edges: $("#ed-edges"), insp: $("#ed-insp"), empty: $("#ed-empty") };
        wireCanvas(); drawNodes(); inspector(); applyView();
        if (S.tab === "runs") loadRuns();
        $("#ed-name").addEventListener("input", (e) => { S.cur.name = e.target.value; dirty(); });
        $("#ed-active").addEventListener("change", (e) => { S.cur.enabled = e.target.checked; dirty(); });
        root.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => { S.tab = b.dataset.t; const keep = S.view; paint(); S.view = keep; applyView(); }));
      }
    }
    async function loadRuns() { const box = $("#ed-runs"); if (!S.cur.id) { box.innerHTML = '<p class="mono hint">Salve a automação para ver as execuções.</p>'; return; }
      try { box.innerHTML = `<div class="phead"><h2>Execuções</h2></div>${runsTable(await papi(`automations/runs?automation_id=${S.cur.id}&limit=50`), false)}`; } catch (e) { box.textContent = e.message; } }

    root.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-a]"), card = e.target.closest("[data-open]");
      if (!b && card && S.mode === "list") return openAuto(S.list.find((x) => x.id === +card.dataset.open));
      if (!b) return;
      const act = b.dataset.a, id = +b.dataset.id;
      const rerender = async () => { await load(); paint(); };
      if (act === "new") { S.pick = true; paint(); }
      else if (act === "cancel-pick") { S.pick = false; paint(); }
      else if (act === "tpl") { S.pick = false; const t = TEMPLATES.find((x) => x.id === b.dataset.id).build(api.service(), S.dests[0]?.id ?? null);
        S.cur = { id: null, name: t.name, enabled: true, nodes: t.nodes, edges: t.edges, dirty: true }; S.sel = null; S.tab = "editor"; S.results = {}; S.states = {}; S.mode = "editor"; paint(); fit(); }
      else if (act === "open") openAuto(S.list.find((x) => x.id === id));
      else if (act === "back") { if (S.cur?.dirty && !confirm("Há alterações não salvas. Sair mesmo assim?")) return; S.mode = "list"; await rerender(); }
      else if (act === "save") await save();
      else if (act === "test") test(b);
      else if (act === "del-node" || act === "del-edge") removeSel();
      else if (act === "toggle") { const a = S.list.find((x) => x.id === id); try { await papi(`automations/${id}`, { method: "PUT", body: { name: a.name, enabled: !a.enabled, graph: a.graph } }); toast(a.enabled ? "Automação pausada." : "Automação ativada."); } catch (er) { toast(er.message, true); } await rerender(); }
      else if (act === "del") { const a = S.list.find((x) => x.id === id); if (!confirm(`Remover “${a.name}”? As execuções antigas ficam no histórico.`)) return; try { await papi(`automations/${id}`, { method: "DELETE" }); toast("Automação removida."); } catch (er) { toast(er.message, true); } await rerender(); }
    });

    (async () => { try { await load(); S.mode = "list"; paint(); } catch (e) { root.innerHTML = `<div class="alert">${esc(e.message)}</div>`; } })();
  }
  window.ObsAuto = { mount };
})();
