"""Exemplo gerado pelo obskitpy: emite logs, traces, métricas e alguns erros para você ver o dashboard.

    python obskitpy_demo.py

Pode apagar este arquivo quando quiser; ele não faz parte do seu projeto.
"""
import logging
import random
import time

import obskitpy

log = logging.getLogger("demo")
shutdown = obskitpy.setup("__SERVICE__")
processados = obskitpy.meter("demo").create_counter("pedidos.processados", unit="{pedido}")


@obskitpy.traced("validar")
def validar(pedido: int) -> None:
    if pedido % 5 == 0:
        raise ValueError(f"pedido {pedido} inválido")


@obskitpy.traced("salvar")
def salvar(pedido: int) -> None:
    time.sleep(random.uniform(0.02, 0.15))  # simula trabalho


@obskitpy.traced("processar-lote")
def lote(total: int = 20) -> None:
    for pedido in range(1, total + 1):
        try:
            validar(pedido)
            salvar(pedido)
            log.info("pedido ok", extra={"pedido": pedido})
            processados.add(1, {"status": "success"})
        except ValueError:
            log.exception("falha no pedido", extra={"pedido": pedido})  # error.type/origin/chain automáticos
            processados.add(1, {"status": "failure"})


if __name__ == "__main__":
    lote()
    shutdown()  # descarrega os dados pendentes
