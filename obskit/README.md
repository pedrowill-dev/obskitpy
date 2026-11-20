# obskit

Observabilidade (OpenTelemetry → Elasticsearch → Kibana) em qualquer projeto Python, via CLI.

> Documentação completa na raiz do repositório: [`docs/`](../docs/inicio-rapido.md). Este arquivo é um resumo.

## Começar (2 comandos)

```bash
pip install obskit            # 'obskit[requests]' adiciona spans HTTP automáticos
cd meu-projeto
obskit init                   # cria observability/ (stack + dashboard base); --service NOME opcional
obskit up                     # sobe ES + Kibana + Collector e cria o dashboard
```

No código:

```python
import logging, obskit

log = logging.getLogger(__name__)
shutdown = obskit.setup("meu-servico")     # logs + traces + métricas via OTLP (localhost:4317)

@obskit.traced()                            # um span por chamada, marca erro se a exceção propagar
def processar(item): ...

try:
    processar(x)
except Exception:
    log.exception("falhou")                 # já sai com error.type, error.origin e error.chain

shutdown()                                  # descarrega os dados pendentes
```

Métricas próprias: `obskit.meter().create_counter("pedidos.processados")` (API do OpenTelemetry).

## Comandos

| Comando | O que faz |
|---|---|
| `obskit init [--service N] [--kibana-port P ...]` | cria `observability/` (compose, collector, `dashboard.ndjson`, `.env`) |
| `obskit up` | sobe a stack, espera ficar saudável e **aplica o dashboard** |
| `obskit down [-v]` | derruba (`-v` apaga os dados) |
| `obskit status` / `obskit open` | containers + link / abre o dashboard |
| `obskit dashboard export` | grava no arquivo o dashboard **como está no Kibana** |
| `obskit dashboard apply` | importa `observability/dashboard.ndjson` no Kibana |

Todos aceitam `--dir` (padrão `observability`).

## Evoluindo o dashboard

1. `obskit up` já cria o dashboard `<servico>-observability` com os painéis básicos: logs, erros, execuções (traces), duração de spans, erros por tipo/origem e lista de erros com `trace_id`.
2. Edite no Kibana (adicione painéis, métricas do seu domínio, filtros).
3. `obskit dashboard export` e versione `observability/dashboard.ndjson` no git.
4. Em outra máquina/CI: `obskit up` (ou `obskit dashboard apply`) recria tudo.

Os painéis filtram por `resource.attributes.service.name`, então vários serviços podem compartilhar uma stack (use o mesmo `--dir`/portas) ou ter uma cada (use portas diferentes no `init`).

## Observações

- Ambiente local: segurança do Elasticsearch desligada, portas só em `127.0.0.1`.
- Os dados demoram alguns segundos para ficar pesquisáveis após o primeiro envio.
- Colima/Docker: o `docker compose` precisa enxergar a pasta do projeto (no Colima, só `$HOME` é montado por padrão).
- Requer Docker com o plugin Compose v2.
