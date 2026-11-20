"""obskitpy: observabilidade (OpenTelemetry + Elasticsearch + Kibana) em qualquer projeto Python."""
import argparse
import difflib
import json
import os
import re
import secrets as pysecrets
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
DEFAULT_PORTS = {"ES_PORT": 9200, "KIBANA_PORT": 5601, "DASH_PORT": 8600, "OTLP_GRPC_PORT": 4317, "OTLP_HTTP_PORT": 4318, "PORTAL_PORT": 8700}
PORT_LABELS = {"PORTAL_PORT": "portal", "ES_PORT": "Elasticsearch", "KIBANA_PORT": "Kibana", "DASH_PORT": "dash-obskitpy",
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
    host = env.get("OBSKITPY_STACK_DIR")
    if host:  # este projeto usa a stack de outro: portas e interface vêm de lá, o serviço é o seu
        host_env_file = Path(host) / ".env"
        if not host_env_file.exists():
            raise CliError(f"a stack compartilhada não foi encontrada em {host}.",
                           hint=f"Crie uma stack própria para este projeto: {cmd('init --force --standalone')}")
        shared = read_env(host_env_file)
        env = {**shared, **env, "OBSKITPY_STACK_SERVICE": shared["OBSKITPY_SERVICE"]}
    return env


def uses_kibana(env: dict[str, str]) -> bool:
    return env.get("OBSKITPY_LOCAL", "kibana") in ("kibana", "ambos")


def uses_dash(env: dict[str, str]) -> bool:
    return env.get("OBSKITPY_LOCAL", "kibana") in ("dash-obskitpy", "ambos")


def profiles_for(local: str) -> str:
    return {"kibana": "kibana", "dash-obskitpy": "dash", "ambos": "kibana,dash"}[local]


def compose_profiles(env: dict[str, str]) -> str:
    base = profiles_for(env.get("OBSKITPY_LOCAL", "kibana"))
    return base + (",portal" if env.get("OBSKITPY_PORTAL") == "1" else "")


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
    return f"{dash_url(env)}/#overview?service={env['OBSKITPY_SERVICE']}" if uses_dash(env) else dashboard_url(env)


def is_shared(env: dict[str, str]) -> bool:
    return bool(env.get("OBSKITPY_STACK_DIR"))


def stack_dir(obs: Path, env: dict[str, str]) -> Path:
    """Pasta onde a stack mora (a própria, ou a do projeto cuja stack este usa)."""
    return Path(env["OBSKITPY_STACK_DIR"]) if is_shared(env) else obs


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
    lines = []
    for line in path.read_text().splitlines():
        key = line.partition("=")[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in env:
            lines.append(f"{key}={env[key]}")
        else:
            lines.append(line)
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
    where = stack_dir(obs, env)
    project = env.get("OBSKITPY_STACK_SERVICE") or env["OBSKITPY_SERVICE"]
    full = base + ["--project-directory", str(where), "-f", str(where / "docker-compose.yml"), "-p", f"obskitpy-{project}", *args]
    override = where / "docker-compose.override.yml"  # só para desenvolvimento do próprio obskitpy
    if override.exists():
        full[full.index("-p"):full.index("-p")] = ["-f", str(override)]
    run_env = {**os.environ, "COMPOSE_PROFILES": compose_profiles(env)}
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
    if _retry and "up" in args and docker.PORT_RE.search(text) and not is_shared(env):  # porta ocupada
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
    template = resources.files("obskitpy.templates").joinpath("demo.py").read_text(encoding="utf-8")
    text = template.replace("__SERVICE__", env["OBSKITPY_SERVICE"])
    if not path.exists() or path.read_text(encoding="utf-8") != text:  # o exemplo é gerado: sempre com o serviço deste projeto
        path.write_text(text, encoding="utf-8")
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
    config = {"service": env["OBSKITPY_SERVICE"], "services": [env["OBSKITPY_SERVICE"]]}
    if uses_kibana(env):
        config["kibana"] = f"{kibana_url(env)}/app/dashboards"
    (dash_dir / "config.js").write_text("window.OBSKITPY = " + json.dumps(config, ensure_ascii=False) + ";\n", encoding="utf-8")


def registered_services(host_dir: Path) -> list[str]:
    cfg = host_dir / "dash" / "config.js"
    try:
        text = cfg.read_text(encoding="utf-8")
        data = json.loads(text[text.index("=") + 1:].strip().rstrip(";"))
        services = [data["service"]] if data.get("service") else []
        return services + [x for x in data.get("services", []) if x not in services]
    except (OSError, ValueError):
        return []


def register_service(host_dir: Path, service: str) -> None:
    """Inclui o projeto na lista do seletor do dash da stack (aparece lá mesmo antes de ter dados)."""
    cfg = host_dir / "dash" / "config.js"
    if not cfg.exists():
        return
    text = cfg.read_text(encoding="utf-8")
    data = json.loads(text[text.index("=") + 1:].strip().rstrip(";"))
    services = data.setdefault("services", [data["service"]] if data.get("service") else [])
    if data.get("service") and data["service"] not in services:
        services.insert(0, data["service"])
    if service not in services:
        services.append(service)
    cfg.write_text("window.OBSKITPY = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")


def find_running_stacks(exclude: Path | None = None) -> list[dict]:
    """Stacks do obskitpy já rodando nesta máquina (containers do Docker Compose `obskitpy-*`)."""
    fmt = '{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.project.working_dir"}}|{{.Label "com.docker.compose.service"}}'
    r = docker.run(["docker", "ps", "--filter", "label=com.docker.compose.project", "--format", fmt])
    if not (r and r.returncode == 0) and docker.use_colima_socket():
        r = docker.run(["docker", "ps", "--filter", "label=com.docker.compose.project", "--format", fmt])
    if not (r and r.returncode == 0):
        return []
    projects: dict[str, Path] = {}
    for line in r.stdout.splitlines():
        proj, wdir, svc = (line.split("|") + ["", "", ""])[:3]
        if proj.startswith("obskitpy-") and svc == "elasticsearch" and wdir:
            projects[proj] = Path(wdir)
    found = []
    for proj, wdir in projects.items():
        if exclude and wdir.resolve() == exclude.resolve():
            continue
        try:
            found.append({"project": proj, "dir": wdir, "env": read_env(wdir / ".env")})
        except (CliError, OSError):
            continue
    return found


def stack_label(stack: dict) -> str:
    e = stack["env"]
    ui_name = {"kibana": "Kibana", "dash-obskitpy": "dash-obskitpy", "ambos": "dash-obskitpy + Kibana"}.get(e.get("OBSKITPY_LOCAL", "kibana"), "Kibana")
    return f"'{e['OBSKITPY_SERVICE']}' ({ui_name}, Elasticsearch :{e['ES_PORT']})"


def pick_stack(args, service: str, interactive: bool, obs: Path) -> dict | None:
    """Há uma stack rodando? Pergunta se esta automação deve entrar nela (mesmo dashboard, outro projeto)."""
    if getattr(args, "standalone", False):
        return None
    stacks = find_running_stacks(exclude=obs.resolve())
    wanted = getattr(args, "join", None)
    if wanted:
        match = [s for s in stacks if wanted == "auto" or wanted in (s["env"]["OBSKITPY_SERVICE"], s["project"])]
        if not match:
            raise CliError("não encontrei uma stack do obskitpy rodando" + ("" if wanted == "auto" else f" com o nome {wanted!r}") + ".",
                           hint=f"Suba a outra stack (`{cmd('up')}` na pasta dela) ou use `{cmd('init --standalone')}` para criar uma nova.")
        return match[0]
    if not stacks or not interactive:
        return None
    ui.out(f"  {ui.paint('Encontrei uma stack do obskitpy já rodando:', 'gray')}")
    for st in stacks:
        ui.out(f"   {ui.paint('•', 'teal')} {stack_label(st)}  {ui.paint(str(st['dir']), 'gray')}")
    ui.out()
    options = [(str(i), f"Incluir '{service}' na stack {stack_label(st)}",
                "mesmos containers e mesmo dashboard; no dash você escolhe qual projeto ver") for i, st in enumerate(stacks)]
    options.append(("new", "Criar uma stack separada", "novos containers, novas portas e outro dashboard"))
    pick = ui.choose("Quer incluir essa automação nela?", options, default=0)
    return None if pick == "new" else stacks[int(pick)]


def init_joined(args, obs: Path, service: str, host: dict, interactive: bool) -> None:
    """Cria só o necessário para esta automação usar a stack que já está rodando."""
    host_dir, host_env = host["dir"], host["env"]
    taken = set(registered_services(host_dir)) | {host_env["OBSKITPY_SERVICE"]}
    while service in taken:
        if not interactive:
            raise CliError(f"já existe um projeto chamado {service!r} nessa stack.", hint="Escolha outro com --service NOME.")
        ui.warn(f"Já existe um projeto chamado '{service}' nessa stack.")
        service = ui.ask("Nome do serviço", f"{service}-2")
    ui.heading("Arquivos")
    with ui.Spinner(f"Criando {obs}/ (sem containers novos)") as sp:
        obs.mkdir(parents=True, exist_ok=True)
        header = [f"# Gerado pelo obskitpy init. Este projeto usa a stack compartilhada de '{host_env['OBSKITPY_SERVICE']}'."]
        (obs / ".env").write_text("\n".join(header + [f"OBSKITPY_SERVICE={service}", f"OBSKITPY_STACK_DIR={host_dir}"]) + "\n")
        env = read_env(obs / ".env")
        created = [".env"]
        if uses_kibana(env):
            (obs / "dashboard.ndjson").write_text(dashboard.to_ndjson(dashboard.build(service)), encoding="utf-8")
            created.append("dashboard.ndjson")
        if uses_dash(env):
            register_service(host_dir, service)
        sp.ok(f"Criado {obs}/ para o serviço {ui.paint(service, 'teal', 'bold')}")
    ui.hint(" · ".join(created))
    ui.ok(f"Usando a stack {stack_label(host)}")
    if uses_kibana(env):
        try:
            apply_dashboard(obs, env)
        except (kibana.KibanaError, CliError) as exc:
            ui.warn(f"Não consegui criar o dashboard no Kibana agora ({exc}). Rode `{cmd('up')}` depois.")
    if args.demo or (interactive and ui.confirm("Gerar e rodar um app de exemplo para já ver dados desse projeto?", default=True)):
        ui.heading("Exemplo")
        run_demo(env)
    ui.out()
    summary_box(env)
    if interactive and ui.confirm("Abrir o dashboard no navegador?", default=True):
        webbrowser.open(primary_url(env))
    ui.out()
    ui.box("Próximos passos", [f"{ui.paint('1.', 'teal', 'bold')} Instrumente o seu código:", ""] + ["   " + l for l in code_snippet(service, env["OTLP_GRPC_PORT"])], accent="amber")
    ui.out(f"\n  {ui.paint('No dash, escolha o projeto', 'gray')} {ui.paint(service, 'bold')} {ui.paint('no seletor do topo.', 'gray')}\n")


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

    host = pick_stack(args, service, interactive, obs)
    if host:
        return init_joined(args, obs, service, host, interactive)

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
    if uses_dash(env) and dash_outdated(stack_dir(obs, env)):
        ui.hint(f"Há uma versão mais nova do dash-obskitpy neste pacote: rode `{cmd('dash update')}` (guardo a sua em .bak).")
    ui.out(f"\n  {ui.paint('Abrir no navegador:', 'gray')} {cmd('open')}   {ui.paint('· tempo real:', 'gray')} {cmd('open --realtime')}   {ui.paint('· rodar seu código:', 'gray')} {cmd('run python app.py')}\n")


def cmd_down(args) -> None:
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    if is_shared(env):
        ui.ok(f"Este projeto usa a stack compartilhada de {ui.paint(env['OBSKITPY_STACK_SERVICE'], 'bold')}; nada foi derrubado, porque outros projetos podem estar usando.")
        ui.hint(f"Para derrubar a stack, rode `{cmd('down')}` na pasta dela: {env['OBSKITPY_STACK_DIR']}")
        return
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
    ui.heading(f"Serviço {env['OBSKITPY_SERVICE']}" + (f"  (stack compartilhada de {env['OBSKITPY_STACK_SERVICE']})" if is_shared(env) else ""))
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
        url = f"{dash_url(env)}/#logs?realtime&service={env['OBSKITPY_SERVICE']}"
    elif args.ui == "kibana":
        need_kibana(env)
        url = dashboard_url(env)
    elif args.ui == "dash-obskitpy":
        if not uses_dash(env):
            raise CliError("este projeto não usa o dash-obskitpy.", hint=f"Recrie com `{cmd('init --force --local ambos')}`.")
        url = primary_url(env)
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


def recent_services(env: dict[str, str], seconds: int = 180) -> list[str]:
    """Serviços que mandaram logs nos últimos segundos (para detectar nome diferente do configurado)."""
    body = json.dumps({"size": 0, "query": {"range": {"@timestamp": {"gte": f"now-{seconds}s"}}},
                       "aggs": {"s": {"terms": {"field": "resource.attributes.service.name", "size": 20}}}}).encode()
    req = urllib.request.Request(f"http://localhost:{env['ES_PORT']}/logs-generic.otel-*/_search", data=body,
                                 headers={"content-type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://localhost:{env['ES_PORT']}/_refresh", method="POST"), timeout=5).read()
        data = json.loads(urllib.request.urlopen(req, timeout=5).read())
        return [b["key"] for b in data["aggregations"]["s"]["buckets"]]
    except (OSError, ValueError, KeyError):
        return []


def wait_recent(env: dict[str, str], since: float, timeout: int = 20) -> list[str]:
    """Espera chegarem dados enviados depois de `since` (de qualquer serviço) e devolve quem os enviou."""
    deadline = time.time() + timeout
    while True:
        found = recent_services(env, int(time.time() - since) + 2)
        if found or time.time() > deadline:
            return found
        time.sleep(1.5)


def service_url(env: dict[str, str], service: str) -> str:
    return primary_url({**env, "OBSKITPY_SERVICE": service})


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
    started = time.time()
    ui.out(f"{ui.paint('▶', 'teal', 'bold')} {ui.paint(' '.join(command), 'bold')}  {ui.paint('→ enviando para ' + endpoint, 'gray')}\n")
    try:
        code = subprocess.call(command, env={**os.environ, "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint})
    except FileNotFoundError:
        raise CliError(f"não encontrei o comando {command[0]!r}.", hint="Confira o nome do programa (ex.: python app.py).")
    sent_by = wait_recent(env, started)  # o Collector e o Elasticsearch levam um instante
    others = [x for x in sent_by if x != env["OBSKITPY_SERVICE"]]
    if sent_by and env["OBSKITPY_SERVICE"] not in sent_by and others:
        # o código mandou dados, mas com outro nome de serviço: o dash deste projeto ficaria vazio
        ui.out()
        ui.warn(f"Seu código enviou os dados como {ui.paint(repr(others[0]), 'bold')}, mas este projeto é {ui.paint(repr(env['OBSKITPY_SERVICE']), 'bold')}; por isso o dash dele aparece vazio.")
        ui.hint(f"→ Para ver agora: {ui.link(service_url(env, others[0]))}")
        ui.hint(f"→ Para corrigir: troque para obskitpy.setup(\"{env['OBSKITPY_SERVICE']}\") no seu código (ou escolha o projeto {others[0]!r} no seletor do dash).")
        sys.exit(code)
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


def set_env_keys(path: Path, updates: dict[str, str]) -> None:
    """Atualiza (ou acrescenta) chaves no .env preservando comentários."""
    lines, seen = [], set()
    for line in path.read_text().splitlines():
        key = line.partition("=")[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in updates:
            lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            lines.append(line)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    path.write_text("\n".join(lines) + "\n")


def portal_url(env: dict[str, str]) -> str:
    return f"{dash_url(env)}/#integrations"


def wait_portal(env: dict[str, str], timeout: int = 240) -> None:
    deadline = time.time() + timeout
    url = f"http://localhost:{env['PORTAL_PORT']}/api/v1/health"
    while True:
        try:
            if urllib.request.urlopen(url, timeout=3).status == 200:
                return
        except OSError:
            pass
        if time.time() > deadline:
            raise CliError("o portal não respondeu a tempo.", hint=f"Veja os logs: docker compose -p obskitpy-{env['OBSKITPY_SERVICE']} logs portal")
        time.sleep(2)


def cmd_portal_enable(args) -> None:
    """Liga o portal de alertas e integrações: PostgreSQL + API, com segredos gerados e a aba Integrações no dash."""
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    if is_shared(env):
        raise CliError("o portal fica na pasta que criou a stack.", hint=f"Rode `{cmd('portal enable')}` em {env['OBSKITPY_STACK_DIR']}.")
    if not uses_dash(env):
        raise CliError("a tela de Integrações fica no dash-obskitpy, e este projeto usa só o Kibana.",
                       hint=f"Recrie com `{cmd('init --force --local ambos')}` e rode de novo.")
    ui.banner(__version__, "portal de alertas e integrações")
    ui.heading("Configuração")
    with ui.Spinner("Preparando arquivos e segredos") as sp:
        for name in ("docker-compose.yml", "nginx.conf"):  # arquivos gerados: ganham os serviços do portal
            target = obs / name
            new = resources.files("obskitpy.templates").joinpath(name).read_text()
            if target.exists() and target.read_text() != new:
                shutil.copy2(target, target.with_name(target.name + ".bak"))
            target.write_text(new)
        if not (obs / "dash").exists():
            write_dash(obs, env)
        for name, data in dash_files().items():  # o dash precisa da versão com a aba Integrações
            (obs / "dash" / name).write_bytes(data)
        portal_env = obs / ".portal.env"
        created = not portal_env.exists()
        if created:
            admin_pw = pysecrets.token_urlsafe(12)
            pg_pw = pysecrets.token_urlsafe(24)
            spec = args.pip_spec or f"obskitpy[portal]=={__version__}"
            portal_env.write_text("\n".join([
                "# Segredos do portal. NÃO versione este arquivo (já está no .gitignore).",
                f"POSTGRES_PASSWORD={pg_pw}",
                f"DATABASE_URL=postgresql://obskitpy:{pg_pw}@postgres:5432/obskitpy",
                f"OBSKITPY_SECRET_KEY={pysecrets.token_urlsafe(48)}",
                f"OBSKITPY_ADMIN_EMAIL={args.admin_email}",
                f"OBSKITPY_ADMIN_PASSWORD={admin_pw}",
                f"OBSKITPY_PIP_SPEC={spec}", ""]), encoding="utf-8")
            portal_env.chmod(0o600)
        else:  # já existia: o portal acompanha a versão instalada (a menos que --pip-spec diga outra coisa)
            spec = args.pip_spec or f"obskitpy[portal]=={__version__}"
            set_env_keys(portal_env, {"OBSKITPY_PIP_SPEC": spec})
        if args.dev_source:  # desenvolvimento do obskitpy: instala o portal a partir do código local
            (obs / "docker-compose.override.yml").write_text(
                f'services:\n  portal:\n    volumes:\n      - "{Path(args.dev_source).resolve()}:/pkg:ro"\n')
        gitignore = obs / ".gitignore"
        lines = gitignore.read_text().splitlines() if gitignore.exists() else []
        for entry in (".portal.env", "docker-compose.override.yml"):
            if entry not in lines:
                lines.append(entry)
        gitignore.write_text("\n".join(lines) + "\n")
        if args.portal_port:
            port = args.portal_port
        elif env.get("OBSKITPY_PORTAL") == "1" and env.get("PORTAL_PORT"):  # já ativado: mantém a porta da stack
            port = int(env["PORTAL_PORT"])
        else:
            port = next_free_port(int(env.get("PORTAL_PORT") or DEFAULT_PORTS["PORTAL_PORT"]))
        set_env_keys(obs / ".env", {"OBSKITPY_PORTAL": "1", "PORTAL_PORT": str(port), "COMPOSE_PROFILES": compose_profiles({**env, "OBSKITPY_PORTAL": "1"})})
        sp.ok("Arquivos e segredos prontos" + ("" if created else " (segredos existentes mantidos)"))
    env = read_env(obs / ".env")
    if args.no_start:
        ui.ok(f"Arquivos do portal prontos em {obs}/ (sem subir os containers)")
        return
    ui.heading("Subindo PostgreSQL e o portal")
    ui.hint("A primeira vez baixa as imagens e instala o portal (pode levar alguns minutos).")
    with ui.Spinner("Subindo a stack com o portal") as sp:
        try:
            compose(obs, env, ["up", "-d", "--wait", "--wait-timeout", "420", "--force-recreate", "dash", "postgres", "portal"], on_line=sp.update)
        except CliError:
            sp.fail("Não foi possível subir o portal")
            raise
        sp.update("esperando o portal responder")
        wait_portal(env)
        sp.ok("Portal no ar")
    pe = read_env(obs / ".portal.env")
    api_docs = f"http://localhost:{env['PORTAL_PORT']}/api/docs"
    ui.out()
    ui.box("Portal de alertas e integrações", [
        f"{ui.paint('Integrações  ', 'gray')} {ui.link(portal_url(env))}",
        f"{ui.paint('API e docs    ', 'gray')} {ui.link(api_docs)}",
        f"{ui.paint('Usuário       ', 'gray')} {pe['OBSKITPY_ADMIN_EMAIL']}",
        f"{ui.paint('Senha         ', 'gray')} {pe['OBSKITPY_ADMIN_PASSWORD']}",
    ], accent="amber")
    ui.hint(f"As credenciais ficam em {obs}/.portal.env (fora do git). Para gerar outra senha: {cmd('portal password')}")


def cmd_portal_password(args) -> None:
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    if env.get("OBSKITPY_PORTAL") != "1":
        raise CliError("o portal não está ativado neste projeto.", hint=f"Rode `{cmd('portal enable')}`.")
    portal_env = stack_dir(obs, env) / ".portal.env"
    pe = read_env(portal_env)
    new_pw = pysecrets.token_urlsafe(12)
    compose(obs, env, ["exec", "-T", "portal", "python", "-m", "obskitpy.portal.manage", "reset-admin", pe["OBSKITPY_ADMIN_EMAIL"], new_pw])
    set_env_keys(portal_env, {"OBSKITPY_ADMIN_PASSWORD": new_pw})
    ui.ok(f"Nova senha de {pe['OBSKITPY_ADMIN_EMAIL']}: {ui.paint(new_pw, 'bold')}")


def cmd_dash_update(args) -> None:
    """Atualiza os arquivos do dash-obskitpy (observability/dash/) para a versão do pacote instalado."""
    obs = Path(args.dir)
    env = read_env(obs / ".env")
    if not uses_dash(env):
        raise CliError("este projeto não usa o dash-obskitpy.", hint=f"Recrie com `{cmd('init --force --local dash-obskitpy')}`.")
    obs = stack_dir(obs, env)  # na stack compartilhada, o dash mora na pasta do projeto que a criou
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


COMMANDS = ["init", "up", "down", "status", "open", "run", "doctor", "portal", "dash", "dashboard"]


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
    init.add_argument("--join", nargs="?", const="auto", metavar="STACK", help="usa uma stack do obskitpy já rodando (nome do serviço dela; sem valor, a primeira encontrada)")
    init.add_argument("--standalone", action="store_true", help="não procurar stacks rodando: cria uma stack própria")
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

    prt = sub.add_parser("portal", help="portal de alertas e integrações (PostgreSQL + API)")
    prtsub = prt.add_subparsers(dest="action", required=True)
    pen = prtsub.add_parser("enable", help="liga o portal: PostgreSQL, API, segredos e a aba Integrações no dash")
    pen.add_argument("--dir", default=DEFAULT_DIR)
    pen.add_argument("--admin-email", default="admin@obskitpy.local", help="e-mail do administrador inicial")
    pen.add_argument("--portal-port", type=int, help="porta local da API (padrão: 8700, ou a próxima livre)")
    pen.add_argument("--pip-spec", help=argparse.SUPPRESS)
    pen.add_argument("--dev-source", help=argparse.SUPPRESS)
    pen.add_argument("--no-start", action="store_true", help=argparse.SUPPRESS)
    pen.set_defaults(fn=cmd_portal_enable)
    ppw = prtsub.add_parser("password", help="gera uma nova senha para o administrador do portal")
    ppw.add_argument("--dir", default=DEFAULT_DIR)
    ppw.set_defaults(fn=cmd_portal_password)

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
