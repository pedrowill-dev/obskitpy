"""Diagnóstico estruturado de exceções: a cadeia de chamadas até o ponto da falha."""
from pathlib import Path


def extract_error_chain(error: BaseException) -> list[dict]:
    """Um item por frame do traceback, do mais externo ao ponto onde o erro nasceu."""
    chain = []
    tb = error.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        chain.append(
            {
                "funcao": code.co_name,
                "arquivo": Path(code.co_filename).name,
                "linha": tb.tb_lineno,
            }
        )
        tb = tb.tb_next
    return chain
