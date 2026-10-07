from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

PLAYERS = ("mpv", "aplay", "ffplay")
VIDEO_PLAYERS = ("mpv", "ffplay")

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "tts-md"
LAST_PLAYED_FILE = STATE_DIR / "last_played"


def record_last_played(path: Path) -> None:
    """Grava o audio tocado por ultimo, para --last reproduzir depois.

    Melhor esforco: se o diretorio de estado nao puder ser escrito, --last
    simplesmente nao vai encontrar nada, sem quebrar a reproducao em si.
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        LAST_PLAYED_FILE.write_text(str(path.resolve()), encoding="utf-8")
    except OSError:
        pass


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


def play_audio(path: Path, *, video: bool = False) -> None:
    found = find_player(video=video)
    if not found:
        raise RuntimeError(missing_player_message(video=video))

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

    record_last_played(path)


class QueuedPlayer:
    """Toca faixas em ordem numa thread separada, sem bloquear a sintese.

    No modo --stream isso deixa a primeira linha tocar enquanto as
    seguintes ainda estao sendo geradas.
    """

    def __init__(self, *, video: bool = False) -> None:
        self._video = video
        self._queue: queue.Queue[Path | None] = queue.Queue()
        self._thread = threading.Thread(target=self._consume, daemon=True)
        self._started = False

    def add(self, path: Path) -> None:
        if not self._started:
            self._thread.start()
            self._started = True
        self._queue.put(path)

    def _consume(self) -> None:
        while True:
            path = self._queue.get()
            if path is None:
                return
            try:
                play_audio(path, video=self._video)
            except RuntimeError as exc:
                # Sem isso uma falha aqui (thread separada, ninguem espera o
                # resultado por faixa) matava a thread e as proximas faixas da
                # fila nunca tocavam - e nada avisava.
                print(f"[tts-md] {exc}", file=sys.stderr)

    def wait(self) -> None:
        """Espera a fila esvaziar e encerra a thread."""
        if not self._started:
            return
        self._queue.put(None)
        self._thread.join()
