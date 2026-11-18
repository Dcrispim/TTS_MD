"""Leitura em voz alta com progresso salvo (--track/--start/--epub).

Cada linha do Markdown (um paragrafo, num livro) e' um bloco de leitura.
Uma thread sintetiza alguns blocos adiantados enquanto o principal toca o
atual num player controlavel, entao da para pausar, pular e parar pelo
teclado. Quando um bloco comeca a tocar, a posicao dele vai para o arquivo
de progresso: interrompida a leitura (tecla q, Ctrl+C, terminal fechado,
kill), a proxima execucao recomeca do inicio daquele mesmo paragrafo.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import groupby
from pathlib import Path

import click

from tts_md.audio.player import STATE_DIR, _command, find_player, missing_player_message
from tts_md.audio.playlist import slugify
from tts_md.engine import TTSEngine
from tts_md.models import SpeechBlock

TRACK_DIR = STATE_DIR / "tracks"
PREFETCH = 3
SNIPPET = 80
POLL = 0.1

KEYS_HELP = "[space/p] pause  [n] next block  [s] next section  [q] quit (progress is saved)"
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")


# ---------------------------------------------------------------------------
# Progresso
# ---------------------------------------------------------------------------

@dataclass
class Progress:
    source: str
    index: int          # bloco atual (0-based) na ordem de leitura
    total: int
    line_no: int        # linha do Markdown desse bloco
    text: str           # inicio do bloco, para reencontrar se o arquivo mudar
    finished: bool = False
    updated: str = ""


def track_path(source: Path) -> Path:
    resolved = str(source.resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:10]
    return TRACK_DIR / f"{slugify(source.stem) or 'doc'}-{digest}.json"


def load_progress(source: Path) -> Progress | None:
    try:
        data = json.loads(track_path(source).read_text(encoding="utf-8"))
        return Progress(**data)
    except (OSError, ValueError, TypeError):
        return None


def save_progress(source: Path, progress: Progress) -> None:
    """Grava atomicamente: um kill no meio nunca deixa um JSON pela metade."""
    progress.updated = datetime.now().isoformat(timespec="seconds")
    path = track_path(source)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(progress), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        _say(f"warning: could not save progress to {path}: {exc}", err=True)


def heading_lines(markdown: str) -> set[int]:
    """Linhas (1-based) do Markdown que sao titulos `#`: inicio de secao."""
    return {n for n, line in enumerate(markdown.splitlines(), start=1) if HEADING_RE.match(line)}


def section_starts(groups: list[list[SpeechBlock]], lines: set[int]) -> list[int]:
    """Indices dos blocos que abrem secao: o primeiro bloco em/apos cada linha marcada
    (a linha marcada pode ter sido filtrada, ex.: um '* * *')."""
    starts: set[int] = set()
    line_nos = [group[0].line_no for group in groups]
    for line in lines:
        for i, line_no in enumerate(line_nos):
            if line_no >= line:
                starts.add(i)
                break
    return sorted(starts)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _title(group: list[SpeechBlock]) -> str:
    return " ".join(block.text for block in group)


def resume_index(groups: list[list[SpeechBlock]], progress: Progress) -> tuple[int, str | None]:
    """Acha onde retomar. Devolve (indice, aviso ou None)."""
    if progress.finished:
        return 0, "this file was already read to the end; starting over"
    # Sem espacos na comparacao: o conserto de palavras partidas ("tom ei" ->
    # "tomei") muda o texto salvo antes dele sem mudar o paragrafo.
    snippet = _squash(progress.text[:SNIPPET])
    for i, group in enumerate(groups):
        if group[0].line_no == progress.line_no and _squash(_title(group)).startswith(snippet):
            return i, None
    # O arquivo mudou e as linhas andaram: procura pelo texto.
    for i, group in enumerate(groups):
        if snippet and _squash(_title(group)).startswith(snippet):
            return i, None
    for i, group in enumerate(groups):
        if group[0].line_no >= progress.line_no:
            return i, (
                "the file changed since the last reading and the saved paragraph "
                f"was not found; resuming near line {progress.line_no}"
            )
    return 0, "saved position is past the end of the file; starting over"


# ---------------------------------------------------------------------------
# Terminal, sinais e player
# ---------------------------------------------------------------------------

def _say(message: str, *, err: bool = False) -> None:
    # Depois de um SIGHUP o terminal ja nao existe: escrever nele da EIO.
    try:
        click.echo(message, err=err)
    except OSError:
        pass


class ReadingStopped(Exception):
    def __init__(self, reason: str, exit_code: int = 0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


class Keyboard:
    """Le teclas soltas (modo cbreak) sem bloquear; Ctrl+C continua gerando SIGINT."""

    def __init__(self) -> None:
        self._fd: int | None = None
        self._saved = None

    def __enter__(self) -> Keyboard:
        try:
            if sys.stdin.isatty():
                import termios
                import tty

                self._fd = sys.stdin.fileno()
                self._saved = termios.tcgetattr(self._fd)
                tty.setcbreak(self._fd)
        except (OSError, ImportError):
            self._fd = None
        return self

    @property
    def interactive(self) -> bool:
        return self._fd is not None

    def read_key(self, timeout: float) -> str | None:
        if self._fd is None:
            return None
        try:
            ready, _, _ = select.select([self._fd], [], [], timeout)
            if not ready:
                return None
            data = os.read(self._fd, 1)
        except OSError:
            # Terminal sumiu (fechado): sem teclado daqui em diante.
            self._fd = None
            return None
        return data.decode("utf-8", errors="ignore").lower() or None

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None and self._saved is not None:
            try:
                import termios

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            except Exception:  # noqa: BLE001 - terminal fechado, nada a restaurar
                pass


class _Signals:
    """SIGHUP (terminal fechado) e SIGTERM viram ReadingStopped no thread principal."""

    CODES = {"SIGHUP": 129, "SIGTERM": 143}

    def __enter__(self) -> _Signals:
        self._previous: dict[int, object] = {}
        for name, code in self.CODES.items():
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            self._previous[signum] = signal.signal(signum, self._handler(name, code))
        return self

    @staticmethod
    def _handler(name: str, code: int) -> Callable:
        def handle(signum: int, frame: object) -> None:
            if name == "SIGHUP":
                # O terminal fechou: tudo que ainda for escrito (inclusive o
                # flush do Python ao sair) daria EIO e trocaria o exit code.
                try:
                    devnull = os.open(os.devnull, os.O_WRONLY)
                    os.dup2(devnull, 1)
                    os.dup2(devnull, 2)
                    os.close(devnull)
                except OSError:
                    pass
            raise ReadingStopped(name, code)

        return handle

    def __exit__(self, *exc: object) -> None:
        for signum, handler in self._previous.items():
            signal.signal(signum, handler)


class ControlledPlayer:
    """Um processo de player por bloco, que da para pausar (SIGSTOP) e matar."""

    def __init__(self) -> None:
        found = find_player()
        if found is None:
            raise RuntimeError(missing_player_message())
        self._player, self._exe = found
        self.proc: subprocess.Popen | None = None
        self.paused = False

    def start(self, path: Path) -> None:
        cmd = _command(self._player, self._exe, path)
        if self._player == "mpv":
            cmd.insert(1, "--no-terminal")
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.paused = False

    def toggle_pause(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        os.kill(self.proc.pid, signal.SIGCONT if self.paused else signal.SIGSTOP)
        self.paused = not self.paused

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            if self.paused:
                os.kill(proc.pid, signal.SIGCONT)
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass
        self.paused = False


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

class _Prefetcher(threading.Thread):
    """Sintetiza os blocos adiantados, no maximo PREFETCH na frente do player."""

    def __init__(self, engine: TTSEngine, groups, start: int, work: Path, *, speed: float, keep_temp: bool):
        super().__init__(daemon=True)
        self.engine = engine
        self.groups = groups
        self.start_at = start
        self.work = work
        self.speed = speed
        self.keep_temp = keep_temp
        self.width = max(4, len(str(len(groups))))
        self.out: queue.Queue = queue.Queue(maxsize=PREFETCH)
        self.halt = threading.Event()

    def _put(self, item: object) -> bool:
        while not self.halt.is_set():
            try:
                self.out.put(item, timeout=POLL)
                return True
            except queue.Full:
                continue
        return False

    def run(self) -> None:
        try:
            for i in range(self.start_at, len(self.groups)):
                if self.halt.is_set():
                    return
                n = i + 1
                path = self.engine.synthesize_line(
                    self.groups[i], self.work, n, speed=self.speed, width=self.width
                )
                if not self.keep_temp:
                    # Pedacos por bloco ja estao dentro do WAV da linha.
                    for part in self.work.glob(f"{n:04d}_*.wav"):
                        part.unlink(missing_ok=True)
                if not self._put((i, path)):
                    return
            self._put(None)
        except BaseException as exc:  # noqa: BLE001 - repassado ao thread principal
            self._put(exc)


def read_aloud(
    engine: TTSEngine,
    blocks: list[SpeechBlock],
    *,
    source: Path | None,
    track: bool,
    start_over: bool,
    rewind: int = 0,
    speed: float,
    keep_temp: bool,
    work: Path,
    sections: set[int] | None = None,
) -> int:
    """Le em voz alta os blocos ja parseados, linha a linha. Devolve o exit code."""
    if not blocks:
        raise click.ClickException("No speakable content found after parsing.")
    groups = [list(g) for _, g in groupby(blocks, key=lambda b: b.line_no)]
    total = len(groups)
    starts = section_starts(groups, sections or set())

    previous = load_progress(source) if source is not None else None
    tracking = source is not None and (track or previous is not None)
    start = 0
    if previous is not None and not start_over:
        start, warning = resume_index(groups, previous)
        if warning:
            _say(f"warning: {warning}", err=True)
        if rewind and start and not previous.finished:
            saved, start = start, max(0, start - rewind)
            _say(f"Going back {saved - start} block(s) from the saved position (block {saved + 1}).")
        if start and not warning:
            _say(f"Resuming at block {start + 1}/{total} ({previous.updated}). Use --start to begin again.")
    elif previous is not None:
        _say("--start: ignoring the saved progress and reading from the beginning.")
    if tracking:
        _say(f"Progress: {track_path(source)}")

    player = ControlledPlayer()
    work.mkdir(parents=True, exist_ok=True)
    prefetch = _Prefetcher(engine, groups, start, work, speed=speed, keep_temp=keep_temp)
    current = start
    exit_code = 0
    finished = False

    with _Signals(), Keyboard() as keyboard:
        if keyboard.interactive:
            _say(KEYS_HELP)
        prefetch.start()
        try:
            while True:
                item = _next_item(prefetch, keyboard)
                if item is None:
                    finished = True
                    break
                if isinstance(item, BaseException):
                    raise RuntimeError(f"synthesis failed: {item}") from item
                current, path = item
                group = groups[current]
                if tracking:
                    save_progress(source, Progress(
                        source=str(source.resolve()),
                        index=current,
                        total=total,
                        line_no=group[0].line_no,
                        text=_title(group)[:SNIPPET],
                    ))
                _say(f"[{current + 1}/{total} {100 * (current + 1) // total}%] {_title(group)}")
                action = _play_block(player, keyboard, path)
                if not keep_temp:
                    path.unlink(missing_ok=True)
                if action == "s":
                    target = next((i for i in starts if i > current), None)
                    if target is None:
                        _say("  [no further section]")
                        continue
                    _say(f"  [skipping to block {target + 1}/{total}...]")
                    prefetch = _restart(prefetch, engine, groups, target, work,
                                        speed=speed, keep_temp=keep_temp)
        except KeyboardInterrupt:
            exit_code = 130
            _say("\nInterrupted (Ctrl+C).", err=True)
        except ReadingStopped as stop:
            exit_code = stop.exit_code
            if stop.reason == "q":
                _say("Stopped.")
            else:
                _say(f"\nInterrupted ({stop.reason}).", err=True)
        finally:
            player.stop()
            prefetch.halt.set()
            prefetch.join(timeout=2)
            if not keep_temp:
                shutil.rmtree(work, ignore_errors=True)

    if tracking:
        if finished:
            save_progress(source, Progress(
                source=str(source.resolve()), index=total, total=total,
                line_no=groups[-1][0].line_no, text="", finished=True,
            ))
            _say("Finished. The next reading starts from the beginning.")
        else:
            _say(f"Progress saved at block {current + 1}/{total}; run again to continue.")
    return exit_code


def _restart(old: _Prefetcher, engine: TTSEngine, groups, start: int, work: Path,
             *, speed: float, keep_temp: bool) -> _Prefetcher:
    """Para a sintese adiantada (termina o bloco em andamento) e recomeca em `start`."""
    old.halt.set()
    old.join()
    while True:
        try:
            item = old.out.get_nowait()
        except queue.Empty:
            break
        if isinstance(item, tuple) and not keep_temp:
            item[1].unlink(missing_ok=True)
    new = _Prefetcher(engine, groups, start, work, speed=speed, keep_temp=keep_temp)
    new.start()
    return new


def _next_item(prefetch: _Prefetcher, keyboard: Keyboard) -> object:
    """Espera o proximo bloco sintetizado, ainda atendendo o q enquanto isso."""
    while True:
        try:
            return prefetch.out.get(timeout=POLL)
        except queue.Empty:
            pass
        if keyboard.interactive and keyboard.read_key(0) == "q":
            raise ReadingStopped("q", 0)
        if not prefetch.is_alive() and prefetch.out.empty():
            return None


def _play_block(player: ControlledPlayer, keyboard: Keyboard, path: Path) -> str | None:
    """Toca um bloco. Devolve "s" se o usuario pediu a proxima secao."""
    player.start(path)
    try:
        while player.proc is not None and player.proc.poll() is None:
            key = keyboard.read_key(POLL) if keyboard.interactive else None
            if key is None:
                if not keyboard.interactive:
                    player.proc.wait()
                continue
            if key in (" ", "p"):
                player.toggle_pause()
                _say("  [paused - space to continue]" if player.paused else "  [playing]")
            elif key in ("n", "s"):
                player.stop()
                return key if key == "s" else None
            elif key == "q":
                raise ReadingStopped("q", 0)
        if player.proc is not None and player.proc.returncode not in (0, None):
            _say(f"warning: player exited with code {player.proc.returncode} on {path.name}", err=True)
    finally:
        player.stop()
