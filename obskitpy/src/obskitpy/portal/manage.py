"""Administração do portal dentro do container:  python -m obskitpy.portal.manage reset-admin EMAIL SENHA"""
import sys

from sqlalchemy import select

from .db import Base, make_engine, make_session_factory
from .models import User
from .security import hash_password
from .settings import load


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "reset-admin":
        print("uso: python -m obskitpy.portal.manage reset-admin EMAIL SENHA")
        return 2
    email, password = argv[1].lower(), argv[2]
    if len(password) < 10:
        print("a senha precisa ter pelo menos 10 caracteres")
        return 2
    settings = load()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user:
            user.password_hash, user.role, user.active = hash_password(password), "admin", True
        else:
            db.add(User(email=email, name="Administrador", role="admin", password_hash=hash_password(password)))
        db.commit()
    print(f"administrador {email} atualizado")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
