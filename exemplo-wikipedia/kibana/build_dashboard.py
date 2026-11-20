"""Gera kibana/dashboard.ndjson (saved objects do Kibana 8.x). Uso: python kibana/build_dashboard.py"""
import json
from pathlib import Path

KIBANA_VERSION = "8.17.0"

DATA_VIEWS = {
    "rpa-pages": ("RPA - Páginas extraídas", "rpa-wikipedia-pages", "extracted_at"),
    "rpa-logs": ("RPA - Logs", "logs-generic.otel-*", "@timestamp"),
    "rpa-traces": ("RPA - Traces", "traces-generic.otel-*", "@timestamp"),
    "rpa-metrics": ("RPA - Métricas", "metrics-generic.otel-*", "@timestamp"),
}


def search_source(query: str) -> str:
    return json.dumps(
        {
            "query": {"query": query, "language": "kuery"},
            "filter": [],
            "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index",
        }
    )


def agg(id_, type_, schema, label=None, **params):
    item = {"id": str(id_), "enabled": True, "type": type_, "params": params, "schema": schema}
    if label:
        item["params"]["customLabel"] = label
    return item


def terms(id_, field, label, order_by="1", size=10, schema="segment"):
    return agg(id_, "terms", schema, label, field=field, orderBy=order_by, order="desc",
               size=size, otherBucket=False, missingBucket=False)


def date_histogram(id_, schema="segment"):
    return agg(id_, "date_histogram", schema, field="@timestamp", useNormalizedEsInterval=True,
               scaleMetricValues=False, interval="auto", drop_partials=False, min_doc_count=1,
               extended_bounds={})


def chart_params(kind, value_title):
    return {
        "type": kind,
        "grid": {"categoryLines": False},
        "categoryAxes": [{"id": "CategoryAxis-1", "type": "category", "position": "bottom", "show": True,
                          "style": {}, "scale": {"type": "linear"},
                          "labels": {"show": True, "filter": True, "truncate": 100}, "title": {}}],
        "valueAxes": [{"id": "ValueAxis-1", "name": "LeftAxis-1", "type": "value", "position": "left",
                       "show": True, "style": {}, "scale": {"type": "linear", "mode": "normal"},
                       "labels": {"show": True, "rotate": 0, "filter": False, "truncate": 100},
                       "title": {"text": value_title}}],
        "seriesParams": [{"show": True, "type": kind, "mode": "normal",
                          "data": {"label": value_title, "id": "1"}, "valueAxis": "ValueAxis-1",
                          "drawLinesBetweenPoints": True, "lineWidth": 2, "showCircles": True}],
        "addTooltip": True, "addLegend": True, "legendPosition": "right", "times": [],
        "addTimeMarker": False, "labels": {},
        "thresholdLine": {"show": False, "value": 10, "width": 1, "style": "full", "color": "#E7664C"},
    }


def metric_vis(title, aggs):
    return {"title": title, "type": "metric", "aggs": aggs, "params": {
        "addTooltip": True, "addLegend": False, "type": "metric",
        "metric": {"percentageMode": False, "useRanges": False, "colorSchema": "Green to Red",
                   "metricColorMode": "None", "colorsRange": [{"from": 0, "to": 10000}],
                   "labels": {"show": True}, "invertColors": False,
                   "style": {"bgFill": "#000", "bgColor": False, "labelColor": False,
                             "subText": "", "fontSize": 48}}}}


def chart_vis(title, kind, aggs, value_title):
    return {"title": title, "type": kind, "aggs": aggs, "params": chart_params(kind, value_title)}


def table_vis(title, aggs):
    return {"title": title, "type": "table", "aggs": aggs, "params": {
        "perPage": 10, "showPartialRows": False, "showMetricsAtAllLevels": False,
        "showTotal": False, "totalFunc": "sum", "percentageCol": ""}}


ERRORS = 'severity_text : "ERROR"'

# (id, índice, query, definição, x, y, w, h)
VISUALIZATIONS = [
    ("rpa-v-pages", "rpa-pages", "",
     metric_vis("Páginas extraídas", [agg(1, "count", "metric", "Páginas")]), 0, 0, 12, 8),
    ("rpa-v-runs", "rpa-traces", 'name : "rpa.run"',
     metric_vis("Execuções do robô", [agg(1, "count", "metric", "Execuções")]), 12, 0, 12, 8),
    ("rpa-v-errors", "rpa-logs", ERRORS,
     metric_vis("Erros registrados", [agg(1, "count", "metric", "Erros")]), 24, 0, 12, 8),
    ("rpa-v-retries", "rpa-logs", 'body.text : "download falhou"',
     metric_vis("Retentativas de download", [agg(1, "count", "metric", "Retentativas")]), 36, 0, 12, 8),
    ("rpa-v-logs-time", "rpa-logs", "",
     chart_vis("Logs por severidade", "histogram",
               [agg(1, "count", "metric", "Logs"), date_histogram(2),
                terms(3, "severity_text", "Severidade", schema="group")], "Logs"), 0, 8, 24, 14),
    ("rpa-v-pages-status", "rpa-metrics", "",
     chart_vis("Páginas processadas por status", "line",
               [agg(1, "sum", "metric", "Páginas", field="metrics.rpa.pages.processed"), date_histogram(2),
                terms(3, "attributes.status", "Status", schema="group")], "Páginas"), 24, 8, 24, 14),
    ("rpa-v-words", "rpa-pages", "",
     chart_vis("Palavras por página", "histogram",
               [agg(1, "max", "metric", "Palavras", field="word_count"),
                terms(2, "title", "Página")], "Palavras"), 0, 22, 24, 14),
    ("rpa-v-spans", "rpa-traces", "",
     table_vis("Duração dos spans (ns)", [
         agg(1, "avg", "metric", "Média (ns)", field="duration"),
         agg(3, "max", "metric", "Máxima (ns)", field="duration"),
         agg(4, "count", "metric", "Qtd"),
         terms(2, "name", "Span", size=20, schema="bucket")]), 24, 22, 24, 14),
    ("rpa-v-error-origin", "rpa-logs", ERRORS,
     table_vis("Erros por tipo e origem", [
         agg(1, "count", "metric", "Qtd"),
         terms(2, "attributes.error.type", "Tipo", schema="bucket"),
         terms(3, "attributes.error.origin", "Origem (arquivo:função:linha)", schema="bucket")]), 0, 36, 24, 14),
    ("rpa-v-data", "rpa-pages", "",
     table_vis("Dados extraídos", [
         agg(2, "max", "metric", "Palavras", field="word_count"),
         agg(3, "max", "metric", "Seções", field="sections"),
         agg(4, "max", "metric", "Links", field="links"),
         agg(5, "max", "metric", "Referências", field="references"),
         terms(1, "title", "Página", order_by="2", size=20, schema="bucket")]), 24, 36, 24, 14),
]


def ref(idx_id):
    return [{"name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern", "id": idx_id}]


def build():
    objects = []
    for id_, (name, pattern, time_field) in DATA_VIEWS.items():
        objects.append({"type": "index-pattern", "id": id_, "references": [],
                        "attributes": {"title": pattern, "name": name, "timeFieldName": time_field}})

    panels, dash_refs = [], []
    for n, (id_, idx, query, vis, x, y, w, h) in enumerate(VISUALIZATIONS):
        objects.append({
            "type": "visualization", "id": id_, "references": ref(idx),
            "attributes": {"title": vis["title"], "visState": json.dumps(vis), "uiStateJSON": "{}",
                           "description": "", "version": 1,
                           "kibanaSavedObjectMeta": {"searchSourceJSON": search_source(query)}}})
        panels.append({"version": KIBANA_VERSION, "type": "visualization", "panelIndex": str(n),
                       "gridData": {"x": x, "y": y, "w": w, "h": h, "i": str(n)},
                       "embeddableConfig": {}, "panelRefName": f"panel_{n}"})
        dash_refs.append({"name": f"panel_{n}", "type": "visualization", "id": id_})

    objects.append({
        "type": "search", "id": "rpa-s-errors", "references": ref("rpa-logs"),
        "attributes": {"title": "Erros do robô (logs)", "description": "", "version": 1, "hits": 0,
                       "columns": ["body.text", "attributes.error.type", "attributes.error.origin",
                                   "attributes.page.slug", "trace_id"],
                       "sort": [["@timestamp", "desc"]],
                       "kibanaSavedObjectMeta": {"searchSourceJSON": search_source(ERRORS)}}})
    n = len(VISUALIZATIONS)
    panels.append({"version": KIBANA_VERSION, "type": "search", "panelIndex": str(n),
                   "gridData": {"x": 0, "y": 50, "w": 48, "h": 14, "i": str(n)},
                   "embeddableConfig": {}, "panelRefName": f"panel_{n}"})
    dash_refs.append({"name": f"panel_{n}", "type": "search", "id": "rpa-s-errors"})

    objects.append({
        "type": "dashboard", "id": "rpa-wikipedia-observability", "references": dash_refs,
        "attributes": {
            "title": "RPA Wikipedia - Observabilidade",
            "description": "Logs, traces, métricas e dados extraídos pelo robô da Wikipedia.",
            "hits": 0, "version": 1, "panelsJSON": json.dumps(panels),
            "optionsJSON": json.dumps({"useMargins": True, "syncColors": False, "syncCursor": True,
                                       "syncTooltips": False, "hidePanelTitles": False}),
            "timeRestore": True, "timeFrom": "now-24h", "timeTo": "now",
            "refreshInterval": {"pause": False, "value": 30000},
            "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps(
                {"query": {"query": "", "language": "kuery"}, "filter": []})}}})
    return objects


if __name__ == "__main__":
    out = Path(__file__).with_name("dashboard.ndjson")
    out.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in build()) + "\n", encoding="utf-8")
    print(f"{out} ({len(build())} objetos)")
