<p align="center"><img src="https://raw.githubusercontent.com/pedrowill-dev/obskitpy/main/docs/images/logo.png" alt="obskitpy" width="380"></p>

# obskitpy

Observabilidade (OpenTelemetry → Elasticsearch → Kibana) em qualquer projeto Python, via CLI.

> Documentação completa na raiz do repositório: [`docs/`](../docs/inicio-rapido.md). Este arquivo é um resumo.

## Começar (2 comandos)

```bash
pip install obskitpy            # 'obskitpy[requests]' adiciona spans HTTP automáticos
cd meu-projeto
obskitpy init                   # cria observability/ (stack + dashboard base); --service NOME opcional
obskitpy up                     # sobe ES + Kibana + Collector e cria o dashboard
```

No código:

```python
import logging, obskitpy

log = logging.getLogger(__name__)
shutdown = obskitpy.setup("meu-servico")     # logs + traces + métricas via OTLP (localhost:4317)

@obskitpy.traced()                            # um span por chamada, marca erro se a exceção propagar
def processar(item): ...

try:
    processar(x)
except Exception:
    log.exception("falhou")                 # já sai com error.type, error.origin e error.chain

shutdown()                                  # descarrega os dados pendentes
```

Métricas próprias: `obskitpy.meter().create_counter("pedidos.processados")` (API do OpenTelemetry).

## Comandos

| Comando | O que faz |
|---|---|
| `obskitpy init [--service N] [--kibana-port P ...]` | cria `observability/` (compose, collector, `dashboard.ndjson`, `.env`) |
| `obskitpy up` | sobe a stack, espera ficar saudável e **aplica o dashboard** |
| `obskitpy down [-v]` | derruba (`-v` apaga os dados) |
| `obskitpy status` / `obskitpy open` | containers + link / abre o dashboard |
| `obskitpy dashboard export` | grava no arquivo o dashboard **como está no Kibana** |
| `obskitpy dashboard apply` | importa `observability/dashboard.ndjson` no Kibana |

Todos aceitam `--dir` (padrão `observability`).

## Evoluindo o dashboard

1. `obskitpy up` já cria o dashboard `<servico>-observability` com os painéis básicos: logs, erros, execuções (traces), duração de spans, erros por tipo/origem e lista de erros com `trace_id`.
2. Edite no Kibana (adicione painéis, métricas do seu domínio, filtros).
3. `obskitpy dashboard export` e versione `observability/dashboard.ndjson` no git.
4. Em outra máquina/CI: `obskitpy up` (ou `obskitpy dashboard apply`) recria tudo.

Os painéis filtram por `resource.attributes.service.name`, então vários serviços podem compartilhar uma stack (use o mesmo `--dir`/portas) ou ter uma cada (use portas diferentes no `init`).

## Observações

- Ambiente local: segurança do Elasticsearch desligada, portas só em `127.0.0.1`.
- Os dados demoram alguns segundos para ficar pesquisáveis após o primeiro envio.
- Colima/Docker: o `docker compose` precisa enxergar a pasta do projeto (no Colima, só `$HOME` é montado por padrão).
- Requer Docker com o plugin Compose v2.
