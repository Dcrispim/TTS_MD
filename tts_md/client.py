from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


class ServerError(Exception):
    """Rede indisponivel, ou o servidor respondeu com um erro de aplicacao."""


@dataclass
class ServerResult:
    mode: str
    output: str
    played: bool


def _url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


def check_server(host: str, port: int, timeout: float = 1.5) -> bool:
    """Probe rapido usado por --check antes de mandar o pedido de verdade."""
    try:
        with urllib.request.urlopen(_url(host, port, "/health"), timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def send_to_server(
    host: str,
    port: int,
    text: str,
    *,
    lang: str | None,
    speed: float,
    timeout: float = 300.0,
) -> ServerResult:
    """Manda o markdown para um tts-md --serve falar. Bloqueia ate ele terminar
    de processar (o mesmo tempo que a sintese+reproducao levariam localmente).
    """
    payload = json.dumps({"text": text, "lang": lang, "speed": speed}).encode("utf-8")
    request = urllib.request.Request(
        _url(host, port, "/speak"),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("error", detail)
        except json.JSONDecodeError:
            pass
        raise ServerError(f"Server at {host}:{port} failed: {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise ServerError(f"Could not reach server at {host}:{port}: {exc}") from exc

    return ServerResult(
        mode=body.get("mode", "single"),
        output=body.get("output", ""),
        played=bool(body.get("played", False)),
    )
