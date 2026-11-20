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
