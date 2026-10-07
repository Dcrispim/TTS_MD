from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PLAYERS = ("mpv", "aplay", "ffplay")
VIDEO_PLAYERS = ("mpv", "ffplay")

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "tts-md"
LAST_PLAYED_FILE = STATE_DIR / "last_played"
LAST_RUN_DIR = STATE_DIR / "last"


def record_last_played(path: Path, *, append: bool = False) -> None:
    """Guarda uma copia do que foi tocado, para --last reproduzir depois.

    Copia (e nao so o caminho) porque --temp apaga os arquivos logo depois.
    `append` acumula os segmentos de um mesmo --stream, na ordem.
    Melhor esforco: se o diretorio de estado nao puder ser escrito, --last
    simplesmente nao vai encontrar nada, sem quebrar a reproducao em si.
    """
    try:
        if not append:
            shutil.rmtree(LAST_RUN_DIR, ignore_errors=True)
        LAST_RUN_DIR.mkdir(parents=True, exist_ok=True)
        index = sum(1 for _ in LAST_RUN_DIR.iterdir()) + 1
        shutil.copy2(path, LAST_RUN_DIR / f"{index:04d}-{path.name}")
        LAST_PLAYED_FILE.write_text(str(path.resolve()), encoding="utf-8")
    except OSError:
        pass


def get_last_run() -> list[Path]:
    """Arquivos da ultima execucao tocada, na ordem (varios no --stream)."""
    try:
        files = sorted(p for p in LAST_RUN_DIR.iterdir() if p.is_file())
    except OSError:
        files = []
    if files:
        return files
    legacy = get_last_played()
    return [legacy] if legacy is not None and legacy.exists() else []


def get_last_played() -> Path | None:
    try:
        text = LAST_PLAYED_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    return Path(text)


def find_player(*, video: bool = False) -> tuple[str, str] | None:
    for player in VIDEO_PLAYERS if video else PLAYERS:
        executable = shutil.which(player)
        if executable:
            return player, executable
    return None


def missing_player_message(*, video: bool = False) -> str:
    if not video:
        return "No audio player found. Install mpv, aplay, or ffplay for --play."
    if shutil.which("aplay"):
        return (
            "aplay only plays audio and can't show the video. Install mpv or "
            "ffplay to use --play with --video."
        )
    return "No video player found. Install mpv or ffplay to use --play with --video."


def _command(player: str, executable: str, path: Path, video: bool = False) -> list[str]:
    if player == "mpv":
        return [executable, str(path)] if video else [executable, "--no-video", str(path)]
    if player == "aplay":
        return [executable, str(path)]
    if video:
        return [executable, "-autoexit", str(path)]
    return [executable, "-nodisp", "-autoexit", str(path)]


def play_audio(
    path: Path, *, video: bool = False, remember: bool = True, append: bool = False
) -> None:
    needs_video_player = video or path.suffix.lower() == ".mp4"
    found = find_player(video=needs_video_player)
    if not found:
        raise RuntimeError(missing_player_message(video=needs_video_player))

    player, executable = found
    result = subprocess.run(
        _command(player, executable, path, video),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # check=False antes engolia isso: o player falhava (device ocupado, sem
        # servidor de audio, etc.) e quem chamou via --serve/--host recebia
        # "played": true e um "[ok]" no log mesmo sem nenhum som ter saido.
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit code {result.returncode}"
        raise RuntimeError(f"{player} failed to play {path.name}: {tail}")

    if remember:
        record_last_played(path, append=append)


class MpvPlaylist:
    """Uma janela do mpv para o --stream --video inteiro.

    Cada segmento entra no fim da playlist; se o proximo ainda nao chegou,
    o --keep-open deixa o video pausado no ultimo quadro ate ele chegar.
    """

    POLL = 0.2

    def __init__(self, executable: str) -> None:
        self._dir = Path(tempfile.mkdtemp(prefix="tts-md-mpv-"))
        sock_path = self._dir / "ipc.sock"
        self._proc = subprocess.Popen(
            [
                executable, "--idle=yes", "--keep-open=yes", "--force-window=no",
                "--really-quiet", f"--input-ipc-server={sock_path}",
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._sock = self._connect(sock_path)
        self._reader = self._sock.makefile("rb")
        self._request = 0

    def _connect(self, sock_path: Path) -> socket.socket:
        deadline = time.monotonic() + 10
        while True:
            if self._proc.poll() is not None:
                raise RuntimeError("mpv exited before opening its IPC socket")
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(str(sock_path))
                return sock
            except OSError:
                sock.close()
                if time.monotonic() > deadline:
                    self.close()
                    raise RuntimeError("timed out waiting for mpv's IPC socket") from None
                time.sleep(0.05)

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def _command(self, *args: object) -> object:
        self._request += 1
        request_id = self._request
        payload = json.dumps({"command": list(args), "request_id": request_id})
        self._sock.sendall(payload.encode() + b"\n")
        while True:
            line = self._reader.readline()
            if not line:
                raise RuntimeError("mpv closed the IPC connection")
            reply = json.loads(line)
            if reply.get("request_id") != request_id:
                continue
            if reply.get("error") != "success":
                return None
            return reply.get("data")

    def append(self, path: Path) -> None:
        self._command("loadfile", str(path.resolve()), "append-play")
        if self._command("get_property", "eof-reached"):
            self._command("playlist-next", "weak")
            self._command("set_property", "pause", False)

    def finished(self) -> bool:
        if not self.alive or self._command("get_property", "idle-active"):
            return True
        pos = self._command("get_property", "playlist-pos")
        count = self._command("get_property", "playlist-count")
        return bool(self._command("get_property", "eof-reached")) and pos == (count or 0) - 1

    def wait_and_close(self) -> None:
        try:
            while not self.finished():
                time.sleep(self.POLL)
            if self.alive:
                self._command("quit")
        except (OSError, RuntimeError, ValueError):
            pass
        self.close()

    def close(self) -> None:
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        shutil.rmtree(self._dir, ignore_errors=True)


def replay(files: list[Path]) -> None:
    """Toca de novo uma execucao gravada por record_last_played."""
    mpv = shutil.which("mpv")
    if len(files) > 1 and mpv and any(f.suffix.lower() == ".mp4" for f in files):
        playlist = MpvPlaylist(mpv)
        for path in files:
            playlist.append(path)
        playlist.wait_and_close()
        return
    for path in files:
        play_audio(path, video=path.suffix.lower() == ".mp4", remember=False)


class QueuedPlayer:
    """Toca faixas em ordem numa thread separada, sem bloquear a sintese.

    No modo --stream isso deixa a primeira linha tocar enquanto as
    seguintes ainda estao sendo geradas.
    """

    def __init__(self, *, video: bool = False) -> None:
        self._video = video
        self._queue: queue.Queue[tuple[Path, bool] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._consume, daemon=True)
        self._started = False
        self._playlist: MpvPlaylist | None = None
        self._playlist_closed = False
        self._playlist_failed = False
        self._remembered = False

    def add(self, path: Path, *, video: bool | None = None) -> None:
        if not self._started:
            self._thread.start()
            self._started = True
        self._queue.put((path, self._video if video is None else video))

    def _consume(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                if self._playlist is not None:
                    self._playlist.wait_and_close()
                return
            path, video = item
            try:
                if self._play_in_window(path, video):
                    continue
                play_audio(path, video=video, append=self._remembered)
                self._remembered = True
            except RuntimeError as exc:
                # Sem isso uma falha aqui (thread separada, ninguem espera o
                # resultado por faixa) matava a thread e as proximas faixas da
                # fila nunca tocavam - e nada avisava.
                print(f"[tts-md] {exc}", file=sys.stderr)

    def _play_in_window(self, path: Path, video: bool) -> bool:
        if self._playlist_closed:
            return True
        if self._playlist is None:
            mpv = shutil.which("mpv")
            if not (video and self._video and mpv) or self._playlist_failed:
                return False
            try:
                self._playlist = MpvPlaylist(mpv)
            except RuntimeError as exc:
                self._playlist_failed = True
                print(f"[tts-md] {exc}; playing each track on its own", file=sys.stderr)
                return False
        if not self._playlist.alive:
            self._playlist_closed = True
            print("[tts-md] mpv window closed; skipping the remaining tracks", file=sys.stderr)
            return True
        try:
            self._playlist.append(path)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"mpv playlist failed: {exc}") from exc
        record_last_played(path, append=self._remembered)
        self._remembered = True
        return True

    def wait(self) -> None:
        """Espera a fila esvaziar e encerra a thread."""
        if not self._started:
            return
        self._queue.put(None)
        self._thread.join()
