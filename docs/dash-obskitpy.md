# dash-obskitpy

O **dash-obskitpy** é o dashboard próprio do obskitpy: leve, direto ao ponto e sem Kibana. Ele roda como um
container `nginx` que serve uma página estática (HTML, CSS e JavaScript puros) e conversa com o Elasticsearch
por um proxy **somente leitura**.

![Visão geral do dash-obskitpy](images/dash-overview.png)

## Escolhendo onde visualizar

Você decide no `obskitpy init`, pelo menu ou pela flag `--local`:

| `--local` | O que sobe | Quando usar |
|---|---|---|
| `kibana` (padrão sem terminal) | Elasticsearch, Collector, Kibana | Explorar a fundo, montar painéis próprios e consultas livres. |
| `dash-obskitpy` | Elasticsearch, Collector, dash | O dia a dia: ver erros, tempos e execuções logo, gastando menos memória e subindo mais rápido. |
| `ambos` | Tudo | dash para olhar rápido e Kibana para investigar a fundo. O dash ganha um atalho "Kibana ↗". |

```bash
obskitpy init --local dash-obskitpy
obskitpy up
obskitpy open        # abre em http://localhost:8600
```

Mudou de ideia? `obskitpy init --force --local ambos` recria a configuração (os dados no Elasticsearch continuam).

## Período, atualização e Realtime

Tudo isso fica no topo da página, e o dash lembra a sua escolha.

| Controle | O que faz |
|---|---|
| **Período** | Quanto tempo para trás mostrar. Atalhos (5 min, 15 min, 1 h, 6 h, 24 h, 7 d) ou **personalizado**: digite o número e escolha **segundos, minutos, horas ou dias** (ex.: últimos 90 segundos, ou 36 horas). |
| **Atualizar** | De quanto em quanto tempo a tela se atualiza: atalhos (1 s, 5 s, 10 s, 30 s, 1 min, 5 min) ou **personalizado** em **segundos, minutos ou horas**. Dá para desligar a atualização automática (modo manual); o botão ⟳ atualiza na hora. |
| **Realtime** | O atalho para quando você precisa ver **na hora**. |

### Realtime

Um clique em **Realtime** (ou a tecla **R**) liga o modo ao vivo: janela dos **últimos 5 minutos**, atualização
**a cada 1 segundo**, o selo vermelho **AO VIVO** no topo e, na aba **Logs**, as linhas novas piscam em verde ao chegar.
Ao desligar, o dash volta ao período e à atualização que você tinha antes (ou você mesmo ajusta o período
enquanto está ao vivo).

![Realtime no dash-obskitpy](images/dash-realtime.png)

- **Abrir já ao vivo:** `obskitpy open --realtime` (ou o endereço `http://localhost:8600/#logs?realtime`).
- **Detalhe aberto:** se você abrir o detalhe de um log, a atualização pausa para a linha não sumir
  ("⏸ pausado"). Feche o detalhe para voltar ao vivo.
- **Latência:** o obskitpy envia os dados a cada 1 s e o Collector entrega ao Elasticsearch a cada 1 s, então
  um log aparece na tela em poucos segundos. Ajuste com `obskitpy.setup(..., flush_interval_ms=...)`.
- **Aba escondida:** o navegador pausa a atualização quando a aba não está visível.

!!! tip "Quando usar"
    Realtime é para acompanhar um robô rodando agora, depurar uma execução ou ver se um deploy quebrou algo.
    Para olhar o dia ou a semana, use períodos maiores e atualização mais calma.

## O que o dash mostra

### Visão geral

Contadores (logs, erros, avisos, execuções, execuções com erro, duração média), **logs por severidade** ao longo
do tempo, **erros por tipo e origem** (`arquivo:função:linha`) e as **operações por duração média**.
Clique num erro para abrir os logs já filtrados.

### Logs

Os 100 registros mais recentes com filtro por nível (ERROR, WARN, INFO, DEBUG) e busca por texto na mensagem.
Cole um `trace_id` na busca para ver só os logs daquela execução. Clique numa linha para abrir os atributos,
o stack trace e o botão **Ver trace completo**.

### Traces

As execuções recentes (spans raiz) e, ao clicar numa delas, o **waterfall**: cada span na ordem da hierarquia,
com a barra de duração na linha do tempo e as falhas em vermelho.

![Waterfall de uma execução no dash-obskitpy](images/dash-traces.png)

O endereço guarda o trace (`#traces/<trace_id>`), então dá para compartilhar o link com quem usa a mesma máquina.

### Métricas

Escolha qualquer métrica criada com `obskitpy.meter(...)`; o gráfico soma os valores no tempo, separados pelo
atributo `status` quando existir (`success` e `failure`, por exemplo).

## Como funciona

```
navegador ──▶ nginx :8600 ──▶ /            página estática (dash/)
                         └──▶ /es/...      proxy ──▶ Elasticsearch (só _search, _count e _field_caps)
```

- Nada de backend próprio: a página faz as consultas de agregação direto no Elasticsearch.
- O `nginx.conf` libera apenas `_search`, `_count` e `_field_caps`; qualquer outra rota de `/es/` responde 403.
- A porta é publicada só em `127.0.0.1`, como o resto da stack.

## Personalizando

Os arquivos ficam em `observability/dash/` e são seus (versione no git): `index.html`, `style.css`, `app.js`
e `config.js` (que traz o nome do serviço). Edite à vontade; o `nginx` serve a pasta como está, então basta
recarregar a página. Para receber a versão mais nova do dash que vem no pacote (guardando a sua em `.bak`):
`obskitpy dash update`.

!!! note "Qual escolher?"
    Está começando ou só quer saber se o robô está bem? Use o **dash-obskitpy**.
    Precisa cruzar dados, montar visualizações novas ou guardar dashboards versionados? Use o **Kibana**
    (veja [Dashboards](dashboards.md)).
