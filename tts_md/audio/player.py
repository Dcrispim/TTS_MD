from __future__ import annotations

import queue
import shutil
import subprocess
import threading
from pathlib import Path

PLAYERS = ("mpv", "aplay", "ffplay")


def find_player() -> tuple[str, str] | None:
    for player in PLAYERS:
        executable = shutil.which(player)
        if executable:
            return player, executable
    return None


def _command(player: str, executable: str, path: Path) -> list[str]:
    if player == "mpv":
        return [executable, "--no-video", str(path)]
    if player == "aplay":
        return [executable, str(path)]
    return [executable, "-nodisp", "-autoexit", str(path)]


def play_audio(path: Path) -> None:
    found = find_player()
    if not found:
        raise RuntimeError(
            "No audio player found. Install mpv, aplay, or ffplay for --play."
        )

    player, executable = found
    subprocess.run(_command(player, executable, path), check=False)


class QueuedPlayer:
    """Toca faixas em ordem numa thread separada, sem bloquear a sintese.

    No modo --stream isso deixa a primeira linha tocar enquanto as
    seguintes ainda estao sendo geradas.
    """

    def __init__(self) -> None:
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
            play_audio(path)

    def wait(self) -> None:
        """Espera a fila esvaziar e encerra a thread."""
        if not self._started:
            return
        self._queue.put(None)
        self._thread.join()
