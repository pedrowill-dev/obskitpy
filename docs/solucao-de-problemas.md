# Solução de problemas

## `docker compose` falha ou não acha o Docker

- Confirme que o daemon está de pé: `docker info`. No macOS com Colima: `colima start`.
- `unknown command: docker compose` → instale o plugin Compose v2 (ou `brew install docker-compose`).
- `docker-credential-desktop: executable file not found` (Colima sem Docker Desktop): em `~/.docker/config.json`,
  remova a linha `"credsStore": "desktop"`, ou rode com um `DOCKER_CONFIG` apontando para uma pasta com
  `config.json` contendo `{}` (nesse caso, copie/linke o plugin do compose para `$DOCKER_CONFIG/cli-plugins/`).
- Erros `read-only file system` / `input/output error` ao baixar imagens: o disco está cheio. Libere espaço
  (`docker system prune`, caches) e reinicie o Colima.

## O Collector não sobe (`config.yaml: is a directory`)

O Docker não enxergou o arquivo de configuração e criou uma pasta no lugar. No Colima só o `$HOME` é compartilhado:
mova o projeto para dentro dele (ou configure mounts extras no Colima). Depois: `obskitpy down` e `obskitpy up`.

## `obskitpy up` demora ou dá timeout

O Elasticsearch leva 30–60 s para ficar saudável e o Kibana mais um pouco. A CLI espera até 5 minutos.
Se estourar, veja `docker compose -p obskitpy-<servico> logs elasticsearch kibana` (memória insuficiente é a causa
mais comum — dê pelo menos 4 GB à VM do Docker).

## `zsh: command not found: obskitpy`

O executável só existe dentro do ambiente do projeto. Use `uv run obskitpy ...` (uv), ative o venv (pip) ou
`python -m obskitpy ...`. Para usar de qualquer lugar: `uv tool install obskitpy`.

## Atualizar para a versão mais nova

```bash
uv add -U obskitpy          # uv (atualiza o pacote e o uv.lock)
pip install -U obskitpy     # pip
uvx obskitpy@latest init    # sem instalar nada
```

Versões antigas (0.1.x) não têm o assistente, o `run` nem as correções automáticas abaixo.

## `unknown flag: --project-directory` ou `docker: unknown command: docker compose`

O Docker CLI está sem o plugin do Compose v2. Nas versões novas o obskitpy resolve sozinho (usa o binário
`docker-compose` se existir, ou oferece instalar com o Homebrew). Manualmente: `brew install docker-compose`.

## O `obskitpy init` não sobe a stack

Rode `obskitpy doctor`: ele checa Docker, Compose v2, se o daemon responde, a memória disponível para o Docker
(o Elasticsearch pede ~2 GB livres; recomendado ≥ 4 GB) e as portas. O `init` também faz essa checagem e,
se faltar o Docker, cria os arquivos mesmo assim para você subir depois com `obskitpy up`.

## O dash-obskitpy abre mas não mostra dados

- A mensagem **"Não consegui falar com o Elasticsearch"** indica a stack parada: `obskitpy status` e `obskitpy up`.
- **"Ainda sem dados para <serviço>"**: o nome em `obskitpy.setup("...")` precisa ser o `OBSKITPY_SERVICE` do
  `observability/.env`. Rode `python obskitpy_demo.py` (gerado pelo `init --demo`) para ver dados na hora.
- Confira o período no canto superior direito (15 min, 1 h, ...).

## Quero usar a mesma stack em outro projeto

Rode `obskitpy init` na pasta do outro projeto: ele detecta a stack rodando e pergunta se quer incluir. Sem terminal interativo:
`obskitpy init --join` (ou `--join NOME` se houver mais de uma). Se a stack original for apagada ou mudar de pasta,
o projeto avisa; recrie uma própria com `obskitpy init --force --standalone`.

## Quero trocar entre Kibana e dash-obskitpy

`obskitpy init --force --local kibana|dash-obskitpy|ambos` e depois `obskitpy up`. Os dados no Elasticsearch
continuam; só muda o que sobe. (`--force` recria `observability/`: guarde o seu `dashboard.ndjson` se o editou.)

## Porta já em uso

Outra stack (ou outro programa) usa 9200/5601/8600/4317/4318. O `init` já escolhe a próxima porta livre sozinho
(e avisa). Para escolher na mão, derrube a outra (`obskitpy down` nela) ou recrie com portas diferentes: `obskitpy init --force --kibana-port 5700 --es-port 9300 --otlp-grpc-port 4320 --otlp-http-port 4321`
e defina `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4320` no processo Python.

## Os painéis estão vazios

Verifique, nesta ordem:

1. **O programa chamou `shutdown()`?** Sem isso, o último lote pode não ter sido enviado.
2. **Nome do serviço**: o de `obskitpy.setup("...")` deve ser igual a `OBSKITPY_SERVICE` no `.env`.
3. **Janela de tempo** do Kibana: pode estar em 24 h enquanto os dados são mais antigos (ou vice-versa).
4. **Atraso de refresh**: espere ~30 s após o primeiro envio.
5. **O Collector recebeu?** `docker compose -p obskitpy-<servico> logs otel-collector`.
6. **O app consegue alcançar a porta?** Avisos `Transient error StatusCode.UNAVAILABLE ... localhost:4317` no console
   indicam Collector fora do ar ou porta diferente (ajuste `OTEL_EXPORTER_OTLP_ENDPOINT`).
7. **Contagem direta**: `curl 'localhost:9200/logs-generic.otel-default/_count'`.

## O dashboard mostra painéis de outro projeto

O navegador guardou o estado da tela anterior. Recarregue a página (⌘/Ctrl+R) ou abra o link em aba anônima.

## Avisos `Failed to export ... UNAVAILABLE` ao encerrar

O Collector caiu ou o `shutdown()` foi chamado depois do `obskitpy down`. Inofensivo; os dados já enviados estão salvos.

## `ModuleNotFoundError: pkg_resources`

Instale com o extra: `pip install "obskitpy[requests]"` (ele fixa uma versão de `setuptools` compatível com
`opentelemetry-instrumentation-requests` 0.48b0), ou `pip install "setuptools<81"`.

## Python 3.14 e protobuf

Se a instalação ou a importação do OpenTelemetry falhar em Python 3.14 por causa do `protobuf`, use Python 3.10–3.13.

## Recomeçar do zero

```bash
obskitpy down -v          # apaga containers e dados
obskitpy up               # recria tudo e reaplica o dashboard
```
