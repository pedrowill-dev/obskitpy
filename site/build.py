#!/usr/bin/env python3
"""Gera o site estático (HTML/CSS/JS puros) em _site/ a partir de docs/*.md e site/landing.html.

    uv run --with markdown python site/build.py

Sem framework: este script só converte Markdown em HTML e injeta o layout (cabeçalho, menu, índice, busca).
"""
import html
import json
import re
import shutil
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC, DOCS, OUT = ROOT / "site", ROOT / "docs", ROOT / "_site"
REPO = "https://github.com/pedrowill-dev/obskitpy"
SITE = "https://pedrowill-dev.github.io/obskitpy/"
PYPI = "https://pypi.org/project/obskitpy/"
DESC = "Observabilidade para automações Python: logs, traces e métricas num dashboard do Kibana, em dois comandos."

NAV = [
    ("Começar", [("inicio-rapido", "Início rápido")]),
    ("Referência", [("guia-obskitpy", "CLI e biblioteca"), ("dash-obskitpy", "dash-obskitpy"), ("portal", "Portal de alertas (Fase 1)"), ("dashboards", "Dashboards (Kibana)")]),
    ("Exemplos", [("exemplo", "Notícias da Prefeitura de SP")]),
    ("Entenda", [("arquitetura", "Arquitetura"), ("solucao-de-problemas", "Solução de problemas")]),
    ("Mantenedores", [("publicando", "Publicando no PyPI")]),
]
PAGES = [slug for _, items in NAV for slug, _ in items]
TITLES = {slug: t for _, items in NAV for slug, t in items}

CURRENT = ' aria-current="page"'
GITHUB_SVG = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>'
SEARCH_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>'
MENU_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18v2H3zm0 5h18v2H3zm0 5h18v2H3z"/></svg>'


def layout(*, title: str, desc: str, body: str, root: str, path: str, docs: bool = False, og: str = "") -> str:
    full_title = f"{title} · obskitpy" if title != "obskitpy" else "obskitpy — observabilidade para automações Python"
    slug = path.split("/")[-1]
    current = {
        "docs": path.startswith("docs/") and slug not in ("exemplo", "arquitetura"),
        "exemplo": slug == "exemplo",
        "arquitetura": slug == "arquitetura",
    }

    def nav_link(href, label, key):
        return f'<a href="{root}{href}"{CURRENT if current[key] else ""}>{label}</a>'
    return f"""<!doctype html>
<html lang="pt-BR" data-root="{root}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(full_title)}</title>
<meta name="description" content="{html.escape(desc)}">
<meta name="theme-color" content="#0a1020">
<meta property="og:title" content="{html.escape(full_title)}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:type" content="website">
<meta property="og:image" content="{SITE}assets/img/og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" type="image/png" href="{root}assets/img/logo-icon-dark.png">
<link rel="stylesheet" href="{root}assets/style.css">
</head>
<body>
<a class="skip" href="#main">Pular para o conteúdo</a>
<header class="site-header">
  <div class="container">
    {'<button class="icon-btn menu-btn" type="button" aria-label="Abrir menu">' + MENU_SVG + '</button>' if docs else ''}
    <a class="brand" href="{root}"><img src="{root}assets/img/logo-icon-dark.png" alt="" width="34" height="34"><span>obskit<b>py</b></span></a>
    <nav class="nav" aria-label="Principal">
      {nav_link("docs/inicio-rapido.html", "Documentação", "docs")}
      {nav_link("docs/exemplo.html", "Exemplo", "exemplo")}
      {nav_link("docs/arquitetura.html", "Arquitetura", "arquitetura")}
      <a href="{PYPI}" rel="noopener">PyPI</a>
    </nav>
    <div class="header-right">
      <button class="search-btn" type="button" aria-label="Buscar">{SEARCH_SVG}<span>Buscar…</span><kbd>/</kbd></button>
      <a class="icon-btn" href="{REPO}" aria-label="GitHub" rel="noopener">{GITHUB_SVG}</a>
    </div>
  </div>
</header>
{body}
<footer class="site-footer">
  <div class="container">
    <span>MIT © 2026 Pedro França</span>
    <span class="links">
      <a href="{REPO}" rel="noopener">GitHub</a>
      <a href="{PYPI}" rel="noopener">PyPI</a>
      <a href="{REPO}/issues" rel="noopener">Issues</a>
      <a href="{REPO}/blob/main/LICENSE" rel="noopener">Licença</a>
    </span>
  </div>
</footer>
<div class="search-modal" role="dialog" aria-modal="true" aria-label="Buscar na documentação">
  <div class="search-box"><input type="search" placeholder="Buscar na documentação…" aria-label="Buscar"><div class="search-results"></div></div>
</div>
<script src="{root}assets/app.js" defer></script>
</body>
</html>
"""


def fix_links(content: str) -> str:
    """Links .md → .html e imagens para ../assets/images (as páginas ficam em docs/)."""
    content = re.sub(r'href="([\w./-]+?)\.md(#[^"]*)?"', lambda m: f'href="{m.group(1)}.html{m.group(2) or ""}"', content)
    return re.sub(r'src="images/', 'src="../assets/images/', content)


def plain(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


def render_doc(slug: str, index: list[dict]) -> None:
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "admonition", "attr_list", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "2-3"}},
    )
    text = (DOCS / f"{slug}.md").read_text(encoding="utf-8")
    content = fix_links(md.convert(text))
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.S)
    title = plain(h1.group(1)) if h1 else TITLES[slug]
    intro = re.search(r"<p>(.*?)</p>", content, re.S)
    desc = plain(intro.group(1))[:180] if intro else DESC

    sidebar = "".join(
        f"<h4>{group}</h4>" + "".join(
            f'<a href="{s}.html"{CURRENT if s == slug else ""}>{t}</a>' for s, t in items)
        for group, items in NAV)
    toc = "".join(
        f'<a class="l{t["level"]}" href="#{t["id"]}">{plain(t["name"])}</a>'
        for top in md.toc_tokens for t in [top, *top.get("children", [])] if t["level"] in (2, 3))
    i = PAGES.index(slug)
    prev_l = f'<a class="prev" href="{PAGES[i-1]}.html"><small>← Anterior</small>{TITLES[PAGES[i-1]]}</a>' if i else ""
    next_l = f'<a class="next" href="{PAGES[i+1]}.html"><small>Próximo →</small>{TITLES[PAGES[i+1]]}</a>' if i < len(PAGES) - 1 else ""
    group = next(g for g, items in NAV if slug in dict(items))

    body = f"""<div class="container docs-layout">
<nav class="sidebar" aria-label="Documentação">{sidebar}</nav>
<main id="main" class="prose">
<div class="crumbs"><a href="../">Início</a> / {group}</div>
{content}
<a class="edit-link" href="{REPO}/edit/main/docs/{slug}.md" rel="noopener">✎ Editar esta página no GitHub</a>
<div class="pager">{prev_l}{next_l}</div>
</main>
<aside class="toc" aria-label="Nesta página"><h4>Nesta página</h4>{toc}</aside>
</div>"""
    (OUT / "docs").mkdir(exist_ok=True)
    (OUT / "docs" / f"{slug}.html").write_text(
        layout(title=title, desc=desc, body=body, root="../", path=f"docs/{slug}", docs=True), encoding="utf-8")

    # índice de busca: um item por seção h2
    parts = re.split(r'(?=<h2 id=")', content)
    for part in parts:
        m = re.match(r'<h2 id="([^"]+)"[^>]*>(.*?)</h2>', part, re.S)
        heading, anchor = (plain(m.group(2)), "#" + m.group(1)) if m else ("", "")
        body_text = plain(re.sub(r"<h1.*?</h1>", "", part, flags=re.S))
        if body_text:
            index.append({"t": title, "h": heading, "u": f"docs/{slug}.html{anchor}", "x": body_text[:1800]})


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copytree(SRC / "assets", OUT / "assets")
    shutil.copytree(DOCS / "images", OUT / "assets" / "images")
    if (SRC / "oauth").exists():  # páginas estáticas de retorno do OAuth (ex.: Slack)
        shutil.copytree(SRC / "oauth", OUT / "oauth")

    index: list[dict] = []
    for slug in PAGES:
        render_doc(slug, index)

    landing = (SRC / "landing.html").read_text(encoding="utf-8")
    (OUT / "index.html").write_text(
        layout(title="obskitpy", desc=DESC, body=landing, root="./", path="index"), encoding="utf-8")
    not_found = '<main id="main" class="container cta"><h2>Página não encontrada</h2><p>O endereço não existe (ou mudou de lugar).</p><a class="btn primary" href="./">Voltar ao início</a></main>'
    (OUT / "404.html").write_text(
        layout(title="Não encontrada", desc=DESC, body=not_found, root="/obskitpy/", path="404"), encoding="utf-8")
    (OUT / "search.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    (OUT / ".nojekyll").write_text("")
    print(f"site gerado em {OUT} ({len(PAGES)} páginas, {len(index)} entradas de busca)")


if __name__ == "__main__":
    main()
