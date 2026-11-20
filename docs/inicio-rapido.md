# Início rápido

Objetivo: ver logs, traces e erros do **seu** código num dashboard do Kibana em cerca de 5 minutos.

## 1. Pré-requisitos

- Python 3.10+
- Docker rodando (`docker info` sem erro) com Compose v2 (`docker compose version`)
- Portas livres em `127.0.0.1`: 9200 (Elasticsearch), 5601 (Kibana), 4317 e 4318 (OTLP).
  Se alguma estiver ocupada, escolha outra no `init` (passo 3).

No macOS com Colima: `colima start --cpu 4 --memory 6 --disk 30`.
O projeto precisa estar **dentro do seu `$HOME`** (o Colima só compartilha essa pasta com o Docker).

## 2. Instalar

```bash
cd meu-projeto
uv init && uv add "obskitpy[requests]"      # com uv (recomendado)
# ou: pip install "obskitpy[requests]"
# versão em desenvolvimento: uv add "obskitpy[requests] @ git+https://github.com/pedrowill-dev/obskitpy.git#subdirectory=obskitpy"
```

## 3. Criar a stack e o dashboard

> Com uv, prefixe os comandos com `uv run` (ex.: `uv run obskitpy init`); com pip, ative o venv antes.

```bash
obskitpy init        # nome do serviço = nome da pasta; use --service para escolher
obskitpy up          # ~1 min na primeira vez (baixa as imagens)
```

O `init` cria a pasta `observability/` no projeto (veja [o que é cada arquivo](guia-obskitpy.md#o-que-o-init-cria)).
O `up` sobe os containers, espera ficarem saudáveis e importa o dashboard no Kibana. Ao final imprime o link.

Portas diferentes? `obskitpy init --kibana-port 5700 --es-port 9300 --otlp-grpc-port 4320 --otlp-http-port 4321`.

## 4. Instrumentar o código

```python
import logging, time
import obskitpy

log = logging.getLogger("pedidos")
shutdown = obskitpy.setup("meu-servico")        # use o mesmo nome do `init`

@obskitpy.traced()
def validar(n):
    if n % 3 == 0:
        raise ValueError(f"pedido {n} inválido")

@obskitpy.traced("processar-lote")
def lote():
    for n in range(1, 10):
        try:
            validar(n)
            log.info("pedido ok", extra={"pedido": n})
        except ValueError:
            log.exception("falha no pedido")
        time.sleep(0.1)

lote()
shutdown()      # sempre chame antes de encerrar: descarrega logs/traces/métricas pendentes
```

> O nome passado a `obskitpy.setup(...)` precisa ser o mesmo do `OBSKITPY_SERVICE` em `observability/.env`,
> pois os painéis filtram por ele.

## 5. Ver o resultado

Rode o script e abra o dashboard (`obskitpy open` ou o link impresso pelo `up`):

```
http://localhost:5601/app/dashboards#/view/<servico>-observability
```

![Dashboard base com os painéis de saúde](images/dashboard-saude.png)

Os primeiros dados levam alguns segundos para ficar pesquisáveis no Elasticsearch — se os painéis estiverem vazios,
aguarde ~30 s e atualize. Confira também a janela de tempo (canto superior direito): o padrão é *Last 24 hours*.

## 6. Próximos passos

- Adicionar painéis próprios e versioná-los: [Dashboards](dashboards.md)
- Métricas de negócio, atributos nos spans, produção de erros: [Guia do obskitpy](guia-obskitpy.md)
- Um projeto real completo: [exemplo das notícias](../exemplo-noticias-sp/README.md)
- Algo deu errado? [Solução de problemas](solucao-de-problemas.md)

## Encerrar

```bash
obskitpy down        # derruba os containers e mantém os dados
obskitpy down -v     # derruba e apaga os dados
```
