"""Persistência dos dados extraídos no Elasticsearch (um documento por página)."""
from elasticsearch import Elasticsearch

INDEX = "rpa-wikipedia-pages"

MAPPINGS = {
    "properties": {
        "title": {"type": "keyword"},
        "slug": {"type": "keyword"},
        "language": {"type": "keyword"},
        "url": {"type": "keyword"},
        "summary": {"type": "text"},
        "word_count": {"type": "integer"},
        "sections": {"type": "integer"},
        "links": {"type": "integer"},
        "references": {"type": "integer"},
        "categories": {"type": "keyword"},
        "infobox": {"type": "flattened"},  # chaves variam por artigo
        "last_modified": {"type": "keyword"},
        "extracted_at": {"type": "date"},
        "run_id": {"type": "keyword"},
        "trace_id": {"type": "keyword"},  # liga o dado ao trace que o produziu
    }
}


class Store:
    def __init__(self, url: str):
        self.es = Elasticsearch(url, max_retries=3, retry_on_timeout=True)

    def ensure_index(self) -> None:
        if not self.es.indices.exists(index=INDEX):
            self.es.indices.create(index=INDEX, mappings=MAPPINGS)

    def save(self, doc: dict) -> None:
        # id = slug: reexecuções atualizam a página em vez de duplicá-la.
        self.es.index(index=INDEX, id=doc["slug"], document=doc)
