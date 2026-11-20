import json

import pytest

import socket

from obskitpy import cli, dashboard, errors, ui
from obskitpy.cli import main


@pytest.fixture(autouse=True)
def no_docker(monkeypatch):
    """Os testes não dependem do Docker da máquina."""
    monkeypatch.setattr(cli, "doctor_checks", lambda ports=None: [("Docker", "ok", "fake")])


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
    assert "pedidos-observability" in (obs / "dash" / "config.js").read_text()  # link para o Kibana


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
    monkeypatch.setattr(cli, "wait_data", lambda env, timeout=0: True)
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
