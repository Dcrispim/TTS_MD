from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

TARGET_SAMPLE_RATE = 22050


def require_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found. Install ffmpeg to concatenate audio files."
        )
    return ffmpeg


def normalize_sample_rate(
    input_path: Path,
    output_path: Path,
    sample_rate: int = TARGET_SAMPLE_RATE,
) -> Path:
    ffmpeg = require_ffmpeg()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(input_path),
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )
    return output_path


def concat_audio(
    wav_files: list[Path],
    output: Path,
    *,
    scratch_dir: Path | None = None,
) -> Path:
    if not wav_files:
        raise ValueError("No audio files to concatenate.")

    ffmpeg = require_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)

    # mkdtemp em vez de um `.normalized` de nome fixo: duas execucoes gravando
    # no mesmo diretorio de saida sobrescreviam os arquivos normalizadas uma da
    # outra, e o rmtree final de uma truncava o audio da outra sem erro nenhum.
    base = scratch_dir if scratch_dir is not None else output.parent
    base.mkdir(parents=True, exist_ok=True)
    normalized_dir = Path(tempfile.mkdtemp(prefix=".normalized-", dir=base))
    normalized_files: list[Path] = []

    for index, wav in enumerate(wav_files, start=1):
        norm_path = normalized_dir / f"{index:03d}.wav"
        normalize_sample_rate(wav, norm_path, TARGET_SAMPLE_RATE)
        normalized_files.append(norm_path)

    concat_list = normalized_dir / "concat.txt"
    with concat_list.open("w", encoding="utf-8") as f:
        for wav in normalized_files:
            escaped = str(wav.resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    suffix = output.suffix.lower()
    if suffix == ".mp3":
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-codec:a",
                "libmp3lame",
                str(output),
            ],
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                str(output),
            ],
            check=True,
            capture_output=True,
        )

    shutil.rmtree(normalized_dir, ignore_errors=True)
    return output


def cleanup_temp(tmp_dir: Path, *, keep_files: list[Path] | None = None) -> None:
    keep = {p.resolve() for p in (keep_files or [])}
    if not tmp_dir.exists():
        return
    for path in tmp_dir.iterdir():
        if path.resolve() in keep:
            continue
        if path.is_file():
            path.unlink(missing_ok=True)

    # O diretorio e proprio desta execucao (veja engine.work_dir): se esvaziou,
    # nao ha por que deixar a casca para tras.
    if not any(tmp_dir.iterdir()):
        tmp_dir.rmdir()
