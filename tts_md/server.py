from __future__ import annotations

import json
import queue
import shutil
import tempfile
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import click

from tts_md.audio.playlist import slugify
from tts_md.engine import TTSEngine, work_dir
from tts_md.models import AppConfig

DEFAULT_PORT = 8420


@dataclass
class ServeOptions:
    """As flags de execucao (--play/--stream/--temp/--output/--keep-temp) sao
    fixadas na inicializacao do servidor e valem para todo pedido recebido -
    quem manda o texto so escolhe o conteudo (lang/speed), nao onde o audio
    para (isso e' uma decisao de quem opera a maquina que fala).
    """

    play: bool
    stream: bool
    temp: bool
    output: Path | None
    keep_temp: bool


class _Job:
    def __init__(self, text: str, lang: str | None, speed: float) -> None:
        self.text = text
        self.lang = lang
        self.speed = speed
        self.done = threading.Event()
        self.result: dict | None = None
        self.error: str | None = None


def _stem(text: str) -> str:
    return slugify(text) or "text"


def _resolve_target(opts: ServeOptions, stem: str) -> tuple[Path, Path | None]:
    """Destino deste pedido e, com --temp, o diretorio descartavel que o contem.

    Ao contrario do modo local (que so processa uma fonte por execucao), o
    servidor atende varios pedidos diferentes ao longo da vida dele, entao
    cada um ganha um destino proprio dentro da base (--output ou output/),
    nomeado por um slug do texto recebido.
    """
    if opts.temp:
        scratch = Path(tempfile.mkdtemp(prefix="tts-md-serve-"))
        target = scratch if opts.stream else scratch / f"{stem}.wav"
        return target, scratch

    base = opts.output or Path("output")
    target = (base / stem) if opts.stream else (base / f"{stem}.wav")
    return target, None


def _process_job(job: _Job, engine: TTSEngine, opts: ServeOptions) -> None:
    # Import tardio: cli.py importa run_server/ServeOptions deste modulo no
    # topo do arquivo, entao importar _run_stream daqui no topo criaria um
    # ciclo. Neste ponto (job ja em execucao) o cli.py ja terminou de carregar.
    from tts_md.cli import _run_stream

    stem = _stem(job.text)
    target, scratch = _resolve_target(opts, stem)
    work_tmp = work_dir(stem)
    try:
        if opts.stream:
            _run_stream(
                engine,
                job.text,
                out_dir=target,
                lang=job.lang,
                play=opts.play,
                keep_temp=opts.keep_temp,
                tmp_dir=work_tmp,
                speed=job.speed,
            )
            job.result = {"mode": "stream", "output": str(target), "played": opts.play}
        else:
            final = engine.run(
                job.text,
                output=target,
                default_lang=job.lang,
                play=opts.play,
                keep_temp=opts.keep_temp,
                tmp_dir=work_tmp,
                speed=job.speed,
            )
            job.result = {"mode": "single", "output": str(final), "played": opts.play}
    except Exception as exc:  # noqa: BLE001 - reportado ao cliente, servidor segue de pe
        job.error = str(exc)
    finally:
        if scratch is not None and not opts.keep_temp:
            shutil.rmtree(scratch, ignore_errors=True)
        job.done.set()


def _make_handler(jobs: "queue.Queue[_Job]") -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # silencia o log padrao do http.server
            pass

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send_json(200, {"status": "ok"})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/speak":
                self._send_json(404, {"error": "not found"})
                return

            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON body"})
                return

            text = (payload.get("text") or "").strip()
            if not text:
                self._send_json(400, {"error": "missing 'text'"})
                return

            speed = float(payload.get("speed") or 1.0)
            job = _Job(text, payload.get("lang"), speed)
            jobs.put(job)
            # Enfileirado: se outro pedido estiver falando, este espera a vez
            # dele chegar na fila antes de ser processado.
            job.done.wait()

            if job.error is not None:
                self._send_json(500, {"error": job.error})
            else:
                self._send_json(200, job.result)

        def _send_json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def run_server(config: AppConfig, opts: ServeOptions, host: str, port: int) -> None:
    """Loop que escuta na rede e sintetiza+toca cada pedido, um de cada vez."""
    engine = TTSEngine(config)
    jobs: "queue.Queue[_Job]" = queue.Queue()

    def worker() -> None:
        while True:
            job = jobs.get()
            _process_job(job, engine, opts)
            if job.error is not None:
                click.echo(f"[erro] {job.text[:60]!r}: {job.error}")
            else:
                click.echo(f"[ok] {job.text[:60]!r} -> {job.result['output']}")

    threading.Thread(target=worker, daemon=True).start()

    server = ThreadingHTTPServer((host, port), _make_handler(jobs))
    click.echo(f"Listening on {host}:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("Stopping.")
    finally:
        server.server_close()
