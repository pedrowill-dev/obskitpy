"""Senhas, sessões assinadas, criptografia de segredos e limite de tentativas de login (só biblioteca padrão + cryptography)."""
import base64
import hashlib
import hmac
import json
import os
import time
from collections import defaultdict, deque

from cryptography.fernet import Fernet, InvalidToken

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT, dklen=32)
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign_key(secret: str) -> bytes:
    return hashlib.sha256(b"session:" + secret.encode()).digest()


def make_token(secret: str, user_id: int, ttl: int) -> str:
    body = _b64(json.dumps({"uid": user_id, "exp": int(time.time()) + ttl}).encode())
    sig = _b64(hmac.new(_sign_key(secret), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(secret: str, token: str) -> int | None:
    """id do usuário se o token é válido e não expirou; senão None."""
    try:
        body, sig = token.split(".")
        good = hmac.new(_sign_key(secret), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(good, _unb64(sig)):
            return None
        data = json.loads(_unb64(body))
        return int(data["uid"]) if data["exp"] > time.time() else None
    except (ValueError, KeyError, TypeError):
        return None


def _fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"vault:" + secret.encode()).digest()))


def encrypt_secrets(secret: str, values: dict) -> str:
    return _fernet(secret).encrypt(json.dumps(values).encode()).decode()


def decrypt_secrets(secret: str, token: str) -> dict:
    try:
        return json.loads(_fernet(secret).decrypt(token.encode()))
    except (InvalidToken, ValueError):
        raise ValueError("não foi possível descriptografar os segredos (OBSKITPY_SECRET_KEY mudou?)")


class LoginLimiter:
    """Máximo de tentativas por chave (e-mail + IP) numa janela de tempo."""

    def __init__(self, attempts: int = 5, window: int = 60):
        self.attempts, self.window = attempts, window
        self.hits: dict[str, deque] = defaultdict(deque)

    def blocked(self, key: str) -> bool:
        q, now = self.hits[key], time.time()
        while q and q[0] < now - self.window:
            q.popleft()
        return len(q) >= self.attempts

    def fail(self, key: str) -> None:
        self.hits[key].append(time.time())

    def reset(self, key: str) -> None:
        self.hits.pop(key, None)
