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
mova o projeto para dentro dele (ou configure mounts extras no Colima). Depois: `obskit down` e `obskit up`.

## `obskit up` demora ou dá timeout

O Elasticsearch leva 30–60 s para ficar saudável e o Kibana mais um pouco. A CLI espera até 5 minutos.
Se estourar, veja `docker compose -p obskit-<servico> logs elasticsearch kibana` (memória insuficiente é a causa
mais comum — dê pelo menos 4 GB à VM do Docker).

## Porta já em uso

Outra stack (ou outro programa) usa 9200/5601/4317/4318. Derrube a outra (`obskit down` nela) ou recrie com portas
diferentes: `obskit init --force --kibana-port 5700 --es-port 9300 --otlp-grpc-port 4320 --otlp-http-port 4321`
e defina `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4320` no processo Python.

## Os painéis estão vazios

Verifique, nesta ordem:

1. **O programa chamou `shutdown()`?** Sem isso, o último lote pode não ter sido enviado.
2. **Nome do serviço**: o de `obskit.setup("...")` deve ser igual a `OBSKIT_SERVICE` no `.env`.
3. **Janela de tempo** do Kibana: pode estar em 24 h enquanto os dados são mais antigos (ou vice-versa).
4. **Atraso de refresh**: espere ~30 s após o primeiro envio.
5. **O Collector recebeu?** `docker compose -p obskit-<servico> logs otel-collector`.
6. **O app consegue alcançar a porta?** Avisos `Transient error StatusCode.UNAVAILABLE ... localhost:4317` no console
   indicam Collector fora do ar ou porta diferente (ajuste `OTEL_EXPORTER_OTLP_ENDPOINT`).
7. **Contagem direta**: `curl 'localhost:9200/logs-generic.otel-default/_count'`.

## O dashboard mostra painéis de outro projeto

O navegador guardou o estado da tela anterior. Recarregue a página (⌘/Ctrl+R) ou abra o link em aba anônima.

## Avisos `Failed to export ... UNAVAILABLE` ao encerrar

O Collector caiu ou o `shutdown()` foi chamado depois do `obskit down`. Inofensivo; os dados já enviados estão salvos.

## `ModuleNotFoundError: pkg_resources`

Instale com o extra: `pip install "obskit[requests]"` (ele fixa uma versão de `setuptools` compatível com
`opentelemetry-instrumentation-requests` 0.48b0), ou `pip install "setuptools<81"`.

## Python 3.14 e protobuf

Se a instalação ou a importação do OpenTelemetry falhar em Python 3.14 por causa do `protobuf`, use Python 3.10–3.13.

## Recomeçar do zero

```bash
obskit down -v          # apaga containers e dados
obskit up               # recria tudo e reaplica o dashboard
```
