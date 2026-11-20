"""Converte um item da API em um documento pronto para o Elasticsearch."""
import html

from bs4 import BeautifulSoup

from .client import BASE_URL


class ParseError(Exception):
    """O item não tem os campos mínimos de uma notícia (layout/estrutura mudou?)."""


def _field(item: dict, name: str) -> str:
    for f in item.get("contentFields", []):
        if (f.get("fieldReference") or f.get("name")) == name:
            return (f.get("contentFieldValue") or {}).get("data") or ""
    return ""


def parse(item: dict) -> dict:
    title = (item.get("title") or "").strip()
    if not title or not item.get("datePublished"):
        raise ParseError(f"item {item.get('id')} sem título ou data de publicação")

    content = _field(item, "content")
    soup = BeautifulSoup(content, "html.parser")
    text = soup.get_text(" ", strip=True)
    keywords = item.get("keywords") or []
    return {
        "id": item["id"],
        "title": title,
        "url": f"{BASE_URL}/w/{item['friendlyUrlPath']}",
        "published_at": item["datePublished"],
        "modified_at": item.get("dateModified"),
        "summary": html.unescape(_field(item, "description")).replace("\xa0", " ").strip(),
        "text": text,
        "word_count": len(text.split()),
        "links": len(soup.select("a[href]")),
        "images": len(soup.select("img")),
        "categories": [c["taxonomyCategoryName"] for c in item.get("taxonomyCategoryBriefs", [])],
        "keywords": keywords,
        # "Dia do prefeito" é a agenda diária, não uma matéria.
        "type": "agenda" if "dia do prefeito" in keywords else "noticia",
    }
