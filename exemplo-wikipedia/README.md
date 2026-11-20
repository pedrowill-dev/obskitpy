# RPA Wikipedia com observabilidade (OpenTelemetry + Elasticsearch + Kibana)

> Protótipo original, com a stack montada à mão. Para novos projetos use o [`obskitpy`](../obskitpy/README.md).

```
rpa (Python) ──OTLP/gRPC──▶ otel-collector ──▶ Elasticsearch ◀── Kibana (dashboard)
     └────────── dados extraídos (índice rpa-wikipedia-pages) ──────────▲
```

- **Robô** (`rpa/`): baixa artigos da Wikipedia (pt), extrai título, resumo, nº de palavras/seções/links/referências, categorias e infobox, e grava no Elasticsearch (`rpa-wikipedia-pages`, 1 doc por página).
- **Traces**: `rpa.run` → `rpa.process_page` → `rpa.fetch` (+ span HTTP automático) / `rpa.parse` / `rpa.store`.
- **Métricas**: `rpa.pages.processed{status}`, `rpa.page.duration{status}`, `rpa.fetch.retries`.
- **Logs**: via `logging`, já correlacionados com `trace_id`/`span_id`. Erros trazem `error.type`, `error.origin` (arquivo:função:linha) e `error.chain` (cadeia de chamadas, ver `rpa/error_chain.py`).
- **Erro de demonstração**: o último tópico padrão não existe (404) para popular os painéis de erro.

## Executar

Docker via Colima (macOS): `colima start --cpu 4 --memory 6 --disk 30`. Se o `docker compose` reclamar de `docker-credential-desktop`, rode com `DOCKER_CONFIG` apontando para uma pasta com `config.json` = `{}`.

```bash
docker compose up -d --build
```

Aguarde ~2 min (Elasticsearch + Kibana). Depois abra
http://localhost:5601/app/dashboards#/view/rpa-wikipedia-observability

O robô roda a cada 60 s (`RPA_INTERVAL_SECONDS` no compose; `0` = uma vez). Outros tópicos: variável `TOPICS` (separados por vírgula) e `WIKI_LANG`.

Se alterar o dashboard em `kibana/build_dashboard.py`: `python kibana/build_dashboard.py` e `docker compose run --rm kibana-setup`.

> Ambiente de demonstração: segurança do Elasticsearch desativada e portas publicadas só em 127.0.0.1.

## Uso no dia a dia

| Quero... | Faça |
|---|---|
| Ver o dashboard | http://localhost:5601/app/dashboards#/view/rpa-wikipedia-observability |
| Ver erros com trace/stack | Dashboard → painel "Erros do robô (logs)"; copie o `trace_id` e filtre em Discover (`RPA - Traces`) |
| Ver os dados extraídos | Painel "Dados extraídos" ou `curl localhost:9200/rpa-wikipedia-pages/_search?pretty` |
| Logs do robô | `docker compose logs -f rpa` |
| Mudar tópicos | `TOPICS` no serviço `rpa` do compose, depois `docker compose up -d` |
| Parar / limpar tudo | `docker compose down` / `docker compose down -v` |
