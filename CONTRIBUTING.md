# Contribuindo

## Ambiente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "./obskit[requests,test]"
pip install -r exemplo-noticias-sp/requirements.txt pytest
```

## Testes

```bash
pytest obskit/tests                        # biblioteca e CLI (sem Docker)
cd exemplo-noticias-sp && pytest tests     # parser com itens reais da API
```

Os testes não precisam de Docker nem de rede. Para validar de ponta a ponta:

```bash
cd exemplo-noticias-sp
obskit up && python -m noticias_sp         # depois confira o dashboard no Kibana
```

## Convenções

- Mudou algo em `obskit/src/obskit/templates/`? Rode um `obskit init` + `obskit up` num projeto novo para validar.
- Mudou a geração do dashboard (`dashboard.py`)? Mantenha os ids estáveis (`<servico>-...`) para que reimportar
  sobrescreva em vez de duplicar, e atualize `docs/dashboards.md`.
- Versões do OpenTelemetry, Elasticsearch, Kibana e Collector são fixadas de propósito; atualize-as em conjunto
  e registre no `docs/arquitetura.md`.
- Documentação em português; código e identificadores públicos da biblioteca em inglês.
