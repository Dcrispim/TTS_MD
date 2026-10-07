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
from tts_md.models import AppConfig, VideoConfig
from tts_md import persona as persona_mod
from tts_md.visual import CueExtraction, extract_cues

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
    video: bool = False


class _Job:
    def __init__(
        self, text: str, lang: str | None, speed: float, persona: str | None = None
    ) -> None:
        self.text = text
        self.lang = lang
        self.speed = speed
        self.persona = persona
        self.extraction: CueExtraction | None = None
        self.images: dict | None = None
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
    suffix = ".mp4" if opts.video else ".wav"
    if opts.temp:
        scratch = Path(tempfile.mkdtemp(prefix="tts-md-serve-"))
        target = scratch if opts.stream else scratch / f"{stem}{suffix}"
        return target, scratch

    base = opts.output or Path("output")
    target = (base / stem) if opts.stream else (base / f"{stem}{suffix}")
    return target, None


def _process_job(job: _Job, engine: TTSEngine, opts: ServeOptions) -> None:
    # Import tardio: cli.py importa run_server/ServeOptions deste modulo no
    # topo do arquivo, entao importar _run_stream daqui no topo criaria um
    # ciclo. Neste ponto (job ja em execucao) o cli.py ja terminou de carregar.
    from tts_md.cli import _run_stream

    stem = _stem(job.text)
    target, scratch = _resolve_target(opts, stem)
    work_tmp = work_dir(stem)
    # Resolvido contra o catalogo desta maquina: o mesmo id pode apontar para
    # uma voz diferente do lado de quem mandou o pedido.
    voice_overrides = (
        persona_mod.get_voice_bundle(job.persona) if job.persona else None
    )
    try:
        if opts.video:
            job.result = _process_video(job, engine, opts, target, work_tmp, voice_overrides)
        elif opts.stream:
            _run_stream(
                engine,
                job.text,
                out_dir=target,
                lang=job.lang,
                play=opts.play,
                keep_temp=opts.keep_temp,
                tmp_dir=work_tmp,
                speed=job.speed,
                voice_overrides=voice_overrides,
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
                voice_overrides=voice_overrides,
            )
            job.result = {"mode": "single", "output": str(final), "played": opts.play}
    except Exception as exc:  # noqa: BLE001 - reportado ao cliente, servidor segue de pe
        job.error = str(exc)
    finally:
        if scratch is not None and not opts.keep_temp:
            shutil.rmtree(scratch, ignore_errors=True)
        job.done.set()


def _process_video(
    job: _Job,
    engine: TTSEngine,
    opts: ServeOptions,
    target: Path,
    work_tmp: Path,
    voice_overrides: dict[str, str] | None,
) -> dict:
    from tts_md.cli import _run_stream, _run_video
    from tts_md.visual.video import StreamVideo

    if opts.stream:
        video = StreamVideo(job.extraction, engine.config.video, job.images, work_dir=work_tmp)
        _run_stream(
            engine,
            job.text,
            out_dir=target,
            lang=job.lang,
            play=opts.play,
            keep_temp=opts.keep_temp,
            tmp_dir=work_tmp,
            speed=job.speed,
            voice_overrides=voice_overrides,
            video=video,
        )
        mode, warnings = "stream", video.warnings()
    else:
        warnings = _run_video(
            engine,
            job.text,
            output=target,
            audio=work_tmp / f"{target.stem}.wav",
            lang=job.lang,
            play=opts.play,
            keep_temp=opts.keep_temp,
            tmp_dir=work_tmp,
            speed=job.speed,
            voice_overrides=voice_overrides or {},
            config=engine.config,
            images=job.images,
        )
        mode = "single"
    return {
        "mode": mode,
        "output": str(target),
        "played": opts.play,
        "video": True,
        "warnings": warnings,
    }


def _make_handler(
    jobs: "queue.Queue[_Job]", opts: ServeOptions, video_cfg: VideoConfig
) -> type[BaseHTTPRequestHandler]:
    max_body = int(video_cfg.max_body_mb * 1024 * 1024)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # silencia o log padrao do http.server
            pass

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send_json(200, {"status": "ok", "video": opts.video})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/speak":
                self._send_json(404, {"error": "not found"})
                return

            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = -1
            if length < 0:
                self._send_json(400, {"error": "invalid Content-Length"})
                return
            if length > max_body:
                self.close_connection = True
                self._send_json(
                    413,
                    {
                        "error": f"request body of {length} bytes exceeds the "
                        f"server limit of {max_body} bytes (video.max_body_mb)"
                    },
                )
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            text = (payload.get("text") or "").strip()
            if not text:
                self._send_json(400, {"error": "missing 'text'"})
                return

            speed = float(payload.get("speed") or 1.0)
            job = _Job(text, payload.get("lang"), speed, payload.get("persona"))
            if opts.video:
                from tts_md.visual.video import UploadResolver, image_refs

                job.extraction = extract_cues(text)
                resolution = UploadResolver(payload.get("images")).resolve(
                    image_refs(job.extraction)
                )
                if resolution.missing:
                    listed = "; ".join(f"{r}: {why}" for r, why in resolution.missing.items())
                    self._send_json(
                        400,
                        {
                            "error": f"{len(resolution.missing)} image(s) referenced in "
                            f"the Markdown have no valid upload: {listed}",
                            "missing": resolution.missing,
                        },
                    )
                    return
                job.images = resolution.images
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

    server = ThreadingHTTPServer((host, port), _make_handler(jobs, opts, config.video))
    click.echo(f"Listening on {host}:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("Stopping.")
    finally:
        server.server_close()
