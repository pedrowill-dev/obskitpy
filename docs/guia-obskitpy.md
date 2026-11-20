# Guia do obskitpy

Referência da CLI e da biblioteca. Para um passo a passo, veja o [início rápido](inicio-rapido.md).

## CLI

Todos os comandos aceitam `--dir PASTA` (padrão: `observability`) e devem ser executados na raiz do projeto.

| Comando | O que faz |
|---|---|
| `obskitpy init` | **Assistente**: cria `observability/` (stack e dashboard) e, se você quiser, já sobe tudo e roda um exemplo. |
| `obskitpy up` | Sobe a stack, mostra o progresso, espera ficar saudável e **aplica o dashboard** (Kibana). Pode ser repetido à vontade. |
| `obskitpy down [-v]` | Derruba a stack. `-v` apaga também os dados (volume do Elasticsearch). |
| `obskitpy status` | Tabela dos containers (estado e portas) e os links dos dashboards. |
| `obskitpy open [--ui kibana\|dash-obskitpy] [--realtime]` | Abre o dashboard no navegador (padrão: dash-obskitpy se existir, senão Kibana). `--realtime` abre o dash já **ao vivo** (logs a cada 1 s). |
| `obskitpy dash update` | Atualiza os arquivos do dash-obskitpy (`observability/dash/`) para a versão do pacote instalado; guarda os seus em `.bak`. |
| `obskitpy run [--] COMANDO...` | **Roda o seu programa** já apontando para a stack (ex.: `obskitpy run python app.py`). Se a stack estiver parada, sobe antes. Sem comando, roda o `obskitpy_demo.py`. |
| `obskitpy portal enable` | Liga o **portal de integrações e automações** (PostgreSQL + API + abas Integrações e Automações no dash). Veja [Portal](portal.md). |
| `obskitpy portal password` | Gera uma nova senha para o administrador do portal. |
| `obskitpy doctor` | Diagnostica Docker, Compose v2, memória do Docker e portas. |
| `obskitpy dashboard apply` | Importa `observability/dashboard.ndjson` no Kibana (sobrescreve objetos com o mesmo id). |
| `obskitpy dashboard export` | Grava no `dashboard.ndjson` o dashboard **como está no Kibana agora**. |
| `obskitpy --version` | Mostra a versão. |

### A CLI resolve em vez de falhar

O objetivo é não parar num erro quando existe uma saída. O que é seguro é automático; o que instala ou inicia
algo pede a sua confirmação.

| Situação | O que o obskitpy faz |
|---|---|
| `docker compose` não existe, mas há o binário `docker-compose` | Usa o binário sozinho. |
| Sem Compose v2 (macOS com Homebrew) | Oferece `brew install docker-compose` e segue. |
| O Docker não responde, mas o Colima está de pé | Aponta o `DOCKER_HOST` para o socket do Colima. |
| O Colima está parado | Oferece iniciá-lo (`colima start`). |
| `docker-credential-desktop` não encontrado | Repete com uma configuração do Docker sem o credential helper. |
| Porta ocupada ao subir | Escolhe a próxima livre, grava no `.env` e tenta de novo (uma porta por vez, se preciso). |
| Comando com atalho ou erro de digitação (`start`, `ps`, `upp`) | Entende (`up`, `status`...) e avisa. Para os outros, sugere: "Você quis dizer…?". |
| Já existe uma stack do obskitpy rodando | O `init` pergunta se esta automação deve **entrar nela** (mesmo dashboard; no dash você escolhe o projeto). |
| Pasta sem `observability/` | Oferece rodar o assistente `init` na hora. |
| `otel-collector-config.yaml` de uma versão antiga | No `up`, atualiza (guarda `.bak`) e recria o Collector para enviar dados a cada 1 s. |
| Arquivos do dash de uma versão antiga | O `up` avisa; `obskitpy dash update` atualiza. |
| `init` numa pasta que já tem o obskitpy | Pergunta: subir como está, reconfigurar (guarda uma cópia em `observability.bak/`) ou cancelar. |
| Collector ainda subindo / dados ainda indexando | Espera o Collector aceitar dados e os dados ficarem consultáveis antes de rodar o exemplo ou terminar o `run`. |
| `obskitpy` fora do PATH | As dicas já aparecem como `uv run obskitpy ...` (ou `python -m obskitpy ...`). |

A saída é colorida, com spinners e caixas. Ela respeita `NO_COLOR` e vira texto simples em pipes e CI
(use `FORCE_COLOR=1` para forçar as cores).

### `obskitpy init`

Com terminal interativo, faz perguntas (e usa os padrões onde você apertar Enter). Sem terminal, ou com
`--yes`, não pergunta nada.

| Opção | Padrão | Descrição |
|---|---|---|
| `--service NOME` | nome da pasta atual | Nome do serviço (minúsculas, números e `-`). Identifica seus dados. |
| `--local {kibana,dash-obskitpy,ambos}` | pergunta; sem terminal, `kibana` | **Onde visualizar os logs.** Veja [dash-obskitpy](dash-obskitpy.md). |
| `-y`, `--yes` | — | Não perguntar nada (usa os padrões e o que vier por flag). |
| `--up` | — | Sobe a stack ao terminar. |
| `--demo` | — | Com `--up`: gera `obskitpy_demo.py` e o roda, para já ter dados. |
| `--es-port` | 9200 | Porta local do Elasticsearch. |
| `--kibana-port` | 5601 | Porta local do Kibana (se usar Kibana). |
| `--dash-port` | 8600 | Porta local do dash-obskitpy (se usar). |
| `--otlp-grpc-port` | 4317 | Porta local do Collector (gRPC) — a que a biblioteca usa. |
| `--otlp-http-port` | 4318 | Porta local do Collector (HTTP). |
| `--join [STACK]` | — | Usa uma **stack do obskitpy já rodando** (pelo nome do serviço dela; sem valor, a primeira encontrada) em vez de criar outra. |
| `--standalone` | — | Não procura stacks rodando: cria uma stack própria. |
| `--force` | — | Recria uma instalação existente (a anterior fica em `observability.bak/`). |

Portas que você não informar e que estiverem ocupadas são trocadas automaticamente pela próxima livre
(o `init` avisa). Numa pasta já inicializada, o terminal interativo pergunta o que fazer; sem terminal, ele pede `--force`.

### O que o `init` cria

```
observability/
├── .env                        # serviço, interface escolhida, perfis do compose e portas
├── docker-compose.yml          # Elasticsearch 8.17, Collector e, conforme --local, Kibana 8.17 e/ou dash
├── otel-collector-config.yaml  # recebe OTLP e grava no Elasticsearch
├── dashboard.ndjson            # (Kibana) dashboard no formato de "saved objects"
├── nginx.conf                  # (dash-obskitpy) serve o dash e dá acesso só de leitura ao Elasticsearch
└── dash/                       # (dash-obskitpy) o dashboard: HTML, CSS, JS e logo
```

Versione tudo no git. O `.env` contém apenas nome, interface e portas, nenhum segredo.

## Biblioteca

```python
import obskitpy
```

### `obskitpy.setup(service_name, *, environment=None, version="0.0.0", flush_interval_ms=1_000, metrics_interval_ms=5_000, instrument_requests=True)`

Configura traces, métricas e logs e devolve uma função `shutdown()`. Chame **uma vez**, no início do programa.

| Parâmetro | Descrição |
|---|---|
| `service_name` | Vira `service.name`; precisa ser igual ao `OBSKITPY_SERVICE` do `.env`. |
| `environment` | `deployment.environment` (padrão: variável `APP_ENV`, ou `local`). |
| `version` | `service.version`. |
| `flush_interval_ms` | De quanto em quanto tempo logs e traces são enviados (padrão 1 s, para o [Realtime](dash-obskitpy.md#realtime) do dash). |
| `metrics_interval_ms` | Intervalo de envio das métricas (padrão 5 s). |
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
