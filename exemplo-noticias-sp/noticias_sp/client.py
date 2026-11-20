"""Acesso às notícias da Prefeitura de SP.

A página https://prefeitura.sp.gov.br/noticias monta a lista "Todas as notícias" no navegador,
chamando a API pública do Liferay (a mesma que usamos aqui), um dia por vez.
"""
import logging
import time
from datetime import date

import obskitpy
import requests

log = logging.getLogger(__name__)

BASE_URL = "https://prefeitura.sp.gov.br"
STRUCTURE_ID = 79914  # estrutura de conteúdo "Notícia" (vem no JS da página)
SITE_ID = 34276       # site da Prefeitura
HEADERS = {"User-Agent": "noticias-sp-observabilidade/1.0 (projeto de estudo)"}
MAX_ATTEMPTS = 3

fetch_retries = obskitpy.meter("noticias_sp").create_counter(
    "noticias.fetch.retries", unit="{retry}", description="Tentativas repetidas de download")


def fetch_day(day: date) -> list[dict]:
    """Todas as notícias publicadas no dia (fuso -03:00), mais recentes primeiro."""
    params = {
        "pageSize": -1,
        "sort": "datePublished:desc",
        "filter": (f"siteId eq {SITE_ID} and datePublished gt {day}T00:00:00-03:00 "
                   f"and datePublished lt {day}T23:59:59-03:00"),
    }
    url = f"{BASE_URL}/o/headless-delivery/v1.0/content-structures/{STRUCTURE_ID}/structured-contents"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.json()["items"]
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            status = exc.response.status_code if getattr(exc, "response", None) is not None else None
            if (status is not None and status < 500) or attempt == MAX_ATTEMPTS:
                raise
            fetch_retries.add(1, {"error.type": type(exc).__name__})
            log.warning("download falhou, tentando novamente",
                        extra={"dia": str(day), "tentativa": attempt, "error.type": type(exc).__name__})
            time.sleep(2**attempt)
