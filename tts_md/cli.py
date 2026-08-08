from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import click

from tts_md.audio.player import QueuedPlayer, find_player
from tts_md.audio.playlist import slugify
from tts_md.engine import TTSEngine, text_label, work_dir
from tts_md.lang_index import (
    DEFAULT_INDEX_PATH,
    add_terms,
    expand_specs,
    format_entry,
    load_raw,
    lookup_term,
    parse_spec,
)
from tts_md.models import AppConfig


AUDIO_SUFFIXES = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}


def _run_stream(
    engine: TTSEngine,
    markdown: str,
    *,
    out_dir: Path,
    lang: str | None,
    play: bool,
    keep_temp: bool,
    tmp_dir: Path,
) -> None:
    if out_dir.suffix.lower() in AUDIO_SUFFIXES:
        raise click.ClickException(
            f"--stream writes a directory of tracks, but --output looks like a "
            f"file: {out_dir}. Pass a directory instead."
        )

    player = None
    if play:
        if find_player() is None:
            raise click.ClickException(
                "No audio player found. Install mpv, aplay, or ffplay for --play."
            )
        player = QueuedPlayer()

    count = 0
    try:
        for track in engine.run_stream(
            markdown,
            out_dir=out_dir,
            default_lang=lang,
            keep_temp=keep_temp,
            tmp_dir=tmp_dir,
        ):
            count += 1
            click.echo(f"{track.path.name}  [{track.lang}] {track.text}")
            if player is not None:
                player.add(track.path)
    finally:
        if player is not None:
            player.wait()

    click.echo(f"Generated {count} tracks in {out_dir}")
    click.echo(f"Playlist: {out_dir / 'playlist.m3u'}")


def _default_config_path() -> Path:
    local = Path("config.yaml")
    if local.exists():
        return local
    return Path(__file__).resolve().parent.parent / "config.example.yaml"


def _manage_index(
    specs: tuple[str, ...],
    wanted: tuple[str, ...],
    *,
    replace: bool,
    list_terms: bool,
) -> None:
    """Modo de manutencao do lang_index.yaml: adiciona, consulta e/ou lista termos."""
    if specs:
        # Todo o lote e' validado antes de gravar: uma spec torta nao deixa
        # metade dos termos aplicados no arquivo.
        try:
            entries = [parse_spec(spec) for spec in expand_specs(specs)]
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        for term, action, entry in add_terms(entries, replace=replace):
            value = format_entry(entry)
            if action == "kept":
                click.echo(
                    f"kept     {term}: {value}  (already in the index; use --replace)"
                )
            else:
                click.echo(f"{action:<8} {term}: {value}")

    missing = False
    for term in expand_specs(wanted):
        found = lookup_term(term)
        if found is None:
            # Silencio no stdout: quem consulta em script le so o valor.
            click.echo(f"not found: {term}", err=True)
            missing = True
        else:
            click.echo(format_entry(found[1]))

    if list_terms:
        data = load_raw()
        click.echo(f"{DEFAULT_INDEX_PATH} ({len(data)} terms)")
        for term in sorted(data, key=lambda t: str(t).lower()):
            click.echo(f"  {term}: {format_entry(data[term])}")

    if missing:
        raise SystemExit(1)


def _output_stem(input_file: Path | None, inline_text: str | None) -> str:
    """Nome base do audio: o do arquivo, ou um slug do proprio texto com --text."""
    if input_file is not None:
        return input_file.stem
    return slugify(inline_text or "") or "text"


@click.command()
@click.argument(
    "input_file",
    required=False,
    default=None,
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--text",
    "inline_text",
    default=None,
    help=(
        'Read this Markdown string instead of a file: --text="Chame approve()". '
        "Mutually exclusive with INPUT_FILE."
    ),
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output audio file path.",
)
@click.option(
    "--lang",
    default=None,
    help="Default language for unparsed text (e.g. pt-BR).",
)
@click.option("--play", is_flag=True, help="Play the generated audio.")
@click.option(
    "--stream",
    is_flag=True,
    help=(
        "Save one audio file per Markdown line into a directory plus a "
        "playlist.m3u, instead of concatenating everything. Each file lands "
        "as soon as it is ready, so playback can start before the end."
    ),
)
@click.option(
    "--temp",
    is_flag=True,
    help=(
        "Treat the audio as throwaway: write it to a scratch directory under "
        "the system temp dir instead of output/, and let the OS reclaim it. "
        "Requires --play."
    ),
)
@click.option(
    "--debug-parser",
    is_flag=True,
    help="Print parsed SpeechBlock IR as JSON and exit.",
)
@click.option(
    "--keep-temp",
    is_flag=True,
    help="Keep the per-run work directory with the intermediate WAV files.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to config.yaml.",
)
@click.option(
    "--skip-validation",
    is_flag=True,
    help="Skip model path validation (useful with --debug-parser).",
)
@click.option(
    "--add-term",
    "add_terms",
    multiple=True,
    metavar="TERM=LANG[:TEXT],...",
    help=(
        "Add terms to lang_index.yaml and exit: --add-term commit=en, or "
        "--add-term dev=en:development to also change what is spoken. Takes a "
        'comma-separated batch (--add-term "k8s=en, deploy=en") and is also '
        "repeatable. Existing terms are kept unless --replace is given."
    ),
)
@click.option(
    "--replace",
    is_flag=True,
    help="With --add-term, overwrite terms that are already in the index.",
)
@click.option(
    "--exist-term",
    "exist_terms",
    multiple=True,
    metavar="TERM,...",
    help=(
        "Print a term's value and exit: 'en', or 'en:development' when the term "
        'also changes what is spoken. Takes a comma-separated batch ("commit, '
        'deploy") and is also repeatable. Exits 1 if any term is missing.'
    ),
)
@click.option(
    "--list-terms",
    is_flag=True,
    help="Print the language index and exit.",
)
def main(
    input_file: Path | None,
    inline_text: str | None,
    output: Path | None,
    lang: str | None,
    play: bool,
    stream: bool,
    temp: bool,
    debug_parser: bool,
    keep_temp: bool,
    config_path: Path | None,
    skip_validation: bool,
    add_terms: tuple[str, ...],
    replace: bool,
    exist_terms: tuple[str, ...],
    list_terms: bool,
) -> None:
    """Convert Markdown to speech using modular parsers and offline TTS."""
    if replace and not add_terms:
        raise click.ClickException("--replace only applies to --add-term.")
    if add_terms or exist_terms or list_terms:
        _manage_index(
            add_terms, exist_terms, replace=replace, list_terms=list_terms
        )
        return

    if input_file is not None and inline_text is not None:
        raise click.ClickException(
            "Pass either a Markdown file or --text, not both."
        )
    if input_file is None and inline_text is None:
        raise click.ClickException(
            'Nothing to read: pass a Markdown file or --text "...".'
        )
    if temp and not play:
        raise click.ClickException(
            "--temp only makes sense with --play: without playback the audio "
            "would be left in a scratch directory you never listen to."
        )
    if temp and output is not None:
        raise click.ClickException(
            "--temp writes to a scratch directory under the system temp dir, "
            "so --output would be ignored. Drop one of the two."
        )

    cfg_path = config_path or _default_config_path()
    if not cfg_path.exists():
        raise click.ClickException(
            f"Config not found: {cfg_path}. Copy config.example.yaml to config.yaml."
        )

    config = AppConfig.load(cfg_path)

    if debug_parser or skip_validation:
        pass
    else:
        if not shutil.which("ffmpeg"):
            raise click.ClickException("ffmpeg is required but was not found in PATH.")
        config.validate()

    engine = TTSEngine(config)
    markdown = (
        inline_text if inline_text is not None else engine.read_markdown(input_file)
    )
    blocks = engine.parse_markdown(markdown, default_lang=lang)

    if debug_parser:
        payload = [block.to_dict() for block in blocks]
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not blocks:
        raise click.ClickException("No speakable content found after parsing.")

    # Com --temp o audio e descartavel: sai num diretorio proprio sob o temp do
    # sistema, que o SO limpa sozinho, em vez de acumular em output/.
    scratch = Path(tempfile.mkdtemp(prefix="tts-md-")) if temp else None
    stem = _output_stem(input_file, inline_text)
    # Diretorio proprio desta execucao, nomeado pela origem do Markdown.
    work_tmp = work_dir(
        input_file.name if input_file is not None else text_label(inline_text or "")
    )

    if stream:
        _run_stream(
            engine,
            markdown,
            out_dir=scratch or output or Path("output") / stem,
            lang=lang,
            play=play,
            keep_temp=keep_temp,
            tmp_dir=work_tmp,
        )
        if keep_temp:
            click.echo(f"Work dir: {work_tmp}")
        return

    out_path = output or (scratch or Path("output")) / f"{stem}.wav"
    final = engine.run(
        markdown,
        output=out_path,
        default_lang=lang,
        play=play,
        keep_temp=keep_temp,
        tmp_dir=work_tmp,
    )
    click.echo(f"Generated: {final}")
    if keep_temp:
        click.echo(f"Work dir: {work_tmp}")


if __name__ == "__main__":
    main()
