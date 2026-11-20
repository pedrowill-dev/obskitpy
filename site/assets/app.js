/* obskitpy docs — JS puro, sem dependências. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const ROOT = document.documentElement.dataset.root || "./";
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  /* ---------- realce de sintaxe (mínimo, por regex) ---------- */
  const RULES = {
    python: [
      ["c", /#[^\n]*/],
      ["s", /[rbfuRBFU]{0,2}(?:"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')/],
      ["n", /@[A-Za-z_][\w.]*/],
      ["k", /\b(?:import|from|as|def|class|return|if|elif|else|for|while|try|except|finally|with|raise|in|not|and|or|is|lambda|yield|pass|break|continue|None|True|False|async|await)\b/],
      ["f", /\b[A-Za-z_]\w*(?=\()/],
      ["fl", /\b\d[\d_]*(?:\.\d+)?\b/],
    ],
    bash: [
      ["c", /(?:^|\s)#[^\n]*/],
      ["s", /"(?:\\.|[^"\\])*"|'[^']*'/],
      ["v", /\$\{?[A-Za-z_]\w*\}?/],
      ["fl", /(?<![\w-])--?[A-Za-z][\w-]*/],
      ["f", /\b(?:cd|export|git|docker|compose|uv|uvx|pip|python3?|obskitpy|curl|pytest|colima|sudo|mkdir|source|ls|echo)\b/],
    ],
    yaml: [
      ["c", /#[^\n]*/],
      ["f", /^[ \t-]*[A-Za-z_][\w.-]*(?=:)/],
      ["s", /"(?:\\.|[^"\\\n])*"|'[^'\n]*'/],
      ["k", /\b(?:true|false|null|yes|no)\b/],
      ["fl", /\b\d+(?:\.\d+)?\b/],
    ],
    json: [
      ["f", /"(?:\\.|[^"\\\n])*"(?=\s*:)/],
      ["s", /"(?:\\.|[^"\\\n])*"/],
      ["k", /\b(?:true|false|null)\b/],
      ["fl", /-?\b\d+(?:\.\d+)?\b/],
    ],
    toml: [
      ["c", /#[^\n]*/],
      ["f", /^\[[^\]\n]+\]/],
      ["s", /"(?:\\.|[^"\\\n])*"/],
      ["fl", /\b\d+(?:\.\d+)?\b/],
    ],
  };
  RULES.sh = RULES.shell = RULES.console = RULES.bash;
  RULES.yml = RULES.yaml;
  RULES.ini = RULES.env = RULES.toml;
  const COMPILED = {};
  function highlight(code, lang) {
    const rules = RULES[lang];
    if (!rules) return esc(code);
    const re = COMPILED[lang] || (COMPILED[lang] = new RegExp(rules.map((r) => `(${r[1].source})`).join("|"), "gm"));
    let out = "", last = 0, m;
    re.lastIndex = 0;
    while ((m = re.exec(code))) {
      if (m[0] === "") { re.lastIndex++; continue; }
      const idx = m.slice(1).findIndex((g) => g !== undefined);
      out += esc(code.slice(last, m.index)) + `<span class="tk-${rules[idx][0]}">${esc(m[0])}</span>`;
      last = m.index + m[0].length;
    }
    return out + esc(code.slice(last));
  }

  function enhanceCode(root = document) {
    $$("pre > code", root).forEach((code) => {
      const pre = code.parentElement;
      if (pre.closest(".code-block")) return;
      const lang = ((code.className.match(/language-(\w+)/) || [])[1] || pre.dataset.lang || "").toLowerCase();
      const raw = code.textContent.replace(/\n$/, "");
      code.innerHTML = highlight(raw, lang);
      const box = document.createElement("div");
      box.className = "code-block";
      const label = { bash: "terminal", sh: "terminal", shell: "terminal", env: ".env", yml: "yaml" }[lang] || lang || "texto";
      box.innerHTML = `<div class="code-head"><span>${label}</span><button class="copy-btn" type="button" aria-label="Copiar código">Copiar</button></div>`;
      pre.replaceWith(box);
      box.appendChild(pre);
      $(".copy-btn", box).addEventListener("click", async (e) => {
        const btn = e.currentTarget;
        const text = (lang === "bash" || lang === "sh") ? raw.replace(/^\$ /gm, "") : raw;
        try { await navigator.clipboard.writeText(text); } catch { return; }
        btn.textContent = "Copiado ✓"; btn.classList.add("done");
        setTimeout(() => { btn.textContent = "Copiar"; btn.classList.remove("done"); }, 1600);
      });
    });
  }
  enhanceCode();

  /* ---------- abas ---------- */
  $$(".tabs").forEach((tabs) => {
    const btns = $$(".tab-list button", tabs), panels = $$(".tab-panel", tabs);
    btns.forEach((b, i) => b.addEventListener("click", () => {
      btns.forEach((x, j) => { x.setAttribute("aria-selected", j === i); panels[j].hidden = j !== i; });
      try { localStorage.setItem("obskitpy-tab", b.dataset.tab); } catch {}
    }));
    try {
      const saved = localStorage.getItem("obskitpy-tab");
      const idx = btns.findIndex((b) => b.dataset.tab === saved);
      if (idx > 0) btns[idx].click();
    } catch {}
  });

  /* ---------- âncoras nos títulos ---------- */
  $$(".prose h2[id], .prose h3[id]").forEach((h) => {
    const a = document.createElement("a");
    a.className = "anchor"; a.href = "#" + h.id; a.textContent = "#"; a.setAttribute("aria-label", "Link para esta seção");
    h.appendChild(a);
  });

  /* ---------- índice lateral (scrollspy) ---------- */
  const tocLinks = $$(".toc a");
  if (tocLinks.length) {
    const map = new Map(tocLinks.map((a) => [a.getAttribute("href").slice(1), a]));
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) {
          tocLinks.forEach((a) => a.classList.remove("active"));
          map.get(en.target.id)?.classList.add("active");
        }
      });
    }, { rootMargin: "-80px 0px -70% 0px" });
    $$(".prose h2[id], .prose h3[id]").forEach((h) => io.observe(h));
  }

  /* ---------- menu mobile ---------- */
  $(".menu-btn")?.addEventListener("click", () => document.body.classList.toggle("nav-open"));
  $$(".sidebar a").forEach((a) => a.addEventListener("click", () => document.body.classList.remove("nav-open")));

  /* ---------- lightbox ---------- */
  const lb = document.createElement("div");
  lb.className = "lightbox"; lb.innerHTML = "<img alt=''>";
  document.body.appendChild(lb);
  lb.addEventListener("click", () => lb.classList.remove("open"));
  $$(".prose img, .shot img").forEach((img) => {
    if (img.src.includes("logo")) return;
    img.addEventListener("click", () => { $("img", lb).src = img.src; $("img", lb).alt = img.alt; lb.classList.add("open"); });
  });

  /* ---------- busca ---------- */
  const modal = $(".search-modal");
  if (modal) {
    const input = $("input", modal), results = $(".search-results", modal);
    let index = null, sel = 0, hits = [];
    const load = async () => {
      if (index) return;
      try { index = await (await fetch(ROOT + "search.json")).json(); } catch { index = []; }
    };
    const open = async () => { modal.classList.add("open"); input.value = ""; render(""); input.focus(); await load(); };
    const close = () => modal.classList.remove("open");
    const norm = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    function render(q) {
      q = norm(q.trim());
      if (!q) { results.innerHTML = '<div class="search-empty">Digite para buscar na documentação…</div>'; hits = []; return; }
      const terms = q.split(/\s+/);
      hits = (index || []).map((d) => {
        const t = norm(d.t), h = norm(d.h || ""), x = norm(d.x);
        let score = 0;
        for (const w of terms) {
          if (t.includes(w)) score += 6; if (h.includes(w)) score += 4; if (x.includes(w)) score += 1;
          if (!t.includes(w) && !h.includes(w) && !x.includes(w)) return null;
        }
        return { d, score, pos: x.indexOf(terms[0]) };
      }).filter(Boolean).sort((a, b) => b.score - a.score).slice(0, 8);
      sel = 0;
      if (!hits.length) { results.innerHTML = `<div class="search-empty">Nada encontrado para “${esc(q)}”.</div>`; return; }
      const mark = (s) => terms.reduce((acc, w) => acc.replace(new RegExp("(" + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi"), "<mark>$1</mark>"), esc(s));
      results.innerHTML = hits.map(({ d, pos }, i) => {
        const start = Math.max(0, pos - 40), snip = d.x.slice(start, start + 140);
        return `<a href="${ROOT}${d.u}" class="${i === 0 ? "sel" : ""}"><b>${esc(d.h || d.t)}</b><small>${esc(d.t)}</small><span class="snip">${mark((start ? "…" : "") + snip)}…</span></a>`;
      }).join("");
    }
    input.addEventListener("input", () => render(input.value));
    input.addEventListener("keydown", (e) => {
      const links = $$("a", results);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!links.length) return;
        links[sel]?.classList.remove("sel");
        sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
        links[sel].classList.add("sel"); links[sel].scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter" && links[sel]) { location.href = links[sel].href; }
    });
    modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
    $$(".search-btn").forEach((b) => b.addEventListener("click", open));
    document.addEventListener("keydown", (e) => {
      const typing = /input|textarea/i.test(document.activeElement?.tagName || "");
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) { e.preventDefault(); open(); }
      else if (e.key === "Escape") { close(); lb.classList.remove("open"); }
    });
  }

  /* ---------- terminal animado (landing) ---------- */
  const term = $("#term");
  if (term) {
    const lines = JSON.parse($("#term-script").textContent);
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const cursor = '<span class="cursor"></span>';
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const render = (done, typing) => { term.innerHTML = done.join("\n") + (done.length ? "\n" : "") + typing + cursor; };
    (async () => {
      const done = [];
      for (;;) {
        done.length = 0;
        for (const l of lines) {
          if (l.cmd) {
            if (reduce) { done.push(`<span class="p">$</span> ${esc(l.cmd)}`); render(done, ""); continue; }
            let t = "";
            for (const ch of l.cmd) { t += ch; render(done, `<span class="p">$</span> ${esc(t)}`); await sleep(24 + Math.random() * 30); }
            done.push(`<span class="p">$</span> ${esc(l.cmd)}`); render(done, ""); await sleep(420);
          } else {
            done.push(`<span class="${l.cls || ""}">${esc(l.out)}</span>`); render(done, ""); if (!reduce) await sleep(l.wait || 170);
          }
        }
        if (reduce) return;
        await sleep(5200);
      }
    })();
  }
})();

/* ---------- diagrama interativo "Como funciona" ---------- */
(() => {
  const flow = document.getElementById("flow");
  if (!flow) return;
  const $ = (s, r = flow) => r.querySelector(s), $$ = (s, r = flow) => [...r.querySelectorAll(s)];
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const canHover = matchMedia("(hover: hover)").matches;

  // O que cada peça significa. `tip` = balão curto no mouse; `text` = explicação completa; `code` = exemplo (opcional).
  const INFO = {
    rpa: { t: "RPA", k: "Automações", tip: "Robôs que clicam, navegam e preenchem sistemas.", text: "Robôs que automatizam tarefas repetitivas (Selenium, Playwright, <code>requests</code>…). Basta chamar <code>setup(\"meu-robo\")</code> uma vez: o <code>logging</code> que você já usa passa a aparecer no painel.", code: 'setup("meu-robo")' },
    etl: { t: "ETL / ELT", k: "Automações", tip: "Pipelines que extraem, transformam e carregam dados.", text: "Pipelines de dados. Cada etapa pode virar uma <em>span</em> com <code>@traced</code>, e você vê quanto tempo cada uma levou e onde falhou.", code: "@traced()\ndef transformar(df): ..." },
    integ: { t: "Integrações", k: "Automações", tip: "Chamadas a APIs, SAP, ERP e outros sistemas.", text: "Código que conversa com APIs, SAP, ERP… As chamadas feitas com <code>requests</code> são instrumentadas sozinhas: o tempo e o status de cada uma entram nos traces.", code: "setup(..., instrument_requests=True)" },
    core: { t: "obskitpy — biblioteca + CLI", k: "O coração", tip: "Uma biblioteca para o seu código e uma CLI para a infraestrutura.", text: "A <strong>biblioteca</strong> (<code>import obskitpy</code>) captura o que o robô faz, usando OpenTelemetry. A <strong>CLI</strong> (<code>obskitpy init</code>) sobe a stack e cria o dashboard sozinha, em qualquer projeto Python.", code: "uv add obskitpy && uv run obskitpy init" },
    logs: { t: "Logs estruturados", k: "O que o obskitpy captura", tip: "O logging de sempre, mas com campos pesquisáveis.", text: "Cada <code>logging.info(...)</code> vira um registro com serviço, nível, hora e atributos, pronto para filtrar e buscar." },
    errors: { t: "Execuções e erros", k: "O que o obskitpy captura", tip: "Cada erro com tipo, origem e cadeia de causas.", text: "Quando algo quebra, o erro é guardado com o <strong>tipo</strong> (<code>KeyError</code>), a <strong>origem</strong> (arquivo e linha) e a cadeia de exceções, e agrupado no painel." },
    metrics: { t: "Métricas", k: "O que o obskitpy captura", tip: "Números ao longo do tempo: duração, contagens, status.", text: "Contadores e medidas (tempo de execução, itens processados, status) coletados a cada poucos segundos, para ver tendências e picos." },
    traces: { t: "Traces", k: "O que o obskitpy captura", tip: "O caminho da execução, ponta a ponta.", text: "A execução inteira como uma linha do tempo de etapas (<em>waterfall</em>): você vê o que rodou, em que ordem e quanto cada parte demorou." },
    es: { t: "Elasticsearch", k: "Onde fica", tip: "O banco onde tudo é guardado e pesquisado.", text: "Guarda logs, execuções, métricas e traces em <em>data streams</em>. O OpenTelemetry Collector grava lá a cada segundo, e a busca é rápida mesmo com milhões de registros.", code: "logs · traces · metrics -generic.otel-default" },
    dash: { t: "dash-obskitpy", k: "Como você vê", tip: "Painel leve, ao vivo, com vários projetos.", text: "O painel próprio do obskitpy: abre rápido, tem <strong>Realtime</strong>, período em segundos, minutos ou horas e um seletor para alternar entre vários projetos.", code: "obskitpy status  →  abre o dash" },
    kibana: { t: "Kibana", k: "Como você vê", tip: "Completo: explore a fundo e monte seus painéis.", text: "A ferramenta oficial do Elasticsearch, para quem quer investigar a fundo e montar os próprios painéis. É opcional: escolha com <code>--local kibana</code> ou <code>ambos</code>.", code: "obskitpy init --local ambos" },
  };
  const STEPS = {
    1: { t: "A automação roda", text: "O seu código Python (RPA, ETL, integrações) executa normalmente: nada muda na forma de escrever." },
    2: { t: "O obskitpy coleta", text: "Logs, métricas e traces são capturados e enviados por OpenTelemetry ao Collector, em segundo plano e sem travar o robô." },
    3: { t: "Tudo fica no Elasticsearch", text: "Os dados são gravados e indexados, prontos para consulta e para alertas." },
    4: { t: "Você visualiza", text: "Abra o dash-obskitpy (leve, ao vivo) ou o Kibana (completo) e veja o que o robô fez, em tempo real." },
  };
  // quais passos acendem cada peça
  const STEP_OF = { rpa: 1, etl: 1, integ: 1, core: 2, logs: 2, errors: 2, metrics: 2, traces: 2, es: 3, dash: 4, kibana: 4 };
  const PATHS = [["rpa", "core"], ["etl", "core"], ["integ", "core"], ["core", "es"], ["core", "dash"], ["core", "kibana"]];
  const STEP_PATHS = { 1: [0, 1, 2], 2: [], 3: [3], 4: [4, 5] };

  const el = (k) => $(`[data-k="${k}"]`);
  const svg = $(".flow-svg"), info = $(".flow-info"), tip = $(".flow-tip");
  let paths = [], step = 0, timer = null, resumeT = null, pinned = false;

  // ----- linhas e partículas (calculadas a partir da posição real dos elementos) -----
  function draw() {
    const g = $(".flow-grid"), gr = g.getBoundingClientRect();
    if (getComputedStyle(svg).display === "none") return;
    const pt = (k, side) => { const r = el(k).getBoundingClientRect(); return [(side === "r" ? r.right : r.left) - gr.left, r.top + r.height / 2 - gr.top]; };
    svg.setAttribute("viewBox", `0 0 ${gr.width} ${gr.height}`);
    svg.innerHTML = "";
    paths = PATHS.map(([a, b], i) => {
      const [x1, y1] = pt(a, a === "core" ? "r" : "r"), [x2, y2] = pt(b, "l");
      const sx = a === "core" ? pt("core", "r")[0] : x1, ex = b === "core" ? pt("core", "l")[0] : x2;
      const mx = (sx + ex) / 2, d = `M${sx},${y1} C${mx},${y1} ${mx},${y2} ${ex},${y2}`;
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", d); p.setAttribute("class", "fp"); svg.appendChild(p);
      if (!reduce) for (let n = 0; n < 3; n++) {
        const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        c.setAttribute("r", "3.6"); if (b === "kibana") c.setAttribute("class", "amber");
        const m = document.createElementNS("http://www.w3.org/2000/svg", "animateMotion");
        m.setAttribute("path", d); m.setAttribute("dur", "3.2s"); m.setAttribute("repeatCount", "indefinite"); m.setAttribute("begin", `${-(n * 1.07 + i * 0.3)}s`);
        c.appendChild(m); svg.appendChild(c);
      }
      return p;
    });
    paint();
  }

  // ----- estado visual -----
  let active = { keys: new Set(), paths: new Set() };
  function paint() {
    flow.classList.toggle("hl", active.keys.size > 0);
    $$("[data-k]").forEach((n) => { const on = active.keys.has(n.dataset.k); n.classList.toggle("on", on); n.classList.toggle("rel", false); });
    paths.forEach((p, i) => p.classList.toggle("on", active.paths.has(i)));
  }
  function show(title, kicker, html, code) {
    info.querySelector(".fi-kicker").textContent = kicker; info.querySelector(".fi-title").textContent = title;
    info.querySelector(".fi-text").innerHTML = html;
    const c = info.querySelector(".fi-code"); c.hidden = !code; c.textContent = code || "";
    info.classList.remove("swap"); void info.offsetWidth; info.classList.add("swap");
  }
  function focusKey(k) {
    const i = INFO[k]; if (!i) return;
    const keys = new Set([k]), ps = new Set();
    PATHS.forEach(([a, b], n) => { if (a === k || b === k || k === "core" || ["logs", "errors", "metrics", "traces"].includes(k)) ps.add(n); });
    if (["logs", "errors", "metrics", "traces"].includes(k)) keys.add("core");
    active = { keys, paths: ps }; paint();
    $$("[data-step]").forEach((b) => b.classList.remove("on"));
    show(i.t, i.k, i.text, i.code);
  }
  function focusStep(n) {
    step = n; const s = STEPS[n];
    $$("[data-step]").forEach((b) => b.classList.toggle("on", +b.dataset.step === n));
    const keys = new Set(Object.keys(STEP_OF).filter((k) => STEP_OF[k] === n));
    if (n === 2) { keys.add("core"); }
    active = { keys, paths: new Set(STEP_PATHS[n]) }; paint();
    show(s.t, `Passo ${n} de 4`, s.text);
  }

  // ----- balão ao passar o mouse -----
  function showTip(node) {
    const i = INFO[node.dataset.k]; if (!i || !canHover) return;
    const fr = flow.getBoundingClientRect(), r = node.getBoundingClientRect();
    tip.textContent = i.tip; tip.hidden = false;
    const half = 130, left = Math.min(Math.max(r.left - fr.left + r.width / 2, half), fr.width - half);
    tip.style.left = `${left}px`; tip.style.top = `${r.top - fr.top - 10}px`;
  }

  // ----- reprodução automática dos 4 passos -----
  const DUR = 4200;
  function play() { stop(); if (reduce || pinned) return; flow.dataset.paused = "0"; flow.style.setProperty("--dur", `${DUR}ms`); timer = setInterval(() => focusStep(step % 4 + 1), DUR); }
  function stop() { clearInterval(timer); clearTimeout(resumeT); timer = null; }
  function pause() { stop(); flow.dataset.paused = "1"; }
  function resumeSoon() { clearTimeout(resumeT); if (pinned) return; resumeT = setTimeout(() => { if (!pinned) { focusStep(step % 4 + 1); play(); } }, 2200); }

  $$("[data-k]").forEach((n) => {
    n.addEventListener("mouseenter", () => { pause(); focusKey(n.dataset.k); showTip(n); });
    n.addEventListener("focus", () => { pause(); focusKey(n.dataset.k); showTip(n); });
    n.addEventListener("mouseleave", () => { tip.hidden = true; resumeSoon(); });
    n.addEventListener("blur", () => { tip.hidden = true; });
    n.addEventListener("click", () => { pause(); pinned = true; focusKey(n.dataset.k); });
  });
  $$("[data-step]").forEach((b) => {
    b.addEventListener("click", () => { pinned = true; pause(); focusStep(+b.dataset.step); });
    b.addEventListener("mouseenter", () => { pause(); focusStep(+b.dataset.step); });
    b.addEventListener("mouseleave", () => { pinned = false; resumeSoon(); });
  });
  flow.addEventListener("mouseleave", () => { pinned = false; });
  document.addEventListener("click", (e) => { if (!flow.contains(e.target)) { pinned = false; } });

  // ----- entrada ao rolar a página + liga/desliga a animação fora da tela -----
  $$(".fcol > *, .fchecks, .flow-info, .flow-steps li").forEach((n, i) => n.style.setProperty("--i", i % 6));
  let ready = false;
  new IntersectionObserver(([e]) => {
    if (e.isIntersecting) {
      flow.classList.add("in-view");
      if (!ready) { ready = true; draw(); focusStep(1); }
      if (!pinned && !timer) play();
    } else pause();
  }, { threshold: 0.25 }).observe(flow);
  let rz; addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => ready && draw(), 150); });
  if (document.fonts?.ready) document.fonts.ready.then(() => ready && draw());
})();
