import json

import pytest

from obskitpy import dashboard, errors
from obskitpy.cli import main


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
