"""obskitpy: observabilidade (OpenTelemetry + Elasticsearch + Kibana) em qualquer projeto Python."""
import argparse
import difflib
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

from . import __version__, dashboard, docker, kibana, ui

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


class NotInitialized(CliError):
    """A pasta ainda não tem o obskitpy (sem observability/.env)."""


def prog() -> str:
    """Como o usuário deve chamar a CLI aqui (`obskitpy`, `uv run obskitpy` ou `python -m obskitpy`)."""
    exe = shutil.which("obskitpy")
    uv_project = bool(shutil.which("uv")) and Path("pyproject.toml").exists()
    if exe and not (uv_project and ".venv" in Path(exe).parts):  # no PATH do usuário, de verdade
        return "obskitpy"
    if uv_project:
        return "uv run obskitpy"
    return "obskitpy" if exe else "python -m obskitpy"


def cmd(text: str) -> str:
    return f"{prog()} {text}"


# --- configuração (.env na pasta observability/) -------------------------------------------

def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise NotInitialized("esta pasta ainda não tem o obskitpy.", hint=f"Rode `{cmd('init')}` aqui primeiro.")
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
                       hint=f"Recrie com `{cmd('init --force --local ambos')}` se quiser o Kibana também.")


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
    docker_bin = shutil.which("docker")
    if not docker_bin:
        checks.append(("Docker", "fail", "não encontrado — instale o Docker Desktop, ou no macOS: brew install colima docker docker-compose"))
        return checks
    checks.append(("Docker", "ok", docker_bin))
    base = docker.find_compose()
    if base:
        r = docker.run(base + ["version", "--short"])
        via = "" if base == ["docker", "compose"] else " (binário docker-compose)"
        checks.append(("Docker Compose v2", "ok", (r.stdout.strip() if r and r.returncode == 0 else "ok") + via))
    else:
        checks.append(("Docker Compose v2", "fail", f"não encontrado — {docker.install_hint()}"))
    r = docker.run(["docker", "info", "--format", "{{.ServerVersion}}|{{.MemTotal}}"])
    via_colima = False
    if not (r and r.returncode == 0 and "|" in r.stdout) and docker.use_colima_socket():
        r = docker.run(["docker", "info", "--format", "{{.ServerVersion}}|{{.MemTotal}}"])
        via_colima = True
    if r and r.returncode == 0 and "|" in r.stdout:
        version, _, mem = r.stdout.strip().partition("|")
        checks.append(("Docker em execução", "ok", f"engine {version}" + (" (via Colima)" if via_colima else "")))
        try:
            gb = int(mem) / 1024**3
            if gb < 3.5:
                checks.append(("Memória do Docker", "warn", f"{gb:.1f} GB — o Elasticsearch pede ~2 GB livres; com Colima: colima stop && colima start --memory 6"))
            else:
                checks.append(("Memória do Docker", "ok", f"{gb:.1f} GB"))
        except ValueError:
            pass
    else:
        fix = "colima start" if shutil.which("colima") else "abra o Docker Desktop"
        checks.append(("Docker em execução", "fail", f"o daemon não responde — {fix}"))
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

def write_env(path: Path, env: dict[str, str]) -> None:
    """Regrava o .env preservando comentários e a ordem das chaves."""
    lines, seen = [], set()
    for line in path.read_text().splitlines():
        key = line.partition("=")[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in env:
            lines.append(f"{key}={env[key]}")
            seen.add(key)
        else:
            lines.append(line)
    lines += [f"{k}={v}" for k, v in env.items() if k not in seen]
    path.write_text("\n".join(lines) + "\n")


def reassign_busy_ports(obs: Path, env: dict[str, str], busy: list[int]) -> list[str]:
    """Troca no .env as portas que o Docker disse estar ocupadas. Devolve as mensagens do que mudou."""
    changed, taken = [], {int(env[k]) for k in DEFAULT_PORTS if k in env}
    for key in ports_for(env.get("OBSKITPY_LOCAL", "kibana")):
        old = int(env[key])
        if old in busy:
            new = next_free_port(old + 1, taken)
            env[key] = str(new)
            taken.add(new)
            changed.append(f"porta {old} ({PORT_LABELS[key]}) ocupada → usando {new}")
    if changed:
        write_env(obs / ".env", env)
        if uses_dash(env):
            write_dash(obs, env)
    return changed


def compose(obs: Path, env: dict[str, str], args: list[str], on_line=None, _retry: int = 8) -> str:
    try:
        base = docker.ensure_ready()
    except docker.DockerProblem as exc:
        raise CliError(str(exc), hint=exc.hint)
    full = base + ["--project-directory", str(obs), "-f", str(obs / "docker-compose.yml"),
                   "-p", f"obskitpy-{env['OBSKITPY_SERVICE']}", *args]
    run_env = {**os.environ, "COMPOSE_PROFILES": profiles_for(env.get("OBSKITPY_LOCAL", "kibana"))}
    proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=run_env)
    lines: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        if line.strip():
            lines.append(line)
            if on_line:
                on_line(line)
    if proc.wait() == 0:
        return "\n".join(lines)
    text = "\n".join(lines)
    if _retry and docker.CRED_RE.search(text):  # credential helper do Docker Desktop ausente
        docker.clean_docker_config()
        ui.hint("O credential helper do Docker não foi encontrado; usando uma configuração limpa do Docker.")
        return compose(obs, env, args, on_line, _retry=_retry - 1)
    if _retry and "up" in args and docker.PORT_RE.search(text):  # porta ocupada
        changed = reassign_busy_ports(obs, env, docker.busy_ports(text))
        if changed:
            for note in changed:
                ui.hint(note)
            try:
                compose(obs, env, ["down"], None, _retry=0)  # libera o que subiu pela metade
            except CliError:
                pass
            return compose(obs, env, args, on_line, _retry=_retry - 1)  # o Docker acusa uma porta por vez
    raise CliError("o docker compose falhou.", detail="\n".join(lines[-25:]),
                   hint=f"Rode `{cmd('doctor')}` para diagnosticar (ele aponta a correção).")


def collector_ready(env: dict[str, str]) -> bool:
    """O Collector responde OTLP/HTTP? (a porta publicada pelo Docker aceita conexão antes de ele estar de pé)"""
    req = urllib.request.Request(f"http://localhost:{env['OTLP_HTTP_PORT']}/v1/logs", data=b'{"resourceLogs":[]}',
                                 headers={"content-type": "application/json"}, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=3).status == 200
    except OSError:
        return False


def wait_collector(env: dict[str, str], timeout: int = 90) -> None:
    deadline = time.time() + timeout
    while not collector_ready(env):
        if time.time() > deadline:
            raise CliError("o Collector não respondeu a tempo.", hint=f"Veja os logs: docker compose -p obskitpy-{env['OBSKITPY_SERVICE']} logs otel-collector")
        time.sleep(1)


def wait_data(env: dict[str, str], timeout: int = 60) -> bool:
    """Espera os dados do serviço ficarem consultáveis (a 1ª criação dos data streams leva alguns segundos)."""
    base = f"http://localhost:{env['ES_PORT']}"
    body = json.dumps({"query": {"term": {"resource.attributes.service.name": env["OBSKITPY_SERVICE"]}}}).encode()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(urllib.request.Request(f"{base}/_refresh", method="POST"), timeout=5).read()
            req = urllib.request.Request(f"{base}/logs-generic.otel-*/_count", data=body, headers={"content-type": "application/json"}, method="POST")
            if json.loads(urllib.request.urlopen(req, timeout=5).read()).get("count", 0) > 0:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(1.5)
    return False


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
        raise CliError(f"{file} não existe.", hint=f"Rode `{cmd('init --force')}` para recriar.")
    with ui.Spinner("Aplicando o dashboard no Kibana") as sp:
        try:
            count = kibana.import_ndjson(kibana_url(env), file.read_text(encoding="utf-8"))
        except kibana.KibanaError:
            sp.fail("Não foi possível aplicar o dashboard")
            raise
        sp.ok(f"Dashboard aplicado {ui.paint(f'({count} objetos)', 'gray')}")
    return count


def refresh_collector_config(obs: Path, env: dict[str, str]) -> bool:
    """Se o otel-collector-config.yaml desta pasta é de uma versão antiga do obskitpy, atualiza (com .bak) e recria o Collector."""
    target = obs / "otel-collector-config.yaml"
    new = resources.files("obskitpy.templates").joinpath("otel-collector-config.yaml").read_text()
    if not target.exists() or target.read_text() == new:
        return False
    shutil.copy2(target, target.with_name(target.name + ".bak"))
    target.write_text(new)
    ui.hint("Atualizei o otel-collector-config.yaml (envio ao Elasticsearch a cada 1 s); a versão anterior ficou em .bak.")
    compose(obs, env, ["up", "-d", "--force-recreate", "otel-collector"])
    return True


def bring_up(obs: Path, env: dict[str, str]) -> None:
    refresh_collector_config(obs, env)
    parts = ["Elasticsearch", "Collector"] + (["Kibana"] if uses_kibana(env) else []) + (["dash-obskitpy"] if uses_dash(env) else [])
    with ui.Spinner(f"Subindo {', '.join(parts[:-1])} e {parts[-1]}") as sp:
        try:
            compose(obs, env, ["up", "-d", "--wait", "--wait-timeout", "300"], on_line=sp.update)
        except CliError:
            sp.fail("Não foi possível subir a stack")
            raise
        sp.update("esperando o Collector aceitar dados")
        wait_collector(env)
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
        ready = wait_data(env)
        (sp.ok if ready else sp.warn)(f"Exemplo enviado {ui.paint('(' + DEMO_FILE + ' — pode apagar quando quiser)', 'gray')}"
                                      + ("" if ready else ui.paint(" — os dados podem levar alguns segundos para aparecer", "gray")))
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
        if not interactive:
            raise CliError(f"{obs}/ já foi inicializada.", hint=f"Use {cmd('init --force')} para recriar (guardo uma cópia em {obs}.bak) ou {cmd('up')} para subir como está.")
        current = read_env(obs / ".env")
        ui.out(f"  {ui.paint('Esta pasta já tem o obskitpy:', 'gray')} serviço {ui.paint(current['OBSKITPY_SERVICE'], 'teal', 'bold')}, "
               f"interface {ui.paint(current.get('OBSKITPY_LOCAL', 'kibana'), 'teal', 'bold')}.\n")
        what = ui.choose("O que você quer fazer?", [
            ("up", "Subir a stack como está", "roda o `up` com a configuração atual"),
            ("again", "Reconfigurar", f"refaz o assistente (escolher interface, portas...); guardo uma cópia em {obs}.bak"),
            ("cancel", "Cancelar", "não mexe em nada"),
        ])
        if what == "up":
            return cmd_up(args)
        if what == "cancel":
            return
        args.force = True
    if args.force and obs.exists():  # recriar nunca perde o que existia
        backup = obs.with_name(obs.name + ".bak")
        shutil.copytree(obs, backup, dirs_exist_ok=True)
        ui.hint(f"Cópia da configuração anterior em {backup}/")

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
    if problems and interactive:  # em vez de desistir, tenta resolver (Compose, Colima...) com a sua confirmação
        try:
            docker.ensure_ready()
            checks = doctor_checks({k: ports[k] for k in ports_for(local)})
            problems = [c for c in checks if c[1] == "fail"]
        except docker.DockerProblem as exc:
            ui.warn(str(exc))
            ui.hint(f"→ {exc.hint}")
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
        ui.warn(f"Os arquivos foram criados, mas o Docker ainda não está pronto. Rode `{cmd('doctor')}` e depois `{cmd('up')}`.")

    ui.out()
    steps = [] if started else [f"{ui.paint('1.', 'teal', 'bold')} Suba a stack:  {ui.paint(cmd('up') + ('' if str(obs) == DEFAULT_DIR else f' --dir {obs}'), 'bold')}"]
    steps.append(f"{ui.paint(str(len(steps) + 1) + '.', 'teal', 'bold')} Instrumente o seu código:")
    ui.box("Próximos passos", steps + [""] + ["   " + l for l in code_snippet(service, env["OTLP_GRPC_PORT"])], accent="amber")
    ui.out(f"\n  {ui.paint('Dúvidas?', 'gray')} {ui.link('https://pedrowill-dev.github.io/obskitpy/')}   {ui.paint('· diagnóstico:', 'gray')} {cmd('doctor')}\n")


def cmd_up(args) -> None:
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    ui.banner(__version__)
    bring_up(obs, env)
    ui.out()
    summary_box(env)
    if uses_dash(env) and dash_outdated(obs):
        ui.hint(f"Há uma versão mais nova do dash-obskitpy neste pacote: rode `{cmd('dash update')}` (guardo a sua em .bak).")
    ui.out(f"\n  {ui.paint('Abrir no navegador:', 'gray')} {cmd('open')}   {ui.paint('· tempo real:', 'gray')} {cmd('open --realtime')}   {ui.paint('· rodar seu código:', 'gray')} {cmd('run python app.py')}\n")


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
        ui.warn(f"Nenhum container rodando. Use `{cmd('up')}`.")
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
    if args.realtime:
        if not uses_dash(env):
            raise CliError("o modo Realtime é do dash-obskitpy, e este projeto usa só o Kibana.",
                           hint=f"Recrie com `{cmd('init --force --local ambos')}` para ter os dois.")
        url = dash_url(env) + "/#logs?realtime"
    elif args.ui == "kibana":
        need_kibana(env)
        url = dashboard_url(env)
    elif args.ui == "dash-obskitpy":
        if not uses_dash(env):
            raise CliError("este projeto não usa o dash-obskitpy.", hint=f"Recrie com `{cmd('init --force --local ambos')}`.")
        url = dash_url(env)
    else:
        url = primary_url(env)
    ui.ok(f"Abrindo {ui.link(url)}")
    webbrowser.open(url)


def stack_reachable(env: dict[str, str]) -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{env['ES_PORT']}", timeout=2).read()
        return True
    except OSError:
        return False


def cmd_run(args) -> None:
    """Roda o seu programa já apontando para a stack (e sobe a stack se ela estiver parada)."""
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    command = list(args.cmd)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        if Path(DEMO_FILE).exists():
            command = [sys.executable, DEMO_FILE]
        else:
            raise CliError("diga o que executar.", hint=f"Exemplo: {cmd('run python app.py')}")
    if command[0] in ("python", "python3") and not shutil.which(command[0]):
        command[0] = sys.executable
    if not stack_reachable(env):
        ui.warn("A stack não está no ar. Subindo agora…")
        bring_up(obs, env)
    wait_collector(env)
    endpoint = f"http://localhost:{env['OTLP_GRPC_PORT']}"
    ui.out(f"{ui.paint('▶', 'teal', 'bold')} {ui.paint(' '.join(command), 'bold')}  {ui.paint('→ enviando para ' + endpoint, 'gray')}\n")
    try:
        code = subprocess.call(command, env={**os.environ, "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint})
    except FileNotFoundError:
        raise CliError(f"não encontrei o comando {command[0]!r}.", hint="Confira o nome do programa (ex.: python app.py).")
    wait_data(env, timeout=20)  # o Collector e o Elasticsearch levam um instante para entregar o último lote
    ui.out(f"\n{ui.paint('✔' if code == 0 else '!', 'green' if code == 0 else 'amber', 'bold')} Terminou (código {code}). "
           f"Veja no dashboard: {ui.link(primary_url(env))}")
    sys.exit(code)


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


def dash_files() -> dict[str, bytes]:
    src = resources.files("obskitpy.templates").joinpath("dash")
    return {i.name: i.read_bytes() for i in src.iterdir() if i.is_file() and i.name != "config.js"}


def dash_outdated(obs: Path) -> list[str]:
    return [n for n, data in dash_files().items() if not (obs / "dash" / n).exists() or (obs / "dash" / n).read_bytes() != data]


def cmd_dash_update(args) -> None:
    """Atualiza os arquivos do dash-obskitpy (observability/dash/) para a versão do pacote instalado."""
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    if not uses_dash(env):
        raise CliError("este projeto não usa o dash-obskitpy.", hint=f"Recrie com `{cmd('init --force --local dash-obskitpy')}`.")
    changed = dash_outdated(obs)
    if not changed:
        ui.ok("O dash-obskitpy já está na versão do pacote instalado.")
        return
    files = dash_files()
    for name in changed:
        target = obs / "dash" / name
        if target.exists():
            shutil.copy2(target, target.with_name(target.name + ".bak"))  # sua versão fica guardada
        target.write_bytes(files[name])
    ui.ok(f"dash-obskitpy atualizado {ui.paint('(' + ', '.join(changed) + ')', 'gray')}")
    ui.hint("Recarregue a página (Ctrl/⌘+Shift+R). Suas versões anteriores ficaram em *.bak dentro de observability/dash/.")


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
            raise CliError("o dashboard não existe no Kibana (foi apagado?).", hint=f"Rode `{cmd('dashboard apply')}` para recriá-lo.")
        (obs / "dashboard.ndjson").write_text(ndjson, encoding="utf-8")
        sp.ok(f"{obs}/dashboard.ndjson atualizado {ui.paint(f'({len(ndjson.splitlines())} objetos)', 'gray')}")
    ui.hint("Versione o arquivo no git.")


# --- parser --------------------------------------------------------------------------------

ALIASES = {"start": "up", "stop": "down", "ps": "status", "ls": "status", "check": "doctor", "new": "init", "exec": "run"}
SAFE_AUTOFIX = {"up", "status", "open", "doctor", "run"}


class FriendlyParser(argparse.ArgumentParser):
    """Em vez de despejar um erro de uso, sugere o comando certo."""

    def error(self, message):
        m = re.search(r"invalid choice: '([^']*)'", message)
        if m and self.prog == "obskitpy":
            ui.out()
            ui.fail(f"Não conheço o comando {ui.paint(repr(m.group(1)), 'bold')}.")
            near = difflib.get_close_matches(m.group(1), COMMANDS, n=2, cutoff=0.5)
            if near:
                ui.hint("→ Você quis dizer: " + " ou ".join(f"{prog()} {n}" for n in near) + "?")
            ui.hint("Comandos: " + ", ".join(COMMANDS) + f"   ({prog()} --help para detalhes)")
            sys.exit(2)
        super().error(message)


COMMANDS = ["init", "up", "down", "status", "open", "run", "doctor", "dash", "dashboard"]


def normalize_argv(argv: list[str]) -> list[str]:
    """Aceita atalhos (start→up) e corrige erros de digitação seguros (upp→up)."""
    if not argv or argv[0].startswith("-"):
        return argv
    word = argv[0]
    if word in COMMANDS:
        return argv
    target = ALIASES.get(word)
    if not target and len(word) >= 3:
        near = difflib.get_close_matches(word, sorted(SAFE_AUTOFIX), n=2, cutoff=0.75)
        target = near[0] if len(near) == 1 else None
    if target:
        ui.hint(f"Entendi {word!r} como {target!r}.")
        return [target, *argv[1:]]
    return argv


def build_parser() -> argparse.ArgumentParser:
    parser = FriendlyParser(prog="obskitpy", description=__doc__)
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
    opn.add_argument("--realtime", action="store_true", help="abre o dash-obskitpy já em tempo real (logs ao vivo, atualizando a cada 1 s)")
    command("doctor", cmd_doctor, "diagnostica Docker, Compose, memória e portas")
    run = command("run", cmd_run, "roda o seu programa enviando os dados para a stack (sobe a stack se preciso)")
    run.add_argument("cmd", nargs=argparse.REMAINDER, help="o comando, ex.: python app.py")

    dsh = sub.add_parser("dash", help="mantém o dash-obskitpy (observability/dash/)")
    dshsub = dsh.add_subparsers(dest="action", required=True)
    upd = dshsub.add_parser("update", help="atualiza os arquivos do dash para a versão do pacote instalado")
    upd.add_argument("--dir", default=DEFAULT_DIR)
    upd.set_defaults(fn=cmd_dash_update)

    dash = sub.add_parser("dashboard", help="sincroniza o dashboard com o Kibana")
    dsub = dash.add_subparsers(dest="action", required=True)
    for name, fn, help_ in (("apply", cmd_dashboard_apply, "importa observability/dashboard.ndjson no Kibana"),
                            ("export", cmd_dashboard_export, "grava o dashboard do Kibana (com suas edições) no arquivo")):
        p = dsub.add_parser(name, help=help_)
        p.add_argument("--dir", default=DEFAULT_DIR)
        p.set_defaults(fn=fn)
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = normalize_argv(list(sys.argv[1:] if argv is None else argv))
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except NotInitialized as exc:
        ui.out()
        ui.warn(f"{str(exc).capitalize()}")
        if ui.interactive() and ui.confirm("Rodar o assistente (init) agora?", default=True):
            init_args = build_parser().parse_args(["init", "--dir", args.dir])
            init_args.fn(init_args)
            return
        ui.hint(f"→ {exc.hint}")
        sys.exit(1)
    except docker.DockerProblem as exc:
        ui.out()
        ui.fail(ui.paint(str(exc), "bold"))
        ui.hint(f"→ {exc.hint}")
        sys.exit(1)
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
