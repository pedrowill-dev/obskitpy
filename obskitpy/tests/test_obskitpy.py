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
    main(["init"])
    out = capsys.readouterr().out
    assert "OBSKITPY_SERVICE=meu-robo" in (tmp_path / "observability" / ".env").read_text()
    assert "falta o Docker" in out and 'obskitpy.setup("meu-robo")' in out


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
