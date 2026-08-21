from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Iterator
from datetime import datetime
from itertools import groupby
from pathlib import Path

from tts_md.models import AppConfig, SpeechBlock, StreamTrack
from tts_md.parsers import PARSERS
from tts_md.parsers.codeblock import CodeBlockParser
from tts_md.parsers.langindex import LangIndexParser
from tts_md.parsers.table import TableParser
from tts_md.audio.ffmpeg import (
    TARGET_SAMPLE_RATE,
    cleanup_temp,
    concat_audio,
    normalize_sample_rate,
)
from tts_md.audio.player import play_audio
from tts_md.audio.playlist import M3UWriter, slugify, track_name
from tts_md.tts.router import TTSRouter

SPEAKABLE_RE = re.compile(r"\w", re.UNICODE)

WORK_ROOT = Path(tempfile.gettempdir()) / "tts-md"
LABEL_SLICE = 15


def text_label(text: str) -> str:
    """Rotulo de uma execucao vinda de --text: o inicio do proprio texto."""
    return text[:LABEL_SLICE]


def work_dir(label: str) -> Path:
    """Diretorio de trabalho da execucao: /tmp/tts-md/<timestamp>-<label>.

    Antes era um `tmp/` fixo no diretorio atual, entao duas execucoes ao mesmo
    tempo escreviam nos mesmos 001.wav, 002.wav... e uma sobrescrevia os WAVs
    da outra no meio do ffmpeg.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = slugify(label)
    name = f"{stamp}-{slug}" if slug else stamp

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    path = WORK_ROOT / name
    try:
        # mkdir sem exist_ok e' atomico: quem perder a corrida cai no desempate.
        path.mkdir()
    except FileExistsError:
        path = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=WORK_ROOT))
    return path


class TTSEngine:
    def __init__(self, config: AppConfig):
        self.config = config
        self.router = TTSRouter(config)
        self._code_parser = CodeBlockParser()
        self._lang_index = LangIndexParser()
        self._table_parser = TableParser()

    def read_markdown(self, source: Path | str) -> str:
        if isinstance(source, Path):
            return source.read_text(encoding="utf-8")
        path = Path(source)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return str(source)

    def parse_markdown(
        self,
        text: str,
        *,
        default_lang: str | None = None,
    ) -> list[SpeechBlock]:
        lang = default_lang or self.config.default_lang
        blocks: list[SpeechBlock] = []
        in_code_block = False

        lines = text.splitlines()
        index = 0
        while index < len(lines):
            line_no = index + 1
            line = lines[index].rstrip("\n")

            if in_code_block:
                if self._code_parser.toggles_block(line, in_code_block=True):
                    in_code_block = False
                index += 1
                continue

            if self._code_parser.toggles_block(line, in_code_block=False):
                in_code_block = True
                index += 1
                continue

            if not line.strip():
                index += 1
                continue

            # Tabela: exige olhar a linha seguinte (separador), entao nao cabe
            # no loop generico de _parse_line. Uma fala so para a tabela toda.
            next_line = lines[index + 1] if index + 1 < len(lines) else None
            if self._table_parser.starts_table(line, next_line):
                span = self._table_parser.table_length(lines, index)
                announcement = self._table_parser.announcement(default_lang=lang)
                announcement.line_no = line_no
                blocks.append(announcement)
                index += span
                continue

            parsed = self._parse_line(line, default_lang=lang)
            # O indice de termos vale para toda linha, entao roda depois do
            # parser que a venceu, sobre o texto que sobrou no idioma padrao.
            parsed = self._lang_index.refine(parsed, default_lang=lang)
            for block in parsed:
                block.line_no = line_no
            blocks.extend(parsed)
            index += 1

        return self._filter_blocks(blocks)

    def _parse_line(self, line: str, *, default_lang: str) -> list[SpeechBlock]:
        for parser in PARSERS:
            if isinstance(parser, CodeBlockParser):
                continue
            if parser.match(line, in_code_block=False):
                return parser.parse(line, default_lang=default_lang)

        text = line.strip()
        if text:
            return [SpeechBlock(text=text, lang=default_lang)]
        return []

    @staticmethod
    def _filter_blocks(blocks: list[SpeechBlock]) -> list[SpeechBlock]:
        filtered: list[SpeechBlock] = []
        for block in blocks:
            if not block.speak:
                continue
            text = block.text.strip()
            # Blocos so com pontuacao fonemizam para vazio e quebram o TTS.
            if not SPEAKABLE_RE.search(text):
                continue
            filtered.append(
                SpeechBlock(
                    text=text,
                    lang=block.lang,
                    speak=block.speak,
                    voice=block.voice,
                    speed=block.speed,
                    pause_after=block.pause_after,
                    line_no=block.line_no,
                )
            )
        return filtered

    def synthesize_blocks(
        self,
        blocks: list[SpeechBlock],
        tmp_dir: Path,
        *,
        speed: float = 1.0,
    ) -> list[Path]:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        wav_files: list[Path] = []

        for index, block in enumerate(blocks, start=1):
            out_path = tmp_dir / f"{index:03d}.wav"
            generated = self.router.synthesize(block, out_path, speed=speed)
            wav_files.append(generated)

            if block.pause_after > 0:
                pause_path = tmp_dir / f"{index:03d}_pause.wav"
                self._create_silence(pause_path, block.pause_after)
                wav_files.append(pause_path)

        return wav_files

    def _synthesize_group(
        self,
        blocks: list[SpeechBlock],
        tmp_dir: Path,
        index: int,
        *,
        speed: float = 1.0,
    ) -> list[Path]:
        """Sintetiza os blocos de uma unica linha do Markdown."""
        parts: list[Path] = []
        last = len(blocks) - 1

        for position, block in enumerate(blocks):
            out_path = tmp_dir / f"{index:04d}_{position:02d}.wav"
            parts.append(self.router.synthesize(block, out_path, speed=speed))

            # Silencio no fim da faixa nao serve: quem pausa entre linhas e o player.
            if block.pause_after > 0 and position != last:
                pause_path = tmp_dir / f"{index:04d}_{position:02d}_pause.wav"
                self._create_silence(pause_path, block.pause_after)
                parts.append(pause_path)

        return parts

    def run_stream(
        self,
        text: str,
        *,
        out_dir: Path,
        default_lang: str | None = None,
        keep_temp: bool = False,
        tmp_dir: Path | None = None,
        speed: float = 1.0,
    ) -> Iterator[StreamTrack]:
        """Gera um audio por linha do Markdown, entregando cada faixa assim que fica pronta.

        Recebe o Markdown ja lido (veja read_markdown), porque a origem pode ser
        um arquivo ou uma string vinda do --text.

        A playlist cresce junto com os arquivos, entao da para comecar a ouvir
        antes de a ultima linha ser sintetizada.
        """
        blocks = self.parse_markdown(text, default_lang=default_lang)

        if not blocks:
            raise ValueError("No speakable content found after parsing.")

        groups = [list(group) for _, group in groupby(blocks, key=lambda b: b.line_no)]
        width = max(3, len(str(len(groups))))

        work_tmp = tmp_dir or work_dir(text_label(text))
        work_tmp.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        with M3UWriter(out_dir / "playlist.m3u") as playlist:
            for index, group in enumerate(groups, start=1):
                title = " ".join(block.text for block in group)
                final_path = out_dir / f"{track_name(index, title, width=width)}.wav"

                parts = self._synthesize_group(group, work_tmp, index, speed=speed)
                staged = work_tmp / f"line_{index:0{width}d}.wav"
                if len(parts) == 1:
                    normalize_sample_rate(parts[0], staged, TARGET_SAMPLE_RATE)
                else:
                    concat_audio(parts, staged)

                # move e' rename atomico no mesmo filesystem: um player que
                # esteja acompanhando a pasta nunca abre um arquivo pela metade.
                shutil.move(str(staged), str(final_path))

                playlist.add(final_path, title)
                yield StreamTrack(
                    index=index,
                    path=final_path,
                    text=title,
                    lang=group[0].lang,
                )

        if not keep_temp:
            cleanup_temp(work_tmp)

    def run(
        self,
        text: str,
        *,
        output: Path,
        default_lang: str | None = None,
        play: bool = False,
        keep_temp: bool = False,
        tmp_dir: Path | None = None,
        speed: float = 1.0,
    ) -> Path:
        """Sintetiza o Markdown ja lido (veja read_markdown) num arquivo unico."""
        blocks = self.parse_markdown(text, default_lang=default_lang)

        if not blocks:
            raise ValueError("No speakable content found after parsing.")

        work_tmp = tmp_dir or work_dir(text_label(text))
        wav_files = self.synthesize_blocks(blocks, work_tmp, speed=speed)

        output.parent.mkdir(parents=True, exist_ok=True)
        # Os intermediarios normalizados ficam no work dir, nao no destino final.
        final_path = concat_audio(wav_files, output, scratch_dir=work_tmp)

        # O audio final ja esta gravado: os WAVs por bloco nao servem mais.
        if not keep_temp:
            cleanup_temp(work_tmp)

        if play:
            play_audio(final_path)

        return final_path

    @staticmethod
    def _create_silence(path: Path, duration: float, sample_rate: int = 22050) -> None:
        import wave

        num_samples = int(sample_rate * duration)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(b"\x00\x00" * num_samples)
