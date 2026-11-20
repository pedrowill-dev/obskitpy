
import inspect
import traceback
from pathlib import Path


def extrair_cadeia_erro(erro: Exception):
    cadeia = []
    tb = erro.__traceback__

    while tb is not None:
        frame = tb.tb_frame
        codigo = frame.f_code

        try:
            linhas, inicio = inspect.getsourcelines(codigo)
            fonte = "".join(linhas)
        except (OSError, TypeError):
            inicio = None
            fonte = None

        cadeia.append({
            "funcao": codigo.co_name,
            "arquivo": str(
                Path(codigo.co_filename).resolve()
            ),
            "linha_erro": tb.tb_lineno,
            "linha_inicio": inicio,
            "codigo_fonte": fonte,
        })

        tb = tb.tb_next

    return {
        "tipo_erro": type(erro).__name__,
        "mensagem": str(erro),
        "traceback": "".join(
            traceback.format_exception(erro)
        ),
        "cadeia": cadeia,
    }


def buscar_cliente(cliente_id):
    return {"id": cliente_id, "email": None}

def normalizar_email(email):
    return email.lower()

def transformar_cliente(cliente):
    return {
        "id": cliente["id"],
        "email": normalizar_email(cliente["email"])
    }

def processar_cliente(cliente_id):
    cliente = buscar_cliente(cliente_id)
    return transformar_cliente(cliente)

def executar_rpa():
    return processar_cliente(123)



try:
    executar_rpa()

except Exception as erro:
    diagnostico = extrair_cadeia_erro(erro)

    for item in diagnostico["cadeia"]:
        print(
            item["funcao"],
            item["arquivo"],
            item["linha_erro"]
        )