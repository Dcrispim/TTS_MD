from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class ServerError(Exception):
    """Rede indisponivel, ou o servidor respondeu com um erro de aplicacao."""


@dataclass
class ServerResult:
    mode: str
    output: str
    played: bool
    warnings: list[str] = field(default_factory=list)


def _url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


def server_health(host: str, port: int, timeout: float = 1.5) -> dict | None:
    try:
        with urllib.request.urlopen(_url(host, port, "/health"), timeout=timeout) as resp:
            if resp.status != 200:
                return None
            raw = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError):
        return None
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def check_server(host: str, port: int, timeout: float = 1.5) -> bool:
    """Probe rapido usado por --check antes de mandar o pedido de verdade."""
    return server_health(host, port, timeout) is not None


def send_to_server(
    host: str,
    port: int,
    text: str,
    *,
    lang: str | None,
    speed: float,
    persona: str | None = None,
    images: dict[str, dict] | None = None,
    timeout: float = 300.0,
) -> ServerResult:
    """Manda o markdown para um tts-md --serve falar. Bloqueia ate ele terminar
    de processar (o mesmo tempo que a sintese+reproducao levariam localmente).

    `persona` e' so o id: quem resolve a voz e' o catalogo local do proprio
    servidor (personas.json dele pode mapear o mesmo id para uma voz diferente
    da que essa maquina usaria).
    """
    body = {"text": text, "lang": lang, "speed": speed, "persona": persona}
    if images is not None:
        body["images"] = images
    payload = json.dumps(body).encode("utf-8")
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
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (BrokenPipeError, ConnectionResetError)):
            raise ServerError(
                f"Server at {host}:{port} closed the connection while receiving "
                f"the request ({len(payload) / 1024 / 1024:.1f} MB); it may exceed "
                "the server's video.max_body_mb limit."
            ) from exc
        raise ServerError(f"Could not reach server at {host}:{port}: {exc}") from exc

    return ServerResult(
        mode=body.get("mode", "single"),
        output=body.get("output", ""),
        played=bool(body.get("played", False)),
        warnings=[str(w) for w in body.get("warnings") or []],
    )
