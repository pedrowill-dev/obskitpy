"""Explicação do erro por IA (a de preferência de quem instala: Gemini, Claude ou qualquer API compatível com OpenAI), pensada para gastar o mínimo de tokens.

Estratégias, da que mais economiza para a que menos:
1. Só roda se alguma mensagem usa `{{explicacao}}` (e se houver erro com traceback).
2. Cache por assinatura do erro (tipo + origem, com números/ids trocados por #): o mesmo erro nunca é explicado duas vezes em 7 dias,
   inclusive nos lembretes e nos testes.
3. Entrada mínima: a linha do erro + as últimas linhas do traceback (limite de caracteres), nunca o log inteiro.
4. Saída curta: até ~40 palavras, `maxOutputTokens` baixo, raciocínio mínimo e sem criatividade.
5. Teto diário de chamadas (falhas contam); passou do teto ou a IA falhou, o aviso sai igual, só sem a explicação.
6. Falhou? Não tenta de novo por 10 minutos (evita tempestade de chamadas com a API fora do ar).
"""
import hashlib
import logging
import re
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AiExplanation, now

log = logging.getLogger("obskitpy.ai")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "anthropic": "claude-haiku-5-5", "openai": "gpt-4.1-nano"}
TTL_OK, TTL_FAIL = timedelta(days=7), timedelta(minutes=10)
TAIL_LINES, TAIL_CHARS, MAX_OUTPUT_TOKENS = 6, 600, 120
INSTRUCTION = ("Você explica erros de automações Python para quem opera o sistema. Responda em português, em no máximo 2 frases "
               "(até 40 palavras): a causa provável e o que fazer. Sem markdown, sem repetir o erro.")


def signature(error: dict) -> str:
    normalized = re.sub(r"0x[0-9a-fA-F]+|\d+", "#", error.get("erro", ""))
    return hashlib.sha1(f"{normalized}|{error.get('origem', '')}".encode()).hexdigest()


def compact(error: dict) -> str:
    tail = "\n".join((error.get("trace") or "").splitlines()[-TAIL_LINES:])[-TAIL_CHARS:]
    return f"Erro: {error.get('erro', '')}\nOnde: {error.get('origem', '')}\n{tail}".strip()


async def _ask(settings, prompt: str) -> str:
    """Uma chamada à IA escolhida (gemini | anthropic | openai, que serve também Groq, OpenRouter, Ollama…)."""
    provider, model, key = settings.ai_provider, settings.ai_model or DEFAULT_MODELS[settings.ai_provider], settings.ai_key
    async with httpx.AsyncClient(timeout=10) as client:
        if provider == "gemini":
            resp = await client.post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": key}, json={
                "systemInstruction": {"parts": [{"text": INSTRUCTION}]}, "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": MAX_OUTPUT_TOKENS, "thinkingConfig": {"thinkingLevel": "minimal"}}})
            resp.raise_for_status()
            return "".join(p.get("text", "") for p in resp.json()["candidates"][0]["content"]["parts"])
        if provider == "anthropic":
            resp = await client.post(ANTHROPIC_URL, headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, json={
                "model": model, "max_tokens": MAX_OUTPUT_TOKENS, "temperature": 0, "system": INSTRUCTION,
                "messages": [{"role": "user", "content": prompt}]})
            resp.raise_for_status()
            return "".join(b.get("text", "") for b in resp.json()["content"])
        base = (settings.ai_url or OPENAI_URL).rstrip("/")  # openai e compatíveis: o endereço é configurável
        resp = await client.post(f"{base}/chat/completions", headers={"authorization": f"Bearer {key}"} if key else {}, json={
            "model": model, "max_tokens": MAX_OUTPUT_TOKENS, "temperature": 0,
            "messages": [{"role": "system", "content": INSTRUCTION}, {"role": "user", "content": prompt}]})
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"] or ""


async def explain(db: Session, settings, error: dict) -> str:
    """Devolve a explicação (do cache ou da IA). Vazio se não deu: nunca levanta erro."""
    if not settings.ai_enabled or not error.get("erro"):
        return ""
    sig, stamp = signature(error), now()
    row = db.get(AiExplanation, sig)
    if row and stamp - _aware(row.created_at) < (TTL_OK if row.text else TTL_FAIL):
        return row.text
    used = db.scalar(select(func.count(AiExplanation.signature)).where(AiExplanation.created_at > stamp - timedelta(days=1)))
    if used >= settings.ai_daily_cap:
        return ""
    text = ""
    try:
        text = (await _ask(settings, compact(error))).strip()[:400]
    except Exception as exc:  # a chave nunca vai para o log
        log.warning("não consegui explicar o erro (%s)", type(exc).__name__)
    if row:
        row.text, row.created_at = text, stamp
    else:
        db.add(AiExplanation(signature=sig, text=text, created_at=stamp))
    db.commit()
    return text


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=now().tzinfo)
