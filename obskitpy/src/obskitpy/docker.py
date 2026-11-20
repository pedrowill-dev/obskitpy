"""Docker sem dor de cabeça: acha o Compose, liga o Colima, contorna credenciais e portas ocupadas.

Regra de ouro: em vez de falhar, tentar resolver. Correções seguras (variável de ambiente, configuração
temporária, trocar de porta) são automáticas; instalar ou iniciar algo só com a sua confirmação.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import ui


class DockerProblem(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


_compose: list[str] | None = None
COLIMA_SOCK = Path.home() / ".colima" / "default" / "docker.sock"


def run(cmd: list[str], timeout: int = 20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _ok(cmd: list[str]) -> bool:
    r = run(cmd)
    return bool(r and r.returncode == 0)


# --- Compose -------------------------------------------------------------------------------

def find_compose() -> list[str] | None:
    """`docker compose` (plugin v2) ou o binário `docker-compose` (v2 standalone). None se não houver."""
    global _compose
    if _compose:
        return _compose
    if shutil.which("docker") and _ok(["docker", "compose", "version"]):
        _compose = ["docker", "compose"]
    elif shutil.which("docker-compose") and _ok(["docker-compose", "version"]):
        _compose = ["docker-compose"]
    return _compose


def install_hint() -> str:
    if sys.platform == "darwin":
        return "brew install docker-compose"
    if sys.platform.startswith("linux"):
        return "sudo apt install docker-compose-v2   (ou veja https://docs.docker.com/compose/install/)"
    return "veja https://docs.docker.com/compose/install/"


def ensure_compose() -> list[str]:
    found = find_compose()
    if found:
        return found
    if not shutil.which("docker"):
        raise DockerProblem("O Docker não está instalado.",
                            "Instale o Docker Desktop (https://docker.com/products/docker-desktop) ou, no macOS, `brew install colima docker docker-compose`.")
    if sys.platform == "darwin" and shutil.which("brew") and ui.interactive():
        ui.warn("O Docker Compose v2 não foi encontrado.")
        if ui.confirm("Instalar agora com o Homebrew? (brew install docker-compose)", default=True):
            with ui.Spinner("Instalando o Docker Compose") as sp:
                proc = subprocess.Popen(["brew", "install", "docker-compose"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in proc.stdout:
                    sp.update(line)
                if proc.wait() != 0:
                    sp.fail("Não foi possível instalar")
                    raise DockerProblem("A instalação do Docker Compose falhou.", "Tente manualmente: brew install docker-compose")
                sp.ok("Docker Compose instalado")
            found = find_compose()
            if found:
                return found
    raise DockerProblem("O Docker Compose v2 não foi encontrado.", f"Instale com: {install_hint()}")


# --- daemon --------------------------------------------------------------------------------

def daemon_up() -> bool:
    return _ok(["docker", "info", "--format", "{{.ServerVersion}}"])


def use_colima_socket() -> bool:
    """O Docker CLI não achou o daemon mas o Colima está de pé: aponta DOCKER_HOST para o socket dele."""
    if os.environ.get("DOCKER_HOST") or not COLIMA_SOCK.exists():
        return False
    os.environ["DOCKER_HOST"] = f"unix://{COLIMA_SOCK}"
    if daemon_up():
        return True
    del os.environ["DOCKER_HOST"]
    return False


def ensure_daemon() -> None:
    if daemon_up():
        return
    if use_colima_socket():
        ui.hint(f"Usando o Docker do Colima (DOCKER_HOST={os.environ['DOCKER_HOST']})")
        return
    if shutil.which("colima"):
        ui.warn("O Docker não está rodando.")
        if ui.confirm("Iniciar o Colima agora?", default=True):
            with ui.Spinner("Iniciando o Colima (pode levar ~1 min)") as sp:
                proc = subprocess.Popen(["colima", "start", "--cpu", "4", "--memory", "6", "--disk", "30"],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in proc.stdout:
                    sp.update(line)
                if proc.wait() != 0:
                    sp.fail("O Colima não iniciou")
                    raise DockerProblem("Não foi possível iniciar o Colima.", "Rode `colima start` e veja a mensagem de erro.")
                sp.ok("Colima rodando")
            if daemon_up() or use_colima_socket():
                return
        raise DockerProblem("O Docker não está rodando.", "Inicie com `colima start` (ou abra o Docker Desktop) e rode o comando de novo.")
    raise DockerProblem("O Docker não está rodando.", "Abra o Docker Desktop (ou instale o Colima: `brew install colima`) e rode o comando de novo.")


def ensure_ready() -> list[str]:
    """Garante Docker + Compose funcionando (corrigindo o que for possível). Devolve o comando do compose."""
    base = ensure_compose()
    ensure_daemon()
    return base


# --- correções automáticas para falhas do compose -----------------------------------------

CRED_RE = re.compile(r"docker-credential|error getting credentials|credsStore", re.I)
PORT_RE = re.compile(r"port is already allocated|address already in use|ports are not available|bind for", re.I)


def clean_docker_config() -> Path:
    """Configuração do Docker sem `credsStore` (o helper do Docker Desktop pode não existir), mantendo os plugins."""
    cfg = Path.home() / ".cache" / "obskitpy" / "docker-config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text("{}\n")
    plugins = Path.home() / ".docker" / "cli-plugins"
    link = cfg / "cli-plugins"
    if plugins.exists() and not link.exists():
        link.symlink_to(plugins)
    os.environ["DOCKER_CONFIG"] = str(cfg)
    global _compose
    _compose = None  # redescobre o compose com a nova configuração
    return cfg


def busy_ports(text: str) -> list[int]:
    """Portas citadas em erros de 'porta ocupada' do docker compose."""
    ports = []
    for line in text.splitlines():
        if PORT_RE.search(line):
            ports += [int(p) for p in re.findall(r"(?:127\.0\.0\.1|0\.0\.0\.0|\[::\]|:::?)[: ]*(\d{2,5})\b", line)]
            ports += [int(p) for p in re.findall(r"exposing port TCP [\d.]+:(\d+)", line)]
    return list(dict.fromkeys(ports))
