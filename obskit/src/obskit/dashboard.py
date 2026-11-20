"""Dashboard base (genérico, por serviço) como saved objects do Kibana 8.x.

O dev evolui o dashboard no próprio Kibana e grava com `obskit dashboard export`;
este módulo só gera o ponto de partida do `obskit init`.
"""
import json

KIBANA_VERSION = "8.17.0"


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


def date_histogram(id_, schema="segment", field="@timestamp", interval="auto"):
    return agg(id_, "date_histogram", schema, field=field, useNormalizedEsInterval=True,
               scaleMetricValues=False, interval=interval, drop_partials=False, min_doc_count=1,
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


def ref(idx_id):
    return [{"name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern", "id": idx_id}]


def build(service: str) -> list[dict]:
    """Objetos do dashboard `<service>-observability`, filtrados por service.name."""
    def sid(kind):
        return f"{service}-{kind}"

    scope = f'resource.attributes.service.name : "{service}"'

    def q(extra=""):
        return f"{scope} and {extra}" if extra else scope

    errors = q('severity_text : "ERROR"')
    data_views = {
        sid("logs"): ("Logs", "logs-generic.otel-*"),
        sid("traces"): ("Traces", "traces-generic.otel-*"),
    }
    # (id, data view, query, definição, x, y, w, h)
    visualizations = [
        (sid("v-logs"), "logs", q(), metric_vis("Logs", [agg(1, "count", "metric", "Logs")]), 0, 0, 12, 8),
        (sid("v-errors"), "logs", errors, metric_vis("Erros", [agg(1, "count", "metric", "Erros")]), 12, 0, 12, 8),
        (sid("v-traces"), "traces", q("name : * and not parent_span_id : *"),
         metric_vis("Execuções (traces)", [agg(1, "count", "metric", "Execuções")]), 24, 0, 12, 8),
        (sid("v-failed"), "traces", q('status.code : "Error" and name : * and not parent_span_id : *'),
         metric_vis("Execuções com erro", [agg(1, "count", "metric", "Com erro")]), 36, 0, 12, 8),
        (sid("v-logs-time"), "logs", q(),
         chart_vis("Logs por severidade", "histogram",
                   [agg(1, "count", "metric", "Logs"), date_histogram(2),
                    terms(3, "severity_text", "Severidade", schema="group")], "Logs"), 0, 8, 24, 14),
        (sid("v-spans-time"), "traces", q(),
         chart_vis("Spans por operação", "line",
                   [agg(1, "count", "metric", "Spans"), date_histogram(2),
                    terms(3, "name", "Span", size=5, schema="group")], "Spans"), 24, 8, 24, 14),
        (sid("v-spans"), "traces", q(),
         table_vis("Duração dos spans (ns)", [
             agg(1, "avg", "metric", "Média (ns)", field="duration"),
             agg(3, "max", "metric", "Máxima (ns)", field="duration"),
             agg(4, "count", "metric", "Qtd"),
             terms(2, "name", "Span", size=20, schema="bucket")]), 0, 22, 24, 14),
        (sid("v-error-origin"), "logs", errors,
         table_vis("Erros por tipo e origem", [
             agg(1, "count", "metric", "Qtd"),
             terms(2, "attributes.error.type", "Tipo", schema="bucket"),
             terms(3, "attributes.error.origin", "Origem (arquivo:função:linha)", schema="bucket")]),
         24, 22, 24, 14),
    ]

    objects = [{"type": "index-pattern", "id": id_, "references": [],
                "attributes": {"title": pattern, "name": f"{service} - {name}", "timeFieldName": "@timestamp"}}
               for id_, (name, pattern) in data_views.items()]

    panels, dash_refs = [], []
    for n, (id_, view, query, vis, x, y, w, h) in enumerate(visualizations):
        objects.append({
            "type": "visualization", "id": id_, "references": ref(sid(view)),
            "attributes": {"title": vis["title"], "visState": json.dumps(vis), "uiStateJSON": "{}",
                           "description": "", "version": 1,
                           "kibanaSavedObjectMeta": {"searchSourceJSON": search_source(query)}}})
        panels.append({"version": KIBANA_VERSION, "type": "visualization", "panelIndex": str(n),
                       "gridData": {"x": x, "y": y, "w": w, "h": h, "i": str(n)},
                       "embeddableConfig": {}, "panelRefName": f"panel_{n}"})
        dash_refs.append({"name": f"panel_{n}", "type": "visualization", "id": id_})

    search_id = sid("s-errors")
    objects.append({
        "type": "search", "id": search_id, "references": ref(sid("logs")),
        "attributes": {"title": "Erros (logs)", "description": "", "version": 1, "hits": 0,
                       "columns": ["body.text", "attributes.error.type", "attributes.error.origin", "trace_id"],
                       "sort": [["@timestamp", "desc"]],
                       "kibanaSavedObjectMeta": {"searchSourceJSON": search_source(errors)}}})
    n = len(visualizations)
    panels.append({"version": KIBANA_VERSION, "type": "search", "panelIndex": str(n),
                   "gridData": {"x": 0, "y": 36, "w": 48, "h": 14, "i": str(n)},
                   "embeddableConfig": {}, "panelRefName": f"panel_{n}"})
    dash_refs.append({"name": f"panel_{n}", "type": "search", "id": search_id})

    objects.append({
        "type": "dashboard", "id": dashboard_id(service), "references": dash_refs,
        "attributes": {
            "title": f"{service} - Observabilidade",
            "description": "Logs, traces e erros do serviço. Evolua este dashboard no Kibana e rode `obskit dashboard export`.",
            "hits": 0, "version": 1, "panelsJSON": json.dumps(panels),
            "optionsJSON": json.dumps({"useMargins": True, "syncColors": False, "syncCursor": True,
                                       "syncTooltips": False, "hidePanelTitles": False}),
            "timeRestore": True, "timeFrom": "now-24h", "timeTo": "now",
            "refreshInterval": {"pause": False, "value": 30000},
            "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps(
                {"query": {"query": "", "language": "kuery"}, "filter": []})}}})
    return objects


def dashboard_id(service: str) -> str:
    return f"{service}-observability"


def to_ndjson(objects: list[dict]) -> str:
    return "\n".join(json.dumps(o, ensure_ascii=False) for o in objects) + "\n"
