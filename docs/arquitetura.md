# Arquitetura

## Visão geral

```
 seu processo Python                 docker compose (observability/)
┌──────────────────────┐   OTLP/gRPC   ┌────────────────┐        ┌───────────────┐      ┌────────┐
│ obskitpy.setup(...)    │ ────────────▶ │ OTel Collector │ ─────▶ │ Elasticsearch │ ◀──── │ Kibana │
│  logs / traces /     │  :4317        │  (contrib)     │  modo  │  data streams │      │  :5601 │
│  métricas            │               └────────────────┘  otel  └───────────────┘      └────────┘
│                      │ ─── dados extraídos (opcional, índice próprio) ──────▲
└──────────────────────┘
        ▲
        └── obskitpy CLI: init (gera arquivos) · up (docker compose + importa dashboard) · export/apply
```

## Componentes

**Biblioteca `obskitpy`** (`obskitpy/src/obskitpy/__init__.py`)
Configura o SDK do OpenTelemetry (três exportadores OTLP/gRPC), liga o `logging` ao pipeline de logs e adiciona
um filtro que enriquece logs com exceção (`error.type`, `error.origin`, `error.chain`). Não prende você a nada
proprietário: `tracer()` e `meter()` devolvem objetos do OpenTelemetry padrão.

**OpenTelemetry Collector** (`otel/opentelemetry-collector-contrib`)
Recebe OTLP (gRPC 4317, HTTP 4318), agrupa em lotes e exporta com o exporter `elasticsearch` em `mapping.mode: otel`.
Os índices são fixados em `logs-/traces-/metrics-generic.otel-default`.

**Elasticsearch 8.17** (nó único, segurança desligada)
Recebe os três sinais em *data streams* gerenciados com os templates nativos de OTel (disponíveis desde a 8.16).
Guarda também o índice de dados de negócio do seu robô, se houver.

**dash-obskitpy** (opcional, `--local dash-obskitpy`)
Container `nginx` que serve uma página estática (HTML/CSS/JS puros) e expõe ao navegador só a leitura do
Elasticsearch (`_search`, `_count`, `_field_caps`) em `/es/`. É a alternativa leve ao Kibana; veja
[dash-obskitpy](dash-obskitpy.md). Kibana e dash são *perfis* do Docker Compose, ativados pelo `.env`
(`COMPOSE_PROFILES`), então só sobe o que você escolheu.

**Kibana 8.17** (opcional, `--local kibana`)
Mostra o dashboard. Os objetos (data views, visualizações, buscas, dashboard) são importados pela API de
*saved objects* — o que torna o dashboard um arquivo versionável.

**CLI `obskitpy`** (`obskitpy/src/obskitpy/cli.py`)
Gera os arquivos de `observability/`, chama `docker compose` (projeto nomeado `obskitpy-<servico>`), espera o Kibana
ficar disponível e importa o `dashboard.ndjson`. Usa apenas a biblioteca padrão para falar com o Kibana.

## Decisões de projeto

| Decisão | Motivo |
|---|---|
| OpenTelemetry em vez de agente/SDK próprio | Padrão aberto: você troca o backend sem mexer no código instrumentado. |
| Collector entre o app e o Elasticsearch | O app só conhece OTLP; mapeamento e destino ficam na configuração do Collector. |
| `mapping.mode: otel` + índices fixos | Usa os templates nativos do Elasticsearch; sem os índices explícitos, o exporter roteia por atributos e os sinais caem em data streams errados. |
| Métricas em temporalidade *delta* | O modo OTel do Elasticsearch descarta histogramas cumulativos. A `setup()` já define isso. |
| Dashboard como `ndjson` versionável | Sem passos manuais em ambiente novo; mudanças passam por code review. |
| Um painel base filtrado por `service.name` | Vários serviços podem dividir a mesma stack sem se misturar. |
| Segurança desligada, portas em `127.0.0.1` | Foco em desenvolvimento local; reduz atrito. Veja a seção de segurança do README. |

## Modelo de dados dos sinais

- **Traces**: cada operação é um span; o span raiz representa uma execução. Exceções registradas viram eventos
  (documentos extras sem `name`). `duration` é em nanossegundos.
- **Logs**: `body.text` é a mensagem; `attributes.*` carrega o `extra={...}` do `logging` e os campos `error.*`;
  `trace_id`/`span_id` ligam o log ao span ativo.
- **Métricas**: um documento por instrumento/intervalo; o valor fica em `metrics.<nome>` e as dimensões em
  `attributes.*`.

## Limites conhecidos

- Nó único do Elasticsearch, 1 GB de heap, sem réplicas — suficiente para desenvolvimento, não para produção.
- `error.chain` é um campo `keyword`; cadeias muito longas (> 1024 caracteres) são mantidas no `_source`, mas não
  ficam indexadas para busca.
- O primeiro dado leva alguns segundos para ficar pesquisável (atraso de *refresh* do Elasticsearch).
- Versões fixadas: OpenTelemetry Python 1.27.0, Elasticsearch/Kibana 8.17.0, Collector 0.116.1. Atualize-as juntas.
