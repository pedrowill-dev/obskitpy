"""Painéis próprios do projeto, somados ao dashboard base do obskit.

Fluxo do dev: edite as listas abaixo e rode
    python observability/extend_dashboard.py && obskit dashboard apply
O arquivo é a fonte da verdade: o ndjson é regerado do zero (base do obskit + painéis daqui).
Prefere montar no Kibana? Use `obskit dashboard export` e não rode este script depois.
"""
import json
from pathlib import Path

from obskit.dashboard import (KIBANA_VERSION, build, agg, chart_vis, date_histogram, metric_vis, ref,
                              search_source, table_vis, terms)

FILE = Path(__file__).with_name("dashboard.ndjson")
DASH_ID = "noticias-sp-observability"
NEWS, METRICS = "noticias-sp-news", "noticias-sp-metrics"
Y0 = 50  # abaixo dos painéis do obskit (que ocupam y 0..50)

DATA_VIEWS = [
    (NEWS, "Notícias SP", "noticias-sp", "published_at"),
    (METRICS, "noticias-sp - Métricas", "metrics-generic.otel-*", "@timestamp"),
]
ONLY_NEWS = 'type : "noticia"'
# (id, data view, query, definição, x, y, w, h)
VISUALIZATIONS = [
    ("noticias-sp-v-total", NEWS, "", metric_vis("Notícias extraídas", [agg(1, "count", "metric", "Notícias")]), 0, Y0, 12, 8),
    ("noticias-sp-v-agenda", NEWS, 'type : "agenda"',
     metric_vis("Agendas do prefeito", [agg(1, "count", "metric", "Agendas")]), 12, Y0, 12, 8),
    ("noticias-sp-v-words-avg", NEWS, ONLY_NEWS,
     metric_vis("Palavras por matéria (média)", [agg(1, "avg", "metric", "Palavras", field="word_count")]), 24, Y0, 12, 8),
    ("noticias-sp-v-links", NEWS, ONLY_NEWS,
     metric_vis("Links por matéria (média)", [agg(1, "avg", "metric", "Links", field="links")]), 36, Y0, 12, 8),
    ("noticias-sp-v-per-day", NEWS, "",
     chart_vis("Publicações por dia", "histogram",
               [agg(1, "count", "metric", "Publicações"),
                date_histogram(2, field="published_at", interval="d"),
                terms(3, "type", "Tipo", schema="group")], "Publicações"), 0, Y0 + 8, 24, 14),
    ("noticias-sp-v-status", METRICS, 'attributes.status : * and resource.attributes.service.name : "noticias-sp"',
     chart_vis("Notícias processadas por status", "histogram",
               [agg(1, "sum", "metric", "Notícias", field="metrics.noticias.processadas"), date_histogram(2),
                terms(3, "attributes.status", "Status", schema="group")], "Notícias"), 24, Y0 + 8, 24, 14),
    ("noticias-sp-v-longest", NEWS, ONLY_NEWS,
     table_vis("Maiores matérias", [
         agg(2, "max", "metric", "Palavras", field="word_count"),
         terms(1, "title.raw", "Título", order_by="2", size=10, schema="bucket")]), 0, Y0 + 22, 48, 14),
]
SEARCH = ("noticias-sp-s-news", "Notícias extraídas", NEWS, "",
          ["title", "type", "word_count", "url"], [["published_at", "desc"]])


def extend(objects: list[dict]) -> list[dict]:
    dash = next(o for o in objects if o["type"] == "dashboard")
    panels, refs = json.loads(dash["attributes"]["panelsJSON"]), dash["references"]
    new: list[dict] = []

    for id_, name, pattern, time_field in DATA_VIEWS:
        new.append({"type": "index-pattern", "id": id_, "references": [],
                    "attributes": {"title": pattern, "name": name, "timeFieldName": time_field}})
    for id_, view, query, vis, x, y, w, h in VISUALIZATIONS:
        new.append({"type": "visualization", "id": id_, "references": ref(view), "attributes": {
            "title": vis["title"], "visState": json.dumps(vis), "uiStateJSON": "{}", "description": "",
            "version": 1, "kibanaSavedObjectMeta": {"searchSourceJSON": search_source(query)}}})
    sid, title, view, query, columns, sort = SEARCH
    new.append({"type": "search", "id": sid, "references": ref(view), "attributes": {
        "title": title, "description": "", "version": 1, "hits": 0, "columns": columns, "sort": sort,
        "kibanaSavedObjectMeta": {"searchSourceJSON": search_source(query)}}})

    layout = [(v[0], "visualization", *v[4:]) for v in VISUALIZATIONS] + [(sid, "search", 0, Y0 + 36, 48, 14)]
    for id_, type_, x, y, w, h in layout:
        n = len(panels)
        panels.append({"version": KIBANA_VERSION, "type": type_, "panelIndex": str(n),
                       "gridData": {"x": x, "y": y, "w": w, "h": h, "i": str(n)},
                       "embeddableConfig": {}, "panelRefName": f"panel_{n}"})
        refs.append({"name": f"panel_{n}", "type": type_, "id": id_})

    dash["attributes"]["timeFrom"] = "now-7d"  # o robô extrai os últimos 7 dias
    dash["attributes"]["panelsJSON"] = json.dumps(panels)
    dash["references"] = refs
    return objects + new


if __name__ == "__main__":
    out = extend(build("noticias-sp"))
    FILE.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in out) + "\n", encoding="utf-8")
    print(f"{FILE} ({len(out)} objetos)")
