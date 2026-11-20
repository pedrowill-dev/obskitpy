"""Download e extração de dados de artigos da Wikipedia."""
import logging
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .telemetry import fetch_retries

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "rpa-wikipedia-observability/1.0 (projeto de estudo)"}
MAX_ATTEMPTS = 3
MAX_INFOBOX_ROWS = 15


class PageNotFound(Exception):
    """A página não existe (HTTP 404); repetir não adianta."""


class ParseError(Exception):
    """O HTML não tem a estrutura esperada (layout mudou?)."""


def fetch(lang: str, slug: str) -> str:
    url = f"https://{lang}.wikipedia.org/wiki/{quote(slug)}"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 404:
                raise PageNotFound(url)
            resp.raise_for_status()
            return resp.text
        except PageNotFound:
            raise
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            status = exc.response.status_code if exc.response is not None else None
            retriable = status is None or status >= 500
            if not retriable or attempt == MAX_ATTEMPTS:
                raise
            fetch_retries.add(1, {"error.type": type(exc).__name__})
            log.warning(
                "download falhou, tentando novamente",
                extra={"page.slug": slug, "retry.attempt": attempt, "error.type": type(exc).__name__},
            )
            time.sleep(2**attempt)


def parse(html: str, slug: str, lang: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("#firstHeading")
    content = soup.select_one("#mw-content-text .mw-parser-output")
    if title is None or content is None:
        raise ParseError(f"estrutura inesperada na página '{slug}'")

    references = len(soup.select("ol.references li"))
    for citation in content.select("sup.reference"):
        citation.decompose()

    # Parágrafos de texto: filhos diretos do conteúdo ou da seção (ignora os de tabelas e avisos).
    paragraphs = (p.get_text(" ", strip=True) for p in content.select(":scope > p, section > p"))
    summary = next((p for p in paragraphs if p), "")

    infobox = {}
    box = content.select_one("table.infobox")
    for row in box.select("tr") if box else []:
        th, td = row.find("th"), row.find("td")
        if th and td and len(infobox) < MAX_INFOBOX_ROWS:
            infobox[th.get_text(" ", strip=True)] = td.get_text(" ", strip=True)[:200]

    last_modified = soup.select_one("#footer-info-lastmod")
    return {
        "title": title.get_text(strip=True),
        "slug": slug,
        "language": lang,
        "url": f"https://{lang}.wikipedia.org/wiki/{quote(slug)}",
        "summary": summary[:1000],
        "word_count": len(content.get_text(" ").split()),
        "sections": len(content.select("h2")),
        "links": len(content.select("a[href^='/wiki/'], a[href*='.wikipedia.org/wiki/']")),
        "references": references,
        "categories": [a.get_text(strip=True) for a in soup.select("#catlinks ul li a")],
        "infobox": infobox,
        "last_modified": last_modified.get_text(" ", strip=True) if last_modified else None,
    }
