"""obskitpy: observabilidade (OpenTelemetry + Elasticsearch + Kibana) em qualquer projeto Python."""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from importlib import resources
from pathlib import Path

from . import __version__, dashboard, kibana, ui

DEFAULT_DIR = "observability"
DEFAULT_PORTS = {"ES_PORT": 9200, "KIBANA_PORT": 5601, "DASH_PORT": 8600, "OTLP_GRPC_PORT": 4317, "OTLP_HTTP_PORT": 4318}
PORT_LABELS = {"ES_PORT": "Elasticsearch", "KIBANA_PORT": "Kibana", "DASH_PORT": "dash-obskitpy",
               "OTLP_GRPC_PORT": "OTLP gRPC", "OTLP_HTTP_PORT": "OTLP HTTP"}
TEMPLATE_FILES = ("docker-compose.yml", "otel-collector-config.yaml")
LOCAL_CHOICES = ("kibana", "dash-obskitpy", "ambos")
LOCAL_MENU = [
    ("kibana", "Kibana", "completo: explore logs, traces e métricas a fundo e monte seus próprios painéis"),
    ("dash-obskitpy", "dash-obskitpy", "leve e direto ao ponto: dashboard próprio do obskitpy, sem Kibana (sobe mais rápido)"),
    ("ambos", "Os dois", "dash-obskitpy para o dia a dia e Kibana para investigar a fundo"),
]
DEMO_FILE = "obskitpy_demo.py"


class CliError(Exception):
    def __init__(self, message: str, detail: str = "", hint: str = ""):
        super().__init__(message)
        self.detail, self.hint = detail, hint


# --- configuração (.env na pasta observability/) -------------------------------------------

def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise CliError(f"{path} não existe.", hint="Rode `obskitpy init` neste projeto primeiro.")
    env = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def uses_kibana(env: dict[str, str]) -> bool:
    return env.get("OBSKITPY_LOCAL", "kibana") in ("kibana", "ambos")


def uses_dash(env: dict[str, str]) -> bool:
    return env.get("OBSKITPY_LOCAL", "kibana") in ("dash-obskitpy", "ambos")


def profiles_for(local: str) -> str:
    return {"kibana": "kibana", "dash-obskitpy": "dash", "ambos": "kibana,dash"}[local]


def ports_for(local: str) -> list[str]:
    keys = ["ES_PORT", "OTLP_GRPC_PORT", "OTLP_HTTP_PORT"]
    if local in ("kibana", "ambos"):
        keys.append("KIBANA_PORT")
    if local in ("dash-obskitpy", "ambos"):
        keys.append("DASH_PORT")
    return keys


def kibana_url(env: dict[str, str]) -> str:
    return f"http://localhost:{env['KIBANA_PORT']}"


def dashboard_url(env: dict[str, str]) -> str:
    return f"{kibana_url(env)}/app/dashboards#/view/{dashboard.dashboard_id(env['OBSKITPY_SERVICE'])}"


def dash_url(env: dict[str, str]) -> str:
    return f"http://localhost:{env.get('DASH_PORT', '8600')}"


def primary_url(env: dict[str, str]) -> str:
    return dash_url(env) if uses_dash(env) else dashboard_url(env)


def need_kibana(env: dict[str, str]) -> None:
    if not uses_kibana(env):
        raise CliError("este projeto usa só o dash-obskitpy, que não tem dashboard do Kibana para importar/exportar.",
                       hint="Recrie com `obskitpy init --force --local ambos` se quiser o Kibana também.")


# --- ambiente ------------------------------------------------------------------------------

def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def next_free_port(start: int, avoid: set[int] = frozenset()) -> int:
    return next((p for p in range(start, start + 100) if p not in avoid and port_free(p)), start)


def _run(cmd: list[str], timeout: int = 15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def doctor_checks(ports: dict[str, int] | None = None) -> list[tuple[str, str, str]]:
    """[(nome, 'ok'|'warn'|'fail', detalhe)]"""
    checks = [("Python", "ok", sys.version.split()[0])]
    docker = shutil.which("docker")
    if not docker:
        checks.append(("Docker", "fail", "não encontrado no PATH — instale o Docker Desktop ou o Colima"))
        return checks
    checks.append(("Docker", "ok", docker))
    r = _run(["docker", "compose", "version", "--short"])
    if r and r.returncode == 0:
        checks.append(("Docker Compose v2", "ok", r.stdout.strip()))
    else:
        checks.append(("Docker Compose v2", "fail", "plugin `docker compose` ausente (brew install docker-compose)"))
    r = _run(["docker", "info", "--format", "{{.ServerVersion}}|{{.MemTotal}}"])
    if r and r.returncode == 0 and "|" in r.stdout:
        version, _, mem = r.stdout.strip().partition("|")
        checks.append(("Docker em execução", "ok", f"engine {version}"))
        try:
            gb = int(mem) / 1024**3
            if gb < 3.5:
                checks.append(("Memória do Docker", "warn", f"{gb:.1f} GB — o Elasticsearch pede ~2 GB livres; recomendado ≥ 4 GB"))
            else:
                checks.append(("Memória do Docker", "ok", f"{gb:.1f} GB"))
        except ValueError:
            pass
    else:
        checks.append(("Docker em execução", "fail", "o daemon não responde — inicie o Docker Desktop ou rode `colima start`"))
    for key, port in (ports or {k: DEFAULT_PORTS[k] for k in ports_for("kibana")}).items():
        if port_free(port):
            checks.append((f"Porta {port} ({PORT_LABELS[key]})", "ok", "livre"))
        else:
            checks.append((f"Porta {port} ({PORT_LABELS[key]})", "warn", "em uso (talvez pela própria stack)"))
    return checks


def print_checks(checks: list[tuple[str, str, str]], only_problems: bool = False) -> None:
    for name, status, detail in checks:
        if only_problems and status == "ok":
            continue
        mark = {"ok": ui.ok, "warn": ui.warn, "fail": ui.fail}[status]
        mark(f"{ui.paint(name, 'bold')}  {ui.paint(detail, 'gray')}")


# --- docker compose ------------------------------------------------------------------------

def compose(obs: Path, env: dict[str, str], args: list[str], on_line=None) -> str:
    if shutil.which("docker") is None:
        raise CliError("docker não encontrado no PATH.", hint="Rode `obskitpy doctor` para ver o que falta.")
    cmd = ["docker", "compose", "--project-directory", str(obs), "-f", str(obs / "docker-compose.yml"),
           "-p", f"obskitpy-{env['OBSKITPY_SERVICE']}", *args]
    run_env = {**os.environ, "COMPOSE_PROFILES": profiles_for(env.get("OBSKITPY_LOCAL", "kibana"))}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=run_env)
    lines: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        if line.strip():
            lines.append(line)
            if on_line:
                on_line(line)
    if proc.wait() != 0:
        raise CliError("o docker compose falhou.", detail="\n".join(lines[-25:]),
                       hint="O Docker/Colima está rodando? Rode `obskitpy doctor` para diagnosticar.")
    return "\n".join(lines)


def kibana_step(env: dict[str, str]) -> None:
    with ui.Spinner("Esperando o Kibana ficar pronto") as sp:
        try:
            kibana.wait_ready(kibana_url(env))
        except kibana.KibanaError:
            sp.fail("O Kibana não ficou pronto a tempo")
            raise
        sp.ok("Kibana pronto")


def apply_dashboard(obs: Path, env: dict[str, str], quiet: bool = False) -> int:
    file = obs / "dashboard.ndjson"
    if not file.exists():
        raise CliError(f"{file} não existe.", hint="Rode `obskitpy init --force` para recriar.")
    with ui.Spinner("Aplicando o dashboard no Kibana") as sp:
        try:
            count = kibana.import_ndjson(kibana_url(env), file.read_text(encoding="utf-8"))
        except kibana.KibanaError:
            sp.fail("Não foi possível aplicar o dashboard")
            raise
        sp.ok(f"Dashboard aplicado {ui.paint(f'({count} objetos)', 'gray')}")
    return count


def bring_up(obs: Path, env: dict[str, str]) -> None:
    parts = ["Elasticsearch", "Collector"] + (["Kibana"] if uses_kibana(env) else []) + (["dash-obskitpy"] if uses_dash(env) else [])
    with ui.Spinner(f"Subindo {', '.join(parts[:-1])} e {parts[-1]}") as sp:
        try:
            compose(obs, env, ["up", "-d", "--wait", "--wait-timeout", "300"], on_line=sp.update)
        except CliError:
            sp.fail("Não foi possível subir a stack")
            raise
        sp.ok("Stack no ar")
    if uses_kibana(env):
        kibana_step(env)
        apply_dashboard(obs, env)


def summary_box(env: dict[str, str]) -> None:
    gray = lambda t: ui.paint(t, "gray")
    rows = []
    if uses_dash(env):
        rows.append(f"{gray('dash-obskitpy ')} {ui.link(dash_url(env))}")
    if uses_kibana(env):
        rows.append(f"{gray('Kibana        ')} {ui.link(dashboard_url(env), 'dashboard no Kibana')}")
    rows += [f"{gray('Elasticsearch')} {ui.link('http://localhost:' + env['ES_PORT'])}",
             f"{gray('OTLP gRPC     ')} localhost:{env['OTLP_GRPC_PORT']}"]
    ui.box("Tudo pronto", rows)


# --- demo ----------------------------------------------------------------------------------

def run_demo(env: dict[str, str]) -> bool:
    path = Path(DEMO_FILE)
    if not path.exists():
        template = resources.files("obskitpy.templates").joinpath("demo.py").read_text(encoding="utf-8")
        path.write_text(template.replace("__SERVICE__", env["OBSKITPY_SERVICE"]), encoding="utf-8")
    run_env = {**os.environ, "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://localhost:{env['OTLP_GRPC_PORT']}"}
    with ui.Spinner("Rodando o app de exemplo (logs, traces, métricas e alguns erros)") as sp:
        proc = subprocess.run([sys.executable, str(path)], env=run_env, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            sp.fail("O app de exemplo falhou")
            ui.hint((proc.stderr or proc.stdout).strip().splitlines()[-1] if (proc.stderr or proc.stdout).strip() else "")
            return False
        sp.update("deixando os dados prontos para consulta")
        time.sleep(2)
        try:  # sem isto, o Elasticsearch só deixa os primeiros dados consultáveis depois de ~30 s
            urllib.request.urlopen(urllib.request.Request(f"http://localhost:{env['ES_PORT']}/_refresh", method="POST"), timeout=10).read()
        except OSError:
            pass
        sp.ok(f"Exemplo enviado {ui.paint('(' + DEMO_FILE + ' — pode apagar quando quiser)', 'gray')}")
    return True


# --- comandos ------------------------------------------------------------------------------

def sanitize(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "app"


def code_snippet(service: str, otlp_port: str) -> list[str]:
    P = ui.paint
    setup = f'shutdown = obskitpy.setup("{service}")'
    pad = lambda text, width=44: text + " " * max(width - len(text), 2)
    lines = [
        f"{P('import', 'blue')} obskitpy",
        "",
        f"shutdown = obskitpy.{P('setup', 'blue')}({P(chr(34) + service + chr(34), 'amber')})" + " " * max(44 - len(setup), 2) + P("# no início do programa", "gray"),
        P("# ... seu código. Use logging normalmente: log.exception() já vai com error.type/origin/chain", "gray"),
        pad("shutdown()") + P("# ao encerrar, descarrega os dados pendentes", "gray"),
    ]
    if otlp_port != "4317":
        lines += ["", P(f"# porta OTLP diferente da padrão: export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:{otlp_port}", "gray")]
    return lines


def resolve_ports(args, interactive: bool, local: str) -> tuple[dict[str, int], list[str]]:
    ports, notes = {}, []
    needed = ports_for(local)
    for key, default in DEFAULT_PORTS.items():
        chosen = getattr(args, key.lower())
        if key not in needed and chosen is None:
            ports[key] = default  # não usada: só um valor para o compose
            continue
        if chosen is None:
            chosen = default if port_free(default) and default not in ports.values() else next_free_port(default + 1, set(ports.values()))
            if chosen != default:
                notes.append(f"porta {default} ({PORT_LABELS[key]}) ocupada → usando {chosen}")
        ports[key] = chosen
    if interactive:
        ui.out(ui.paint("  Portas: ", "gray") + " · ".join(f"{PORT_LABELS[k]} {ports[k]}" for k in needed))
        for note in notes:
            ui.hint(note)
        if ui.confirm("Alterar as portas?", default=False):
            for key in needed:
                while True:
                    answer = ui.ask(f"Porta do {PORT_LABELS[key]}", str(ports[key]))
                    if answer.isdigit() and 1024 <= int(answer) <= 65535:
                        ports[key] = int(answer)
                        break
                    ui.warn("Informe um número de porta entre 1024 e 65535.")
        notes = []
    return ports, notes


def write_dash(obs: Path, env: dict[str, str]) -> None:
    """Copia o dash-obskitpy (HTML/CSS/JS + logo) e gera o config.js do serviço."""
    dash_dir = obs / "dash"
    dash_dir.mkdir(exist_ok=True)
    (obs / "nginx.conf").write_text(resources.files("obskitpy.templates").joinpath("nginx.conf").read_text())
    src = resources.files("obskitpy.templates").joinpath("dash")
    for item in src.iterdir():
        if item.is_file():
            (dash_dir / item.name).write_bytes(item.read_bytes())
    config = {"service": env["OBSKITPY_SERVICE"]}
    if uses_kibana(env):
        config["kibana"] = dashboard_url(env)
    (dash_dir / "config.js").write_text("window.OBSKITPY = " + json.dumps(config, ensure_ascii=False) + ";\n", encoding="utf-8")


def cmd_init(args) -> None:
    interactive = ui.interactive() and not args.yes
    obs = Path(args.dir)
    ui.banner(__version__)
    if (obs / ".env").exists() and not args.force:
        raise CliError(f"{obs}/ já foi inicializada.", hint="Use --force para recriar (o dashboard.ndjson atual será perdido).")

    default_service = sanitize(Path.cwd().name)
    if interactive:
        ui.out(ui.paint("  Vou criar uma stack de observabilidade local e um dashboard para o seu projeto.\n", "gray"))
    service = args.service
    while True:
        service = service or (ui.ask("Nome do serviço", default_service) if interactive else default_service)
        if re.fullmatch(r"[a-z0-9][a-z0-9-]*", service):
            break
        if not interactive:
            raise CliError(f"nome de serviço inválido: {service!r}", hint="Use letras minúsculas, números e hífen.")
        ui.warn("Use letras minúsculas, números e hífen (ex.: meu-robo).")
        service = None

    local = args.local or (ui.choose("Onde você quer visualizar os logs?", LOCAL_MENU, default=0) if interactive else "kibana")
    ui.out()
    ports, notes = resolve_ports(args, interactive, local)
    env = {"OBSKITPY_SERVICE": service, "OBSKITPY_LOCAL": local, "COMPOSE_PROFILES": profiles_for(local),
           **{k: str(v) for k, v in ports.items()}}

    ui.heading("Ambiente")
    with ui.Spinner("Checando Docker, Compose e portas") as sp:
        checks = doctor_checks({k: ports[k] for k in ports_for(local)})
        problems = [c for c in checks if c[1] == "fail"]
        (sp.warn if problems else sp.ok)("Ambiente verificado" if not problems else "Ambiente com pendências")
    print_checks(checks, only_problems=True)
    for note in notes:
        ui.warn(note)
    docker_ok = not problems

    ui.heading("Arquivos")
    with ui.Spinner(f"Criando {obs}/") as sp:
        obs.mkdir(parents=True, exist_ok=True)
        for name in TEMPLATE_FILES:
            (obs / name).write_text(resources.files("obskitpy.templates").joinpath(name).read_text())
        header = ["# Gerado pelo obskitpy init; usado pelo docker compose e pela CLI."]
        (obs / ".env").write_text("\n".join(header + [f"{k}={v}" for k, v in env.items()]) + "\n")
        created = ["docker-compose.yml", "otel-collector-config.yaml", ".env"]
        if local in ("kibana", "ambos"):
            (obs / "dashboard.ndjson").write_text(dashboard.to_ndjson(dashboard.build(service)), encoding="utf-8")
            created.append("dashboard.ndjson")
        if local in ("dash-obskitpy", "ambos"):
            write_dash(obs, env)
            created += ["nginx.conf", "dash/"]
        sp.ok(f"Criado {obs}/ para o serviço {ui.paint(service, 'teal', 'bold')}")
    ui.hint(" · ".join(created))

    started = False
    if docker_ok and (args.up or (interactive and ui.confirm("Subir a stack agora?", default=True))):
        ui.heading("Stack")
        bring_up(obs, env)
        started = True
        if args.demo or (interactive and ui.confirm("Gerar e rodar um app de exemplo para já ver dados no dashboard?", default=True)):
            ui.heading("Exemplo")
            run_demo(env)
        ui.out()
        summary_box(env)
        if interactive and ui.confirm("Abrir o dashboard no navegador?", default=True):
            webbrowser.open(primary_url(env))
    elif not docker_ok:
        ui.warn("Os arquivos foram criados, mas falta o Docker para subir a stack. Rode `obskitpy doctor` e depois `obskitpy up`.")

    ui.out()
    steps = [] if started else [f"{ui.paint('1.', 'teal', 'bold')} Suba a stack:  {ui.paint('obskitpy up' + ('' if str(obs) == DEFAULT_DIR else f' --dir {obs}'), 'bold')}"]
    steps.append(f"{ui.paint(str(len(steps) + 1) + '.', 'teal', 'bold')} Instrumente o seu código:")
    ui.box("Próximos passos", steps + [""] + ["   " + l for l in code_snippet(service, env["OTLP_GRPC_PORT"])], accent="amber")
    ui.out(f"\n  {ui.paint('Dúvidas?', 'gray')} {ui.link('https://pedrowill-dev.github.io/obskitpy/')}   {ui.paint('· diagnóstico:', 'gray')} obskitpy doctor\n")


def cmd_up(args) -> None:
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    ui.banner(__version__)
    bring_up(obs, env)
    ui.out()
    summary_box(env)
    ui.out(f"\n  {ui.paint('Abrir no navegador:', 'gray')} obskitpy open\n")


def cmd_down(args) -> None:
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    with ui.Spinner("Derrubando a stack" + (" e apagando os dados" if args.volumes else "")) as sp:
        try:
            compose(obs, env, ["down", *(["-v"] if args.volumes else [])], on_line=sp.update)
        except CliError:
            sp.fail("Não foi possível derrubar a stack")
            raise
        sp.ok("Stack derrubada" + (" e dados apagados" if args.volumes else ""))


def cmd_status(args) -> None:
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    raw = compose(obs, env, ["ps", "-a", "--format", "json"])
    items = []
    try:
        data = json.loads(raw)
        items = data if isinstance(data, list) else [data]
    except ValueError:
        items = [json.loads(l) for l in raw.splitlines() if l.startswith("{")]
    ui.heading(f"Serviço {env['OBSKITPY_SERVICE']}")
    if not items:
        ui.warn("Nenhum container rodando. Use `obskitpy up`.")
    else:
        rows = []
        for it in items:
            state, health = it.get("State", ""), it.get("Health", "")
            good = state == "running" and health in ("", "healthy")
            label = f"{state}{' · ' + health if health else ''}"
            ports = ", ".join(sorted({f"{p['PublishedPort']}→{p['TargetPort']}" for p in it.get("Publishers") or [] if p.get("PublishedPort")}))
            rows.append([ui.paint(it.get("Service", ""), "bold"), ui.paint("● ", "green" if good else "red") + label, ui.paint(ports, "gray")])
        ui.table(["SERVIÇO", "ESTADO", "PORTAS"], rows)
    ui.out()
    if uses_dash(env):
        ui.out(f"  {ui.paint('dash-obskitpy:', 'gray')} {ui.link(dash_url(env))}")
    if uses_kibana(env):
        ui.out(f"  {ui.paint('Kibana:       ', 'gray')} {ui.link(dashboard_url(env))}")
    ui.out()


def cmd_open(args) -> None:
    env = read_env(Path(args.dir) / ".env")
    if args.ui == "kibana":
        need_kibana(env)
        url = dashboard_url(env)
    elif args.ui == "dash-obskitpy":
        if not uses_dash(env):
            raise CliError("este projeto não usa o dash-obskitpy.", hint="Recrie com `obskitpy init --force --local ambos`.")
        url = dash_url(env)
    else:
        url = primary_url(env)
    ui.ok(f"Abrindo {ui.link(url)}")
    webbrowser.open(url)


def cmd_doctor(args) -> None:
    ports = None
    env_file = Path(args.dir) / ".env"
    if env_file.exists():
        env = read_env(env_file)
        ports = {k: int(env.get(k, DEFAULT_PORTS[k])) for k in ports_for(env.get("OBSKITPY_LOCAL", "kibana"))}
    ui.banner(__version__, "diagnóstico do ambiente")
    checks = doctor_checks(ports)
    print_checks(checks)
    bad = [c for c in checks if c[1] == "fail"]
    ui.out()
    if bad:
        ui.fail(ui.paint("Corrija os itens acima para subir a stack.", "bold"))
        raise SystemExit(1)
    ui.ok(ui.paint("Tudo certo para rodar o obskitpy.", "bold"))


def cmd_dashboard_apply(args) -> None:
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    need_kibana(env)
    apply_dashboard(obs, env)
    ui.hint(dashboard_url(env))


def cmd_dashboard_export(args) -> None:
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    need_kibana(env)
    with ui.Spinner("Exportando o dashboard do Kibana") as sp:
        try:
            ndjson = kibana.export_dashboard(kibana_url(env), dashboard.dashboard_id(env["OBSKITPY_SERVICE"]))
        except kibana.KibanaError:
            sp.fail("Não foi possível exportar")
            raise
        if not ndjson.strip():
            sp.fail("O Kibana não devolveu o dashboard")
            raise CliError("o dashboard não existe no Kibana (foi apagado?).", hint="Rode `obskitpy dashboard apply` para recriá-lo.")
        (obs / "dashboard.ndjson").write_text(ndjson, encoding="utf-8")
        sp.ok(f"{obs}/dashboard.ndjson atualizado {ui.paint(f'({len(ndjson.splitlines())} objetos)', 'gray')}")
    ui.hint("Versione o arquivo no git.")


# --- parser --------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="obskitpy", description=__doc__)
    parser.add_argument("--version", action="version", version=f"obskitpy {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--dir", default=DEFAULT_DIR, help=f"pasta da stack (padrão: {DEFAULT_DIR})")
        p.set_defaults(fn=fn)
        return p

    init = command("init", cmd_init, "assistente: cria observability/ (stack + dashboard) e, se quiser, já sobe tudo")
    init.add_argument("--service", help="nome do serviço (padrão: nome da pasta atual)")
    init.add_argument("--local", choices=LOCAL_CHOICES, help="onde visualizar: kibana, dash-obskitpy ou ambos (padrão: pergunta; sem terminal, kibana)")
    init.add_argument("--force", action="store_true", help="recria uma instalação existente")
    init.add_argument("-y", "--yes", action="store_true", help="não perguntar nada (usa os padrões)")
    init.add_argument("--up", action="store_true", help="sobe a stack ao terminar")
    init.add_argument("--demo", action="store_true", help="com --up: gera e roda um app de exemplo")
    for key, default in DEFAULT_PORTS.items():
        init.add_argument(f"--{key.lower().replace('_', '-')}", dest=key.lower(), type=int, default=None,
                          help=f"porta local (padrão: {default}; se ocupada, escolhe a próxima livre)")

    command("up", cmd_up, "sobe a stack, espera ficar saudável e aplica o dashboard")
    down = command("down", cmd_down, "derruba a stack")
    down.add_argument("-v", "--volumes", action="store_true", help="apaga também os dados")
    command("status", cmd_status, "mostra os containers e o link do dashboard")
    opn = command("open", cmd_open, "abre o dashboard no navegador")
    opn.add_argument("--ui", choices=("kibana", "dash-obskitpy"), help="qual abrir (padrão: dash-obskitpy se existir, senão Kibana)")
    command("doctor", cmd_doctor, "diagnostica Docker, Compose, memória e portas")

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
    except CliError as exc:
        ui.out()
        ui.fail(ui.paint(str(exc), "bold"))
        if exc.detail:
            for line in exc.detail.splitlines():
                ui.out(f"  {ui.paint(line, 'gray')}")
        if exc.hint:
            ui.hint(f"→ {exc.hint}")
        sys.exit(1)
    except kibana.KibanaError as exc:
        ui.out()
        ui.fail(ui.paint(str(exc), "bold"))
        sys.exit(1)
    except KeyboardInterrupt:
        ui.out(f"\n{ui.paint('Cancelado.', 'gray')}")
        sys.exit(130)


if __name__ == "__main__":
    main()
