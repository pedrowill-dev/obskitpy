# Contribuindo

## Ambiente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "./obskitpy[requests,test]"
pip install -r exemplo-noticias-sp/requirements.txt pytest
```

## Testes

```bash
pytest obskitpy/tests                        # biblioteca e CLI (sem Docker)
cd exemplo-noticias-sp && pytest tests     # parser com itens reais da API
```

Os testes não precisam de Docker nem de rede. Para validar de ponta a ponta:

```bash
cd exemplo-noticias-sp
obskitpy up && python -m noticias_sp         # depois confira o dashboard no Kibana
```

## Documentação

O site (HTML/CSS/JS puros, tema escuro) é gerado por `site/build.py` a partir de `docs/*.md` (conteúdo) e
`site/` (layout, `landing.html`, `assets/`) e publicado no GitHub Pages a cada push em `main`.
Para ver localmente:

```bash
uv run --with markdown python site/build.py && python3 -m http.server -d _site 8000
```

Para adicionar uma página: crie `docs/nome.md` e registre-a em `NAV`, no `site/build.py`.

## Convenções

- Mudou algo em `obskitpy/src/obskitpy/templates/`? Rode um `obskitpy init` + `obskitpy up` num projeto novo para validar.
- Mudou a geração do dashboard (`dashboard.py`)? Mantenha os ids estáveis (`<servico>-...`) para que reimportar
  sobrescreva em vez de duplicar, e atualize `docs/dashboards.md`.
- Versões do OpenTelemetry, Elasticsearch, Kibana e Collector são fixadas de propósito; atualize-as em conjunto
  e registre no `docs/arquitetura.md`.
- Documentação em português; código e identificadores públicos da biblioteca em inglês.
