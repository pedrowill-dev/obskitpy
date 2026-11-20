"""Gravação no Elasticsearch: um documento por notícia (id = id da Prefeitura, então reprocessar não duplica)."""
from elasticsearch import Elasticsearch

INDEX = "noticias-sp"
MAPPING = {"properties": {
    "id": {"type": "keyword"},
    "title": {"type": "text", "fields": {"raw": {"type": "keyword", "ignore_above": 256}}},
    "url": {"type": "keyword"},
    "published_at": {"type": "date"},
    "modified_at": {"type": "date"},
    "summary": {"type": "text"},
    "text": {"type": "text"},
    "word_count": {"type": "integer"},
    "links": {"type": "integer"},
    "images": {"type": "integer"},
    "categories": {"type": "keyword"},
    "keywords": {"type": "keyword"},
    "type": {"type": "keyword"},
    "extracted_at": {"type": "date"},
    "run_id": {"type": "keyword"},
    "trace_id": {"type": "keyword"},
}}


class Store:
    def __init__(self, url: str):
        self.es = Elasticsearch(url)

    def ensure_index(self) -> None:
        if not self.es.indices.exists(index=INDEX):
            self.es.indices.create(index=INDEX, mappings=MAPPING)

    def save(self, doc: dict) -> None:
        self.es.index(index=INDEX, id=str(doc["id"]), document=doc)
