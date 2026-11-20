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
python3 -m venv .venv && source .venv/bin/activate
pip install "obskit[requests] @ git+https://github.com/pedrowill-dev/obskit.git#subdirectory=obskit"
```

## 3. Criar a stack e o dashboard

```bash
obskit init        # nome do serviço = nome da pasta; use --service para escolher
obskit up          # ~1 min na primeira vez (baixa as imagens)
```

O `init` cria a pasta `observability/` no projeto (veja [o que é cada arquivo](guia-obskit.md#o-que-o-init-cria)).
O `up` sobe os containers, espera ficarem saudáveis e importa o dashboard no Kibana. Ao final imprime o link.

Portas diferentes? `obskit init --kibana-port 5700 --es-port 9300 --otlp-grpc-port 4320 --otlp-http-port 4321`.

## 4. Instrumentar o código

```python
import logging, time
import obskit

log = logging.getLogger("pedidos")
shutdown = obskit.setup("meu-servico")        # use o mesmo nome do `init`

@obskit.traced()
def validar(n):
    if n % 3 == 0:
        raise ValueError(f"pedido {n} inválido")

@obskit.traced("processar-lote")
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

> O nome passado a `obskit.setup(...)` precisa ser o mesmo do `OBSKIT_SERVICE` em `observability/.env`,
> pois os painéis filtram por ele.

## 5. Ver o resultado

Rode o script e abra o dashboard (`obskit open` ou o link impresso pelo `up`):

```
http://localhost:5601/app/dashboards#/view/<servico>-observability
```

![Dashboard base com os painéis de saúde](images/dashboard-saude.png)

Os primeiros dados levam alguns segundos para ficar pesquisáveis no Elasticsearch — se os painéis estiverem vazios,
aguarde ~30 s e atualize. Confira também a janela de tempo (canto superior direito): o padrão é *Last 24 hours*.

## 6. Próximos passos

- Adicionar painéis próprios e versioná-los: [Dashboards](dashboards.md)
- Métricas de negócio, atributos nos spans, produção de erros: [Guia do obskit](guia-obskit.md)
- Um projeto real completo: [exemplo das notícias](../exemplo-noticias-sp/README.md)
- Algo deu errado? [Solução de problemas](solucao-de-problemas.md)

## Encerrar

```bash
obskit down        # derruba os containers e mantém os dados
obskit down -v     # derruba e apaga os dados
```
