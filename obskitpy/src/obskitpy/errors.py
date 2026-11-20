"""Diagnóstico estruturado de exceções: cadeia de chamadas e ponto de origem."""
import json
from pathlib import Path


def error_chain(error: BaseException) -> list[dict]:
    """Um item por frame do traceback, do mais externo ao ponto onde o erro nasceu."""
    chain, tb = [], error.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        chain.append({"funcao": code.co_name, "arquivo": Path(code.co_filename).name, "linha": tb.tb_lineno})
        tb = tb.tb_next
    return chain


def error_attributes(error: BaseException) -> dict:
    """Atributos `error.*` anexados aos logs de erro (usados pelos painéis do dashboard)."""
    chain = error_chain(error)
    attrs = {"error.type": type(error).__name__}
    if chain:
        last = chain[-1]
        attrs["error.origin"] = f"{last['arquivo']}:{last['funcao']}:{last['linha']}"
        attrs["error.chain"] = json.dumps(chain, ensure_ascii=False)
    return attrs
