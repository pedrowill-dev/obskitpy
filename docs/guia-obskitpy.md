# Guia do obskitpy

Referência da CLI e da biblioteca. Para um passo a passo, veja o [início rápido](inicio-rapido.md).

## CLI

Todos os comandos aceitam `--dir PASTA` (padrão: `observability`) e devem ser executados na raiz do projeto.

| Comando | O que faz |
|---|---|
| `obskitpy init` | Cria `observability/` com a stack e o dashboard base. |
| `obskitpy up` | Sobe a stack, espera os serviços ficarem saudáveis (até 5 min) e **aplica o dashboard**. Pode ser repetido à vontade. |
| `obskitpy down [-v]` | Derruba a stack. `-v` apaga também os dados (volume do Elasticsearch). |
| `obskitpy status` | Mostra os containers e o link do dashboard. |
| `obskitpy open` | Abre o dashboard no navegador. |
| `obskitpy dashboard apply` | Importa `observability/dashboard.ndjson` no Kibana (sobrescreve objetos com o mesmo id). |
| `obskitpy dashboard export` | Grava no `dashboard.ndjson` o dashboard **como está no Kibana agora**. |

### `obskitpy init`

| Opção | Padrão | Descrição |
|---|---|---|
| `--service NOME` | nome da pasta atual | Nome do serviço (minúsculas, números e `-`). Identifica seus dados no Kibana. |
| `--es-port` | 9200 | Porta local do Elasticsearch. |
| `--kibana-port` | 5601 | Porta local do Kibana. |
| `--otlp-grpc-port` | 4317 | Porta local do Collector (gRPC) — a que a biblioteca usa. |
| `--otlp-http-port` | 4318 | Porta local do Collector (HTTP). |
| `--force` | — | Recria uma instalação existente (**o `dashboard.ndjson` atual é perdido**). |

Sem `--force`, o `init` se recusa a sobrescrever uma pasta já inicializada.

### O que o `init` cria

```
observability/
├── .env                        # serviço e portas (lido pelo docker compose e pela CLI)
├── docker-compose.yml          # Elasticsearch 8.17, Kibana 8.17, OpenTelemetry Collector
├── otel-collector-config.yaml  # recebe OTLP e grava no Elasticsearch
└── dashboard.ndjson            # dashboard no formato de "saved objects" do Kibana
```

Versione tudo no git. O `.env` contém apenas nome e portas, nenhum segredo.

## Biblioteca

```python
import obskitpy
```

### `obskitpy.setup(service_name, *, environment=None, version="0.0.0", metrics_interval_ms=10_000, instrument_requests=True)`

Configura traces, métricas e logs e devolve uma função `shutdown()`. Chame **uma vez**, no início do programa.

| Parâmetro | Descrição |
|---|---|
| `service_name` | Vira `service.name`; precisa ser igual ao `OBSKITPY_SERVICE` do `.env`. |
| `environment` | `deployment.environment` (padrão: variável `APP_ENV`, ou `local`). |
| `version` | `service.version`. |
| `metrics_interval_ms` | Intervalo de envio das métricas. |
| `instrument_requests` | Cria um span para cada `requests.get/post/...` (precisa do extra `obskitpy[requests]`; sem ele é ignorado). |

O que acontece por baixo:

- **Logs**: um handler OpenTelemetry é adicionado ao *root logger*. Tudo que você já loga com `logging` passa a ser
  enviado, com `trace_id` e `span_id` do span ativo — é isso que liga um erro ao seu trace.
- **Erros enriquecidos**: todo log com `exc_info` (`log.exception(...)`, `log.error(..., exc_info=True)`) ganha
  `error.type`, `error.origin` e `error.chain` automaticamente.
- **Métricas**: exportadas em temporalidade *delta* (`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta`,
  definida por padrão), exigida pelo modo OTel do Elasticsearch.
- **Destino**: `OTEL_EXPORTER_OTLP_ENDPOINT` (padrão `http://localhost:4317`). Mude a variável se a porta do `init`
  for outra ou se o Collector estiver em outro host.

Sempre chame `shutdown()` ao terminar (ou em um `finally`); sem isso, os últimos dados podem se perder.

### `@obskitpy.traced(name=None)`

Decorator que abre um span por chamada. Se a função levantar exceção, ela é registrada no span (que fica com
status *Error*) e **repropagada**. O nome padrão é `modulo.funcao`.

### `obskitpy.tracer(name="app")`

Devolve um tracer OpenTelemetry para spans manuais e atributos:

```python
tracer = obskitpy.tracer("pedidos")
with tracer.start_as_current_span("processar_pedido", attributes={"pedido.id": 42}) as span:
    ...
    span.set_attribute("pedido.itens", 3)
```

### `obskitpy.meter(name="app")`

Devolve um meter OpenTelemetry para métricas de negócio:

```python
meter = obskitpy.meter("pedidos")
processados = meter.create_counter("pedidos.processados", unit="{pedido}")
duracao = meter.create_histogram("pedidos.duracao", unit="ms")

processados.add(1, {"status": "success"})
duracao.record(123.4, {"status": "success"})
```

No Elasticsearch o contador aparece como `metrics.pedidos.processados` e os atributos como `attributes.status`
(use esses campos ao montar painéis).

### `obskitpy.error_chain(exc)` e `obskitpy.error_attributes(exc)`

Usadas internamente; úteis se você quiser montar o log de erro à mão.
`error_attributes` devolve `error.type`, `error.origin` (`arquivo:função:linha` do ponto onde o erro nasceu)
e `error.chain` (JSON com um item por frame do traceback).

## Variáveis de ambiente

| Variável | Padrão | Efeito |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Para onde enviar os dados. |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `delta` | Não altere para `cumulative` (histogramas seriam descartados). |
| `APP_ENV` | `local` | Valor de `deployment.environment`. |

## Onde os dados ficam no Elasticsearch

| Sinal | Data stream | Campos úteis |
|---|---|---|
| Logs | `logs-generic.otel-default` | `body.text`, `severity_text`, `attributes.*`, `trace_id`, `resource.attributes.service.name` |
| Traces | `traces-generic.otel-default` | `name`, `duration` (ns), `parent_span_id` (ausente nos spans raiz), `status.code`, `trace_id` |
| Métricas | `metrics-generic.otel-default` | `metrics.<nome>`, `attributes.*`, `resource.attributes.service.name` |

Todos os serviços que apontam para a mesma stack compartilham esses data streams; o `service.name` os separa.
Spans que registram exceção geram também documentos de *evento* sem `name` — por isso as consultas do dashboard
usam `name : *` ao contar spans raiz.
