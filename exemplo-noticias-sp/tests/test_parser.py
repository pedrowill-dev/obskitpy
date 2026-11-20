import json
from pathlib import Path

import pytest

from noticias_sp.parser import ParseError, parse

ITEMS = json.loads((Path(__file__).with_name("items.json")).read_text(encoding="utf-8"))


def test_parse_real_items():
    for item in ITEMS:
        doc = parse(item)
        assert doc["title"] and doc["url"].startswith("https://prefeitura.sp.gov.br/w/")
        assert doc["type"] in ("noticia", "agenda")
        assert doc["word_count"] == len(doc["text"].split())
        assert "<" not in doc["summary"]


def test_agenda_is_detected():
    assert parse({**ITEMS[0], "keywords": ["dia do prefeito"]})["type"] == "agenda"


def test_missing_title_raises():
    with pytest.raises(ParseError):
        parse({**ITEMS[0], "title": ""})
