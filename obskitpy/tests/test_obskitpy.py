import json

import pytest

import socket

from obskitpy import cli, dashboard, errors, ui
from obskitpy.cli import main


@pytest.fixture(autouse=True)
def no_docker(monkeypatch):
    """Os testes não dependem do Docker da máquina."""
    monkeypatch.setattr(cli, "doctor_checks", lambda ports=None: [("Docker", "ok", "fake")])
    monkeypatch.setattr(cli, "find_running_stacks", lambda exclude=None: [])  # não depende de stacks reais da máquina


def test_dashboard_is_scoped_and_consistent():
    objects = dashboard.build("meu-app")
    ids = {(o["type"], o["id"]) for o in objects}
    dash = next(o for o in objects if o["type"] == "dashboard")
    assert dash["id"] == "meu-app-observability"
    for r in dash["references"]:
        assert (r["type"], r["id"]) in ids
    for o in objects:
        for r in o["references"]:
            assert (r["type"], r["id"]) in ids
    for o in objects:
        if o["type"] in ("visualization", "search"):
            query = json.loads(o["attributes"]["kibanaSavedObjectMeta"]["searchSourceJSON"])["query"]["query"]
            assert 'resource.attributes.service.name : "meu-app"' in query
    assert len(dashboard.to_ndjson(objects).splitlines()) == len(objects)


def test_init_creates_files_and_refuses_overwrite(tmp_path, capsys):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "pedidos", "--kibana-port", "5700"])
    assert {p.name for p in obs.iterdir()} == {
        ".env", "docker-compose.yml", "otel-collector-config.yaml", "dashboard.ndjson"}
    env = (obs / ".env").read_text()
    assert "OBSKITPY_SERVICE=pedidos" in env and "KIBANA_PORT=5700" in env
    for line in (obs / "dashboard.ndjson").read_text().splitlines():
        json.loads(line)
    with pytest.raises(SystemExit):
        main(["init", "--dir", str(obs), "--service", "pedidos"])
    assert "obskitpy.setup(\"pedidos\")" in capsys.readouterr().out


def test_init_rejects_bad_service(tmp_path):
    with pytest.raises(SystemExit):
        main(["init", "--dir", str(tmp_path / "o"), "--service", "Nome Ruim"])


def test_commands_need_init(tmp_path):
    with pytest.raises(SystemExit):
        main(["up", "--dir", str(tmp_path / "nada")])


def test_error_attributes():
    def inner():
        raise ValueError("x")

    try:
        inner()
    except ValueError as exc:
        attrs = errors.error_attributes(exc)
    assert attrs["error.type"] == "ValueError"
    assert attrs["error.origin"].endswith(":inner:" + attrs["error.origin"].split(":")[-1])
    assert json.loads(attrs["error.chain"])[-1]["funcao"] == "inner"


def test_ui_plain_when_not_a_tty(monkeypatch):
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert ui.paint("oi", "teal", "bold") == "oi"


def test_ui_colors_when_forced(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert "\x1b[" in ui.paint("oi", "teal") and ui.visible_len(ui.paint("oi", "teal")) == 2
    monkeypatch.setenv("NO_COLOR", "1")
    assert ui.paint("oi", "teal") == "oi"


def test_spinner_non_tty_prints_lines(capsys):
    with ui.Spinner("Trabalhando") as sp:
        sp.update("detalhe")
        sp.ok("Feito")
    out = capsys.readouterr().out
    assert "Trabalhando" in out and "Feito" in out and "\x1b" not in out


def test_ask_and_confirm_use_defaults_without_tty():
    assert ui.ask("Nome", "padrao") == "padrao"
    assert ui.confirm("Seguir?", True) is True and ui.confirm("Seguir?", False) is False


def test_next_free_port_skips_busy():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        assert not cli.port_free(busy)
        assert cli.next_free_port(busy) != busy


def test_init_auto_picks_free_port(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        monkeypatch.setitem(cli.DEFAULT_PORTS, "KIBANA_PORT", busy)
        main(["init", "--service", "app", "--yes"])
    env = (tmp_path / "observability" / ".env").read_text()
    assert f"KIBANA_PORT={busy}" not in env


def test_init_wizard_asks_name(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ui, "interactive", lambda: True)
    answers = iter(["meu-robo", "1", "n"])  # nome; onde visualizar (1=Kibana); "Alterar as portas?" não
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr(cli, "doctor_checks", lambda ports=None: [("Docker", "fail", "ausente")])

    def not_ready():
        raise cli.docker.DockerProblem("sem docker", "instale")
    monkeypatch.setattr(cli.docker, "ensure_ready", not_ready)
    main(["init"])
    out = capsys.readouterr().out
    assert "OBSKITPY_SERVICE=meu-robo" in (tmp_path / "observability" / ".env").read_text()
    assert "ainda não está pronto" in out and 'obskitpy.setup("meu-robo")' in out


def test_doctor_fails_without_docker(monkeypatch):
    monkeypatch.undo()  # usa o doctor_checks real
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as exc:
        main(["doctor"])
    assert exc.value.code == 1


def test_demo_template_has_service_placeholder():
    from importlib import resources
    text = resources.files("obskitpy.templates").joinpath("demo.py").read_text()
    assert "__SERVICE__" in text and "obskitpy.setup" in text


def test_init_local_dash_only(tmp_path):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "pedidos", "--local", "dash-obskitpy", "--yes"])
    names = {p.name for p in obs.iterdir()}
    assert {"dash", "nginx.conf", ".env", "docker-compose.yml"} <= names and "dashboard.ndjson" not in names
    env = (obs / ".env").read_text()
    assert "OBSKITPY_LOCAL=dash-obskitpy" in env and "COMPOSE_PROFILES=dash" in env
    config = (obs / "dash" / "config.js").read_text()
    assert '"service": "pedidos"' in config and "kibana" not in config
    assert {"index.html", "app.js", "style.css", "logo.png", "config.js"} <= {p.name for p in (obs / "dash").iterdir()}


def test_init_local_both(tmp_path):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "pedidos", "--local", "ambos", "--yes"])
    assert (obs / "dashboard.ndjson").exists() and (obs / "dash" / "app.js").exists()
    assert "COMPOSE_PROFILES=kibana,dash" in (obs / ".env").read_text()
    assert "/app/dashboards" in (obs / "dash" / "config.js").read_text()  # link para o Kibana


def test_kibana_commands_refuse_dash_only(tmp_path):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "pedidos", "--local", "dash-obskitpy", "--yes"])
    for cmd in (["dashboard", "apply"], ["dashboard", "export"], ["open", "--ui", "kibana"]):
        with pytest.raises(SystemExit):
            main([*cmd, "--dir", str(obs)])


def test_choose_menu_default_without_tty():
    assert ui.choose("Onde?", cli.LOCAL_MENU, default=0) == "kibana"


def test_dash_app_has_no_unfilled_placeholders():
    from importlib import resources
    js = resources.files("obskitpy.templates").joinpath("dash/app.js").read_text()
    assert "es/${index}/${endpoint}" in js and "__SERVICE__" not in js


def test_find_compose_falls_back_to_standalone(monkeypatch):
    monkeypatch.setattr(cli.docker, "_compose", None)
    monkeypatch.setattr(cli.docker.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(cli.docker, "_ok", lambda c: c[0] == "docker-compose")  # `docker compose` quebrado
    assert cli.docker.find_compose() == ["docker-compose"]
    monkeypatch.setattr(cli.docker, "_compose", None)
    monkeypatch.setattr(cli.docker, "_ok", lambda c: True)
    assert cli.docker.find_compose() == ["docker", "compose"]


def test_busy_ports_parsed_from_compose_errors():
    text = "Error response from daemon: Bind for 127.0.0.1:9200 failed: port is already allocated\n" \
           "ports are not available: exposing port TCP 127.0.0.1:5601 -> 0.0.0.0:0: listen tcp 127.0.0.1:5601: bind: address already in use"
    assert cli.docker.busy_ports(text) == [9200, 5601]
    assert cli.docker.CRED_RE.search("error getting credentials - err: exec: \"docker-credential-desktop\": not found")


def test_reassign_busy_ports_rewrites_env(tmp_path):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "app", "--yes"])
    env = cli.read_env(obs / ".env")
    notes = cli.reassign_busy_ports(obs, env, [int(env["ES_PORT"])])
    assert notes and "Elasticsearch" in notes[0]
    assert cli.read_env(obs / ".env")["ES_PORT"] != str(9200) or env["ES_PORT"] != "9200"
    assert "# Gerado pelo obskitpy init" in (obs / ".env").read_text()  # comentário preservado


def test_aliases_and_typos(capsys):
    assert cli.normalize_argv(["start"]) == ["up"]
    assert cli.normalize_argv(["upp", "--dir", "x"]) == ["up", "--dir", "x"]
    assert cli.normalize_argv(["xyz"]) == ["xyz"]
    assert cli.normalize_argv(["up"]) == ["up"] and cli.normalize_argv(["--version"]) == ["--version"]


def test_unknown_command_suggests(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["rnu"])
    out = capsys.readouterr().out
    assert exc.value.code == 2 and "Não conheço" in out and "run" in out and "Traceback" not in out


def test_commands_without_init_explain_instead_of_crashing(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["up", "--dir", str(tmp_path / "nada")])
    out = capsys.readouterr().out
    assert exc.value.code == 1 and "init" in out


def test_run_points_program_at_the_stack(tmp_path, monkeypatch):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "app", "--yes"])
    monkeypatch.setattr(cli, "stack_reachable", lambda env: True)
    monkeypatch.setattr(cli, "collector_ready", lambda env: True)
    monkeypatch.setattr(cli, "wait_recent", lambda env, since, timeout=0: ["app"])
    seen = {}

    def fake_call(command, env):
        seen["cmd"], seen["endpoint"] = command, env["OTEL_EXPORTER_OTLP_ENDPOINT"]
        return 0
    monkeypatch.setattr(cli.subprocess, "call", fake_call)
    monkeypatch.setattr(cli.urllib.request, "urlopen", lambda *a, **k: type("R", (), {"read": lambda self: b""})())
    with pytest.raises(SystemExit) as exc:
        main(["run", "--dir", str(obs), "--", "python", "app.py"])
    assert exc.value.code == 0 and seen["cmd"][-1] == "app.py" and seen["endpoint"].startswith("http://localhost:")


def test_run_without_command_explains(tmp_path, monkeypatch):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "app", "--yes"])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        main(["run", "--dir", str(obs)])


def test_prog_picks_a_usable_invocation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda n: "/x/obskitpy" if n == "obskitpy" else None)
    assert cli.prog() == "obskitpy"
    # dentro de um projeto uv, o executável do .venv não está no PATH do usuário: sugere `uv run`
    monkeypatch.setattr(cli.shutil, "which", lambda n: {"obskitpy": "/p/.venv/bin/obskitpy", "uv": "/x/uv"}.get(n))
    (tmp_path / "pyproject.toml").write_text("")
    assert cli.prog() == "uv run obskitpy"
    (tmp_path / "pyproject.toml").unlink()
    monkeypatch.setattr(cli.shutil, "which", lambda n: "/x/uv" if n == "uv" else None)
    (tmp_path / "pyproject.toml").write_text("")
    assert cli.prog() == "uv run obskitpy"
    monkeypatch.setattr(cli.shutil, "which", lambda n: None)
    assert cli.prog() == "python -m obskitpy"


def test_init_on_existing_folder_offers_choices(tmp_path, monkeypatch, capsys):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "app", "--yes"])
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "3")  # Cancelar
    main(["init", "--dir", str(obs)])
    out = capsys.readouterr().out
    assert "já tem o obskitpy" in out and "Cancelar" in out
    assert not (tmp_path / "observability.bak").exists()


def test_init_force_keeps_a_backup(tmp_path):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "app", "--yes"])
    (obs / "dashboard.ndjson").write_text("meu dashboard editado\n")
    main(["init", "--dir", str(obs), "--service", "app", "--yes", "--force"])
    assert (tmp_path / "observability.bak" / "dashboard.ndjson").read_text() == "meu dashboard editado\n"


def test_wait_collector_retries_until_ready(monkeypatch):
    answers = iter([False, False, True])
    monkeypatch.setattr(cli, "collector_ready", lambda env: next(answers))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    cli.wait_collector({"OTLP_HTTP_PORT": "1", "OBSKITPY_SERVICE": "a"})  # não levanta


def test_wait_collector_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(cli, "collector_ready", lambda env: False)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    clock = iter(range(0, 1000, 50))
    monkeypatch.setattr(cli.time, "time", lambda: next(clock))
    with pytest.raises(cli.CliError) as exc:
        cli.wait_collector({"OTLP_HTTP_PORT": "1", "OBSKITPY_SERVICE": "a"}, timeout=60)
    assert "logs otel-collector" in exc.value.hint


def test_wait_data_polls_until_service_has_data(monkeypatch):
    calls = {"n": 0}

    class Resp:
        def __init__(self, count): self.count = count
        def read(self): return json.dumps({"count": self.count}).encode()

    def fake_urlopen(req, timeout=0):
        if req.full_url.endswith("_refresh"):
            return Resp(0)
        calls["n"] += 1
        return Resp(0 if calls["n"] < 3 else 5)
    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    assert cli.wait_data({"ES_PORT": "1", "OBSKITPY_SERVICE": "a"}, timeout=30) is True and calls["n"] == 3


def test_setup_flushes_fast_for_realtime():
    import inspect
    import obskitpy
    params = inspect.signature(obskitpy.setup).parameters
    assert params["flush_interval_ms"].default == 1000 and params["metrics_interval_ms"].default == 5000


def test_open_realtime_needs_dash(tmp_path):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "app", "--local", "kibana", "--yes"])
    with pytest.raises(SystemExit):
        main(["open", "--realtime", "--dir", str(obs)])


def test_open_realtime_url(tmp_path, monkeypatch, capsys):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "app", "--local", "dash-obskitpy", "--yes"])
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    main(["open", "--realtime", "--dir", str(obs)])
    assert opened and "/#logs?realtime" in opened[0] and "service=app" in opened[0]


def test_dash_update_replaces_outdated_files_keeping_backup(tmp_path):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "app", "--local", "dash-obskitpy", "--yes"])
    (obs / "dash" / "app.js").write_text("// versão antiga")
    assert cli.dash_outdated(obs) == ["app.js"]
    main(["dash", "update", "--dir", str(obs)])
    assert (obs / "dash" / "app.js.bak").read_text() == "// versão antiga"
    assert "realtime" in (obs / "dash" / "app.js").read_text().lower() and cli.dash_outdated(obs) == []
    main(["dash", "update", "--dir", str(obs)])  # já atualizado: não faz nada
    assert 'window.OBSKITPY' in (obs / "dash" / "config.js").read_text()


def test_dash_app_supports_custom_spans_and_realtime():
    from importlib import resources
    js = resources.files("obskitpy.templates").joinpath("dash/app.js").read_text()
    for needle in ("segundos", "setRealtime", "realtime", "dash.refresh", "bucket("):
        assert needle in js


def test_collector_config_flushes_every_second():
    from importlib import resources
    text = resources.files("obskitpy.templates").joinpath("otel-collector-config.yaml").read_text()
    assert "flush:" in text and "interval: 1s" in text


def test_refresh_collector_config_updates_old_file(tmp_path, monkeypatch):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "app", "--yes"])
    cfg = obs / "otel-collector-config.yaml"
    cfg.write_text("# antigo\n")
    recreated = []
    monkeypatch.setattr(cli, "compose", lambda *a, **k: recreated.append(a[2]))
    env = cli.read_env(obs / ".env")
    assert cli.refresh_collector_config(obs, env) is True
    assert (obs / "otel-collector-config.yaml.bak").read_text() == "# antigo\n"
    assert "flush:" in cfg.read_text() and recreated == [["up", "-d", "--force-recreate", "otel-collector"]]
    assert cli.refresh_collector_config(obs, env) is False  # já atualizado


# ---------- stack compartilhada ----------

def fake_host(tmp_path, monkeypatch, local="dash-obskitpy", service="news-gov"):
    host_obs = tmp_path / "host" / "observability"
    main(["init", "--dir", str(host_obs), "--service", service, "--local", local, "--yes"])
    host = {"project": f"obskitpy-{service}", "dir": host_obs.resolve(), "env": cli.read_env(host_obs / ".env")}
    monkeypatch.setattr(cli, "find_running_stacks", lambda exclude=None: [host])
    return host


def test_find_running_stacks_reads_compose_labels(tmp_path, monkeypatch):
    host = tmp_path / "observability"
    main(["init", "--dir", str(host), "--service", "alfa", "--yes"])
    out = (f"obskitpy-alfa|{host.resolve()}|elasticsearch\nobskitpy-alfa|{host.resolve()}|kibana\n"
           "outra|/x|elasticsearch\nobskitpy-sem-es|/y|kibana\n")
    monkeypatch.undo()
    monkeypatch.setattr(cli.docker, "run", lambda cmd, timeout=20: type("R", (), {"returncode": 0, "stdout": out})())
    found = cli.find_running_stacks()
    assert [f["project"] for f in found] == ["obskitpy-alfa"] and found[0]["env"]["OBSKITPY_SERVICE"] == "alfa"
    assert cli.find_running_stacks(exclude=host) == []


def test_init_join_creates_only_env_and_registers_service(tmp_path, monkeypatch):
    host = fake_host(tmp_path, monkeypatch)
    mine = tmp_path / "b" / "observability"
    main(["init", "--dir", str(mine), "--service", "teste-b", "--join", "--yes"])
    names = {p.name for p in mine.iterdir()}
    assert names == {".env"}  # dash-obskitpy: sem compose, sem containers novos
    env = cli.read_env(mine / ".env")
    assert env["OBSKITPY_SERVICE"] == "teste-b" and env["OBSKITPY_STACK_SERVICE"] == "news-gov"
    assert env["ES_PORT"] == host["env"]["ES_PORT"] and cli.is_shared(env)
    assert cli.registered_services(host["dir"]) == ["news-gov", "teste-b"]
    assert "service=teste-b" in cli.primary_url(env)


def test_init_join_with_kibana_gets_its_own_dashboard(tmp_path, monkeypatch):
    fake_host(tmp_path, monkeypatch, local="kibana")
    monkeypatch.setattr(cli, "apply_dashboard", lambda *a, **k: 12)
    mine = tmp_path / "b" / "observability"
    main(["init", "--dir", str(mine), "--service", "teste-b", "--join", "--yes"])
    assert "teste-b-observability" in (mine / "dashboard.ndjson").read_text()


def test_join_rejects_duplicate_name_and_missing_stack(tmp_path, monkeypatch):
    fake_host(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        main(["init", "--dir", str(tmp_path / "x" / "observability"), "--service", "news-gov", "--join", "--yes"])
    monkeypatch.setattr(cli, "find_running_stacks", lambda exclude=None: [])
    with pytest.raises(SystemExit):
        main(["init", "--dir", str(tmp_path / "y" / "observability"), "--service", "novo", "--join", "--yes"])


def test_interactive_init_offers_to_join(tmp_path, monkeypatch, capsys):
    fake_host(tmp_path, monkeypatch)
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(cli, "run_demo", lambda env: True)
    answers = iter(["teste-b", "1", "n", "n"])  # nome; incluir na stack; exemplo não; abrir não
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    mine = tmp_path / "b" / "observability"
    main(["init", "--dir", str(mine)])
    out = capsys.readouterr().out
    assert "Encontrei uma stack" in out and "Incluir 'teste-b'" in out and "seletor" in out
    assert cli.is_shared(cli.read_env(mine / ".env"))


def test_standalone_flag_skips_detection(tmp_path, monkeypatch):
    fake_host(tmp_path, monkeypatch)
    mine = tmp_path / "b" / "observability"
    main(["init", "--dir", str(mine), "--service", "teste-b", "--standalone", "--yes"])
    assert (mine / "docker-compose.yml").exists() and not cli.is_shared(cli.read_env(mine / ".env"))


def test_down_in_shared_project_does_not_touch_the_stack(tmp_path, monkeypatch, capsys):
    fake_host(tmp_path, monkeypatch)
    mine = tmp_path / "b" / "observability"
    main(["init", "--dir", str(mine), "--service", "teste-b", "--join", "--yes"])
    called = []
    monkeypatch.setattr(cli, "compose", lambda *a, **k: called.append(a))
    main(["down", "--dir", str(mine)])
    assert not called and "compartilhada" in capsys.readouterr().out


def test_compose_in_shared_mode_uses_host_dir_and_project(tmp_path, monkeypatch):
    host = fake_host(tmp_path, monkeypatch)
    mine = tmp_path / "b" / "observability"
    main(["init", "--dir", str(mine), "--service", "teste-b", "--join", "--yes"])
    env = cli.read_env(mine / ".env")
    seen = {}

    class P:
        stdout = iter([])
        def wait(self): return 0
    monkeypatch.setattr(cli.docker, "ensure_ready", lambda: ["docker", "compose"])
    monkeypatch.setattr(cli.subprocess, "Popen", lambda full, **k: seen.setdefault("cmd", full) and P())
    cli.compose(mine, env, ["ps"])
    assert str(host["dir"]) in seen["cmd"] and "obskitpy-news-gov" in seen["cmd"]


def test_demo_is_regenerated_for_the_current_service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "wait_data", lambda env, timeout=0: True)
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    env = {"OBSKITPY_SERVICE": "um", "OTLP_GRPC_PORT": "1", "ES_PORT": "2"}
    cli.run_demo(env)
    cli.run_demo({**env, "OBSKITPY_SERVICE": "dois"})
    assert 'setup("dois")' in (tmp_path / "obskitpy_demo.py").read_text()


def test_dash_has_project_selector():
    from importlib import resources
    t = resources.files("obskitpy.templates")
    assert 'id="svc"' in t.joinpath("dash/index.html").read_text() and "<select" in t.joinpath("dash/index.html").read_text()
    js = t.joinpath("dash/app.js").read_text()
    for needle in ("state.service", "loadServices", "dash.service", "service="):
        assert needle in js


def test_run_warns_when_code_sends_a_different_service_name(tmp_path, monkeypatch, capsys):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "meu-projeto", "--local", "dash-obskitpy", "--yes"])
    monkeypatch.setattr(cli, "stack_reachable", lambda env: True)
    monkeypatch.setattr(cli, "collector_ready", lambda env: True)
    monkeypatch.setattr(cli, "wait_recent", lambda env, since, timeout=0: ["news-gov"])  # chegou, mas como outro nome
    monkeypatch.setattr(cli.subprocess, "call", lambda command, env: 0)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--dir", str(obs), "--", "python", "app.py"])
    out = capsys.readouterr().out
    assert exc.value.code == 0 and "'news-gov'" in out and "'meu-projeto'" in out
    assert "service=news-gov" in out and 'obskitpy.setup("meu-projeto")' in out


def test_dash_empty_state_suggests_other_projects():
    from importlib import resources
    js = resources.files("obskitpy.templates").joinpath("dash/app.js").read_text()
    assert "data-svc" in js and "Mas há dados de outros projetos" in js and "emptyShown" in js


# ---------- portal: CLI ----------

def test_portal_enable_writes_files_secrets_and_gitignore(tmp_path, monkeypatch):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "app", "--local", "dash-obskitpy", "--standalone", "--yes"])
    main(["portal", "enable", "--dir", str(obs), "--no-start", "--admin-email", "ops@empresa.com"])
    env = cli.read_env(obs / ".env")
    assert env["OBSKITPY_PORTAL"] == "1" and "portal" in env["COMPOSE_PROFILES"].split(",") and env["PORTAL_PORT"].isdigit()
    secrets = cli.read_env(obs / ".portal.env")
    assert len(secrets["OBSKITPY_SECRET_KEY"]) >= 48 and secrets["OBSKITPY_ADMIN_EMAIL"] == "ops@empresa.com"
    assert secrets["DATABASE_URL"].startswith("postgresql://obskitpy:") and secrets["OBSKITPY_PIP_SPEC"].startswith("obskitpy[portal]==")
    assert oct((obs / ".portal.env").stat().st_mode & 0o777) == "0o600"
    ignored = (obs / ".gitignore").read_text().splitlines()
    assert ".portal.env" in ignored
    assert "portal:" in (obs / "docker-compose.yml").read_text() and "location /api/" in (obs / "nginx.conf").read_text()
    first = secrets["OBSKITPY_SECRET_KEY"]
    main(["portal", "enable", "--dir", str(obs), "--no-start"])  # de novo: mantém os segredos
    assert cli.read_env(obs / ".portal.env")["OBSKITPY_SECRET_KEY"] == first


def test_portal_enable_requires_dash_and_own_stack(tmp_path):
    obs = tmp_path / "observability"
    main(["init", "--dir", str(obs), "--service", "app", "--local", "kibana", "--standalone", "--yes"])
    with pytest.raises(SystemExit):
        main(["portal", "enable", "--dir", str(obs), "--no-start"])


def test_compose_profiles_include_portal():
    assert cli.compose_profiles({"OBSKITPY_LOCAL": "dash-obskitpy"}) == "dash"
    assert cli.compose_profiles({"OBSKITPY_LOCAL": "ambos", "OBSKITPY_PORTAL": "1"}) == "kibana,dash,portal"


def test_dash_has_integrations_tab():
    from importlib import resources
    t = resources.files("obskitpy.templates")
    assert 'data-tab="integrations"' in t.joinpath("dash/index.html").read_text()
    js = t.joinpath("dash/app.js").read_text()
    for needle in ("papi(", "Entrar no portal", "obskitpy portal enable", "X-Obskitpy-Csrf"):
        assert needle in js


def test_nginx_resolves_upstreams_lazily():
    from importlib import resources
    conf = resources.files("obskitpy.templates").joinpath("nginx.conf").read_text()
    assert "resolver 127.0.0.11" in conf and "set $portal" in conf and "set $elasticsearch" in conf
