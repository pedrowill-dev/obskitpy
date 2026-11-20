"""Cliente mínimo da API de saved objects do Kibana (stdlib)."""
import json
import time
import urllib.error
import urllib.request
import uuid


class KibanaError(RuntimeError):
    pass


def _request(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"kbn-xsrf": "obskit", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise KibanaError(f"{url}: HTTP {exc.code} {exc.read().decode(errors='replace')[:300]}") from exc
    except urllib.error.URLError as exc:
        raise KibanaError(f"{url}: {exc.reason}") from exc


def wait_ready(base: str, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            if json.loads(_request(f"{base}/api/status", timeout=5))["status"]["overall"]["level"] == "available":
                return
        except (KibanaError, ValueError, KeyError):
            pass
        if time.time() > deadline:
            raise KibanaError(f"Kibana não ficou disponível em {base} (timeout {timeout}s)")
        time.sleep(3)


def import_ndjson(base: str, ndjson: str) -> int:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"dashboard.ndjson\"\r\n"
            f"Content-Type: application/ndjson\r\n\r\n{ndjson}\r\n--{boundary}--\r\n").encode()
    out = json.loads(_request(f"{base}/api/saved_objects/_import?overwrite=true", body,
                              {"Content-Type": f"multipart/form-data; boundary={boundary}"}))
    if not out.get("success"):
        raise KibanaError(f"importação falhou: {json.dumps(out.get('errors'), ensure_ascii=False)[:500]}")
    return out.get("successCount", 0)


def export_dashboard(base: str, dashboard_id: str) -> str:
    body = json.dumps({"objects": [{"type": "dashboard", "id": dashboard_id}],
                       "includeReferencesDeep": True, "excludeExportDetails": True}).encode()
    return _request(f"{base}/api/saved_objects/_export", body, {"Content-Type": "application/json"}).decode()
