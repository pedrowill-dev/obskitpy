"""obskitpy: observabilidade (OpenTelemetry + Elasticsearch + Kibana) em qualquer projeto Python."""
import argparse
import re
import shutil
import subprocess
import sys
import webbrowser
from importlib import resources
from pathlib import Path

from . import dashboard, kibana

DEFAULT_DIR = "observability"
DEFAULT_PORTS = {"ES_PORT": 9200, "KIBANA_PORT": 5601, "OTLP_GRPC_PORT": 4317, "OTLP_HTTP_PORT": 4318}
TEMPLATE_FILES = ("docker-compose.yml", "otel-collector-config.yaml")
SNIPPET = '''import obskitpy

shutdown = obskitpy.setup("{service}")   # no início do programa
# ... seu código; use logging normalmente: log.exception("falhou") já vai com error.type/origin/chain
shutdown()                             # ao encerrar, descarrega os dados pendentes'''


class CliError(Exception):
    pass


# --- configuração (.env na pasta observability/) -------------------------------------------

def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise CliError(f"{path} não existe. Rode `obskitpy init` neste projeto primeiro.")
    env = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def kibana_url(env: dict[str, str]) -> str:
    return f"http://localhost:{env['KIBANA_PORT']}"


def dashboard_url(env: dict[str, str]) -> str:
    return f"{kibana_url(env)}/app/dashboards#/view/{dashboard.dashboard_id(env['OBSKITPY_SERVICE'])}"


# --- comandos ------------------------------------------------------------------------------

def cmd_init(args) -> None:
    obs = Path(args.dir)
    service = args.service or re.sub(r"[^a-z0-9-]+", "-", Path.cwd().name.lower()).strip("-") or "app"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", service):
        raise CliError(f"nome de serviço inválido: {service!r} (use letras minúsculas, números e hífen)")
    if (obs / ".env").exists() and not args.force:
        raise CliError(f"{obs}/ já existe. Use --force para sobrescrever (o dashboard.ndjson é perdido).")

    obs.mkdir(parents=True, exist_ok=True)
    for name in TEMPLATE_FILES:
        (obs / name).write_text(resources.files("obskitpy.templates").joinpath(name).read_text())
    ports = {key: getattr(args, key.lower()) for key in DEFAULT_PORTS}
    lines = ["# Gerado pelo obskitpy init; usado pelo docker compose e pela CLI.", f"OBSKITPY_SERVICE={service}"]
    (obs / ".env").write_text("\n".join(lines + [f"{k}={v}" for k, v in ports.items()]) + "\n")
    (obs / "dashboard.ndjson").write_text(dashboard.to_ndjson(dashboard.build(service)), encoding="utf-8")

    print(f"Criado {obs}/ para o serviço '{service}':")
    print("  docker-compose.yml  otel-collector-config.yaml  dashboard.ndjson  .env")
    print("\nPróximos passos:")
    print("  1. pip install obskitpy            # (ou 'obskitpy[requests]' p/ spans HTTP automáticos)")
    print(f"  2. obskitpy up --dir {obs}" if str(obs) != DEFAULT_DIR else "  2. obskitpy up")
    print("  3. No seu código:\n")
    print("     " + SNIPPET.format(service=service).replace("\n", "\n     "))


def compose(obs: Path, env: dict[str, str], *args: str) -> None:
    if shutil.which("docker") is None:
        raise CliError("docker não encontrado no PATH.")
    cmd = ["docker", "compose", "--project-directory", str(obs), "-f", str(obs / "docker-compose.yml"),
           "-p", f"obskitpy-{env['OBSKITPY_SERVICE']}", *args]
    if subprocess.run(cmd).returncode != 0:
        raise CliError("docker compose falhou (o Docker/Colima está rodando?).")


def apply_dashboard(obs: Path, env: dict[str, str]) -> None:
    file = obs / "dashboard.ndjson"
    if not file.exists():
        raise CliError(f"{file} não existe.")
    count = kibana.import_ndjson(kibana_url(env), file.read_text(encoding="utf-8"))
    print(f"Dashboard aplicado ({count} objetos): {dashboard_url(env)}")


def cmd_up(args) -> None:
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    compose(obs, env, "up", "-d", "--wait", "--wait-timeout", "300")
    kibana.wait_ready(kibana_url(env))
    apply_dashboard(obs, env)
    print(f"\nOTLP gRPC: localhost:{env['OTLP_GRPC_PORT']}   Elasticsearch: http://localhost:{env['ES_PORT']}")


def cmd_down(args) -> None:
    obs = Path(args.dir)
    compose(obs, read_env(obs / ".env"), "down", *(["-v"] if args.volumes else []))


def cmd_status(args) -> None:
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    compose(obs, env, "ps")
    print(f"\nDashboard: {dashboard_url(env)}")


def cmd_open(args) -> None:
    url = dashboard_url(read_env(Path(args.dir) / ".env"))
    print(url)
    webbrowser.open(url)


def cmd_dashboard_apply(args) -> None:
    obs = Path(args.dir)
    apply_dashboard(obs, read_env(obs / ".env"))


def cmd_dashboard_export(args) -> None:
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    ndjson = kibana.export_dashboard(kibana_url(env), dashboard.dashboard_id(env["OBSKITPY_SERVICE"]))
    if not ndjson.strip():
        raise CliError("o Kibana não devolveu o dashboard (foi apagado? rode `obskitpy dashboard apply`).")
    (obs / "dashboard.ndjson").write_text(ndjson, encoding="utf-8")
    print(f"{obs}/dashboard.ndjson atualizado ({len(ndjson.splitlines())} objetos). Versione no git.")


# --- parser --------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="obskitpy", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, fn, help_, **kw):
        p = sub.add_parser(name, help=help_, **kw)
        p.add_argument("--dir", default=DEFAULT_DIR, help=f"pasta da stack (padrão: {DEFAULT_DIR})")
        p.set_defaults(fn=fn)
        return p

    init = command("init", cmd_init, "cria a pasta observability/ com stack e dashboard base")
    init.add_argument("--service", help="nome do serviço (padrão: nome da pasta atual)")
    init.add_argument("--force", action="store_true", help="sobrescreve uma instalação existente")
    for key, default in DEFAULT_PORTS.items():
        init.add_argument(f"--{key.lower().replace('_', '-')}", dest=key.lower(), type=int, default=default,
                          help=f"porta local (padrão: {default})")

    command("up", cmd_up, "sobe a stack, espera ficar saudável e aplica o dashboard")
    down = command("down", cmd_down, "derruba a stack")
    down.add_argument("-v", "--volumes", action="store_true", help="apaga também os dados")
    command("status", cmd_status, "mostra os containers e o link do dashboard")
    command("open", cmd_open, "abre o dashboard no navegador")

    dash = sub.add_parser("dashboard", help="sincroniza o dashboard com o Kibana")
    dsub = dash.add_subparsers(dest="action", required=True)
    for name, fn, help_ in (("apply", cmd_dashboard_apply, "importa observability/dashboard.ndjson no Kibana"),
                            ("export", cmd_dashboard_export, "grava o dashboard do Kibana (com suas edições) no arquivo")):
        p = dsub.add_parser(name, help=help_)
        p.add_argument("--dir", default=DEFAULT_DIR)
        p.set_defaults(fn=fn)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (CliError, kibana.KibanaError) as exc:
        sys.exit(f"erro: {exc}")


if __name__ == "__main__":
    main()
