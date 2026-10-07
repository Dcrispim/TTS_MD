from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

import click

from tts_md import client, persona
from tts_md.audio.player import (
    QueuedPlayer,
    find_player,
    get_last_played,
    missing_player_message,
    play_audio,
)
from tts_md.audio.playlist import M3UWriter, slugify
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
from tts_md.models import AppConfig, VideoConfig
from tts_md.server import DEFAULT_PORT, ServeOptions, run_server
from tts_md.visual import VideoDepsError, extract_cues, require_video_deps
from tts_md.voices import VoiceLangMismatch, check_voice_lang

if TYPE_CHECKING:
    from tts_md.visual.cues import CueExtraction
    from tts_md.visual.video import StreamVideo


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
    speed: float,
    voice_overrides: dict[str, str] | None = None,
    video: StreamVideo | None = None,
) -> None:
    file_suffixes = AUDIO_SUFFIXES | {".mp4"} if video is not None else AUDIO_SUFFIXES
    if out_dir.suffix.lower() in file_suffixes:
        raise click.ClickException(
            f"--stream writes a directory of tracks, but --output looks like a "
            f"file: {out_dir}. Pass a directory instead."
        )

    player = None
    if play:
        if find_player(video=video is not None) is None:
            raise click.ClickException(missing_player_message(video=video is not None))
        player = QueuedPlayer(video=video is not None)

    count = 0
    with ExitStack() as stack:
        playlist = None
        if video is not None:
            playlist = stack.enter_context(M3UWriter(out_dir / "playlist.m3u"))
        try:
            for track in engine.run_stream(
                markdown,
                out_dir=tmp_dir / "tracks" if video is not None else out_dir,
                default_lang=lang,
                keep_temp=keep_temp or video is not None,
                tmp_dir=tmp_dir,
                speed=speed,
            ):
                count += 1
                path = track.path
                if video is not None:
                    try:
                        path = video.add(path, track.line_no, out_dir / f"{path.stem}.mp4")
                    except RuntimeError as exc:
                        raise click.ClickException(str(exc)) from exc
                    playlist.add(path, track.text)
                click.echo(f"{path.name}  [{track.lang}] {track.text}")
                if player is not None:
                    player.add(path)
        finally:
            if player is not None:
                player.wait()
            if video is not None and not keep_temp:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    if video is not None:
        _echo_warnings(video.warnings())
    click.echo(f"Generated {count} tracks in {out_dir}")
    click.echo(f"Playlist: {out_dir / 'playlist.m3u'}")


def _echo_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        click.echo(f"warning: {warning}", err=True)


def _require_video_deps() -> None:
    try:
        require_video_deps()
    except VideoDepsError as exc:
        raise click.ClickException(str(exc)) from exc


def _prepare_video(extraction: CueExtraction, base: Path, *, play: bool) -> dict:
    _require_video_deps()
    from tts_md.visual.video import FileSystemResolver, format_warning, image_refs

    if play and find_player(video=True) is None:
        raise click.ClickException(missing_player_message(video=True))
    resolution = FileSystemResolver(base).resolve(image_refs(extraction))
    if resolution.missing:
        listed = "\n".join(f"  {ref}: {why}" for ref, why in resolution.missing.items())
        raise click.ClickException(
            f"{len(resolution.missing)} image(s) referenced in the Markdown could not "
            f"be loaded (relative paths resolve against {base}); nothing was "
            f"synthesized:\n{listed}"
        )
    _echo_warnings([format_warning(w) for w in extraction.warnings])
    return resolution.images


def _video_uploads(
    extraction: CueExtraction, base: Path, config_path: Path | None
) -> dict[str, dict]:
    images = _prepare_video(extraction, base, play=False)
    from tts_md.visual.video import encode_upload

    cfg_path = config_path or AppConfig.default_path()
    video_cfg = AppConfig.load(cfg_path).video if cfg_path.exists() else VideoConfig()
    try:
        return {ref: encode_upload(src, video_cfg.upload_max_side) for ref, src in images.items()}
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"Could not prepare the images for upload: {exc}") from exc


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


def _checked_voice(config: AppConfig, target_lang: str, voice: str) -> None:
    """Confere que `voice` bate com o idioma de `target_lang` antes de deixar
    ela virar override (avulso ou dentro de uma persona)."""
    try:
        voice_cfg = config.get_voice(target_lang)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        check_voice_lang(voice_cfg.engine, voice, target_lang)
    except VoiceLangMismatch as exc:
        raise click.ClickException(str(exc)) from exc


def _handle_set_persona(
    set_persona_id: str,
    *,
    voice_override: str | None,
    lang: str | None,
    host: str | None,
    port: int,
    config_path: Path | None,
    blocked: bool,
) -> None:
    """--set-persona so registra/ativa uma persona; nunca sintetiza nada."""
    if blocked:
        raise click.ClickException(
            "--set-persona only registers a persona; it doesn't read or speak "
            "anything, so it can't be combined with a Markdown input or "
            "execution flags."
        )
    if not voice_override:
        raise click.ClickException("--set-persona requires --voice=<id>.")

    cfg_path = config_path or AppConfig.default_path()
    if not cfg_path.exists():
        raise click.ClickException(
            f"Config not found: {cfg_path}. Copy config.example.yaml to config.yaml."
        )
    config = AppConfig.load(cfg_path)
    target_lang = lang or config.default_lang
    _checked_voice(config, target_lang, voice_override)

    persona.set_persona(
        set_persona_id, target_lang, voice_override, host=host, port=port
    )
    target = persona.target_key(host, port)
    click.echo(
        f"Persona '{set_persona_id}' set for {target_lang} -> {voice_override} "
        f"(active for {target} in {persona.ACTIVE_FILENAME})."
    )


def _validate_execution_flags(*, temp: bool, play: bool, output: Path | None) -> None:
    """Regras de --temp, compartilhadas pelo modo local e pela inicializacao
    do --serve (as duas execucoes decidem sozinhas como lidar com os arquivos)."""
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


def _run_video(
    engine: TTSEngine,
    markdown: str,
    *,
    output: Path,
    audio: Path,
    lang: str | None,
    play: bool,
    keep_temp: bool,
    tmp_dir: Path,
    speed: float,
    voice_overrides: dict[str, str],
    config: AppConfig,
    images: dict,
) -> list[str]:
    from tts_md.visual.video import render_document

    spans: dict[int, tuple[float, float]] = {}
    try:
        engine.run(
            markdown,
            output=audio,
            default_lang=lang,
            keep_temp=True,
            tmp_dir=tmp_dir,
            speed=speed,
            voice_overrides=voice_overrides,
            spans=spans,
        )
        warnings = render_document(
            engine.last_cues, spans, audio, output, config.video, images, work_dir=tmp_dir
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if not keep_temp:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    _echo_warnings(warnings)
    click.echo(f"Generated: {output}")
    if keep_temp:
        click.echo(f"Work dir: {tmp_dir}")
    if play:
        try:
            play_audio(output, video=True)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
    return warnings


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
    "--last",
    "replay_last",
    is_flag=True,
    help=(
        "Replay the most recently played audio instead of synthesizing "
        "anything new. Takes no Markdown input."
    ),
)
@click.option(
    "--speed",
    type=float,
    default=1.0,
    help="Global speed multiplier applied to every voice (e.g. 1.5 for 50% faster).",
)
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
@click.option(
    "--serve",
    is_flag=True,
    help=(
        "Listen on the network as a remote speaker instead of reading a "
        "Markdown input: every request received is handled with the "
        "--play/--stream/--temp/--output/--keep-temp flags given here. "
        "Blocks until Ctrl+C."
    ),
)
@click.option(
    "--host",
    default=None,
    envvar="TTS_MD_HOST",
    help=(
        "With --serve, the interface to bind (default 0.0.0.0). Without "
        "--serve, the address of a tts-md --serve to send this run to "
        "instead of speaking locally. Also settable via TTS_MD_HOST, so "
        "every tts-md run on a machine can default to a server without "
        "repeating the flag."
    ),
)
@click.option(
    "--port",
    type=int,
    default=DEFAULT_PORT,
    show_default=True,
    envvar="TTS_MD_PORT",
    help="Port to bind (--serve) or to reach (--host). Also settable via TTS_MD_PORT.",
)
@click.option(
    "--check",
    is_flag=True,
    envvar="TTS_MD_CHECK",
    help=(
        "With --host, probe the server first and fall back to local "
        "synthesis if it does not respond. Also settable via TTS_MD_CHECK."
    ),
)
@click.option(
    "--voice",
    "voice_override",
    default=None,
    metavar="VOICE",
    help=(
        "Override the voice used for --lang (or the default language) just "
        "for this run. Must match that language's engine (e.g. a Kokoro "
        "'af_'/'am_' voice for en-US, 'pf_'/'pm_' for pt-BR). Required by "
        "--set-persona; on its own it doesn't touch any persona file."
    ),
)
@click.option(
    "--set-persona",
    "set_persona_id",
    default=None,
    metavar="ID",
    help=(
        "Register ID -> --voice for --lang (or the default language) in "
        "personas.json, and make ID the active persona for this directory "
        "(or for --host/--port, if given). Doesn't read or speak anything; "
        "run it again with another --lang/--voice to add languages to the "
        "same persona."
    ),
)
@click.option(
    "--persona",
    "persona_id",
    default=None,
    metavar="ID",
    help=(
        "Use this persona for the run instead of whatever is active in "
        f"{persona.ACTIVE_FILENAME}. Resolved against this machine's "
        "personas.json when speaking locally; with --host, the id is "
        "forwarded as-is and resolved on the server's own personas.json."
    ),
)
@click.option(
    "--video",
    is_flag=True,
    help=(
        "Render an .mp4 instead of audio: the ![[image]], !{x,y}, !{Lx-y} and "
        "!{clear} tags and the fenced code blocks of the Markdown become what "
        "is shown on screen while it is spoken. With --stream, one .mp4 per "
        "line. Requires the video extra: pip install 'tts-md[video]'."
    ),
)
@click.option(
    "--images",
    "images_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "With --video, resolve relative image references against this "
        "directory instead of the Markdown file's directory (or the current "
        "directory with --text)."
    ),
)
def main(
    input_file: Path | None,
    inline_text: str | None,
    output: Path | None,
    lang: str | None,
    play: bool,
    replay_last: bool,
    speed: float,
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
    serve: bool,
    host: str | None,
    port: int,
    check: bool,
    voice_override: str | None,
    set_persona_id: str | None,
    persona_id: str | None,
    video: bool,
    images_dir: Path | None,
) -> None:
    """Convert Markdown to speech using modular parsers and offline TTS."""
    if replay_last:
        if input_file is not None or inline_text is not None:
            raise click.ClickException(
                "--last replays the last played audio; it doesn't take a Markdown input."
            )
        if serve:
            raise click.ClickException("--last doesn't apply to --serve.")
        if find_player() is None:
            raise click.ClickException(
                "No audio player found. Install mpv, aplay, or ffplay for --last."
            )
        last_path = get_last_played()
        if last_path is None:
            raise click.ClickException("No previously played audio found.")
        if not last_path.exists():
            # Comum quando o ultimo audio veio de --temp: o SO ja pode ter
            # limpo o diretorio de scratch (reboot, systemd-tmpfiles, etc.).
            raise click.ClickException(
                f"The last played audio no longer exists: {last_path}"
            )
        click.echo(f"Replaying: {last_path}")
        try:
            play_audio(last_path, video=last_path.suffix.lower() == ".mp4")
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        return

    if replace and not add_terms:
        raise click.ClickException("--replace only applies to --add-term.")
    if add_terms or exist_terms or list_terms:
        _manage_index(
            add_terms, exist_terms, replace=replace, list_terms=list_terms
        )
        return

    if set_persona_id:
        if persona_id:
            raise click.ClickException(
                "--set-persona and --persona are mutually exclusive."
            )
        blocked = (
            input_file is not None
            or inline_text is not None
            or play
            or stream
            or temp
            or debug_parser
            or keep_temp
            or serve
        )
        _handle_set_persona(
            set_persona_id,
            voice_override=voice_override,
            lang=lang,
            host=host,
            port=port,
            config_path=config_path,
            blocked=blocked,
        )
        return

    if images_dir is not None and serve:
        raise click.ClickException(
            "--images doesn't apply to --serve: the images come uploaded with each request."
        )
    if images_dir is not None and not video and not host:
        raise click.ClickException("--images only applies to --video or --host.")

    if serve:
        if input_file is not None or inline_text is not None:
            raise click.ClickException(
                "--serve doesn't take a Markdown input; it only listens for requests."
            )
        _validate_execution_flags(temp=temp, play=play, output=output)

        cfg_path = config_path or AppConfig.default_path()
        if not cfg_path.exists():
            raise click.ClickException(
                f"Config not found: {cfg_path}. Copy config.example.yaml to config.yaml."
            )
        config = AppConfig.load(cfg_path)
        if not skip_validation:
            if not shutil.which("ffmpeg"):
                raise click.ClickException("ffmpeg is required but was not found in PATH.")
            config.validate()
        if video:
            _require_video_deps()
            if play and find_player(video=True) is None:
                raise click.ClickException(missing_player_message(video=True))

        opts = ServeOptions(
            play=play,
            stream=stream,
            temp=temp,
            output=output,
            keep_temp=keep_temp,
            video=video,
        )
        run_server(config, opts, host or "0.0.0.0", port)
        return

    if input_file is not None and inline_text is not None:
        raise click.ClickException(
            "Pass either a Markdown file or --text, not both."
        )
    if input_file is None and inline_text is None:
        raise click.ClickException(
            'Nothing to read: pass a Markdown file or --text "...".'
        )
    if check and not host:
        raise click.ClickException("--check only applies to --host.")
    if speed <= 0:
        raise click.ClickException("--speed must be greater than 0.")

    markdown = (
        inline_text if inline_text is not None else input_file.read_text(encoding="utf-8")
    )

    # Explicito (--persona) vence; senao olha o .tts-md.persona do diretorio
    # atual para o host:port de destino. Se cair no fallback local (--check),
    # a resolucao e' refeita mais abaixo para o alvo "local".
    try:
        resolved_persona_for_host = persona.resolve_persona_id(
            persona_id, host=host, port=port
        )
    except persona.PersonaError as exc:
        raise click.ClickException(str(exc)) from exc

    # --host manda o texto pro servidor falar; --play/--stream/--temp/--output
    # daqui nao entram nessa jogada (quem decide isso e' o --serve). So voltam
    # a valer no caminho local abaixo, usado quando --host nao foi passado ou
    # quando --check descobre que o servidor nao respondeu.
    #
    # O probe de /health roda sempre (e' rapido: ~1.5s), nao so com --check -
    # sem isso um host inalcancavel mas roteavel deixaria send_to_server preso
    # no timeout de sintese (300s) so pra descobrir que ninguem responde.
    if host and not debug_parser:
        health = client.server_health(host, port)
        if health is None:
            if not check:
                raise click.ClickException(
                    f"Server at {host}:{port} did not respond. Pass --check to "
                    "fall back to local TTS instead of failing."
                )
            click.echo(
                f"warning: server at {host}:{port} did not respond, "
                "falling back to local TTS",
                err=True,
            )
        else:
            remote_text, uploads = markdown, None
            extraction = extract_cues(markdown)
            has_tags = extraction.markdown != markdown
            server_video = health.get("video") is True
            if has_tags and server_video:
                base = input_file.resolve().parent if input_file is not None else Path.cwd()
                uploads = _video_uploads(extraction, images_dir or base, config_path)
            elif not server_video and (has_tags or video):
                if has_tags:
                    remote_text = extraction.markdown
                click.echo(
                    f"warning: server at {host}:{port} has no video support; only "
                    "the audio will be generated"
                    + (" (the ! tags were removed locally)" if has_tags else ""),
                    err=True,
                )
            try:
                result = client.send_to_server(
                    host,
                    port,
                    remote_text,
                    lang=lang,
                    speed=speed,
                    persona=resolved_persona_for_host,
                    images=uploads,
                )
            except client.ServerError as exc:
                if not check:
                    raise click.ClickException(str(exc)) from exc
                click.echo(f"warning: {exc}; falling back to local TTS", err=True)
            else:
                _echo_warnings(result.warnings)
                click.echo(f"Handled by {host}:{port}: {result.output}")
                return

    _validate_execution_flags(temp=temp, play=play, output=output)

    cfg_path = config_path or AppConfig.default_path()
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

    # Resolucao local: --persona/.tts-md.persona sao lidos de novo para o alvo
    # "local" (importa quando --host foi dado mas caiu no fallback do --check).
    try:
        resolved_persona_local = persona.resolve_persona_id(
            persona_id, host=None, port=port
        )
    except persona.PersonaError as exc:
        raise click.ClickException(str(exc)) from exc

    voice_overrides: dict[str, str] = {}
    if resolved_persona_local:
        try:
            voice_overrides.update(persona.get_voice_bundle(resolved_persona_local))
        except persona.PersonaError as exc:
            raise click.ClickException(str(exc)) from exc
    if voice_override:
        target_lang = lang or config.default_lang
        _checked_voice(config, target_lang, voice_override)
        voice_overrides[target_lang] = voice_override

    engine = TTSEngine(config)
    blocks = engine.parse_markdown(
        markdown, default_lang=lang, voice_overrides=voice_overrides
    )

    if debug_parser:
        payload = [block.to_dict() for block in blocks]
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not blocks:
        raise click.ClickException("No speakable content found after parsing.")

    video_images = None
    if video:
        if not stream and output is not None and output.suffix.lower() != ".mp4":
            raise click.ClickException(
                f"--video writes an .mp4, but --output is {output}. Use an .mp4 path."
            )
        base = input_file.resolve().parent if input_file is not None else Path.cwd()
        video_images = _prepare_video(engine.last_cues, images_dir or base, play=play)

    # Com --temp o audio e descartavel: sai num diretorio proprio sob o temp do
    # sistema, que o SO limpa sozinho, em vez de acumular em output/.
    scratch = Path(tempfile.mkdtemp(prefix="tts-md-")) if temp else None
    stem = _output_stem(input_file, inline_text)
    # Diretorio proprio desta execucao, nomeado pela origem do Markdown.
    work_tmp = work_dir(
        input_file.name if input_file is not None else text_label(inline_text or "")
    )

    if stream:
        stream_video = None
        if video:
            from tts_md.visual.video import StreamVideo

            stream_video = StreamVideo(
                engine.last_cues, config.video, video_images, work_dir=work_tmp
            )
        _run_stream(
            engine,
            markdown,
            out_dir=scratch or output or Path("output") / stem,
            lang=lang,
            play=play,
            keep_temp=keep_temp,
            tmp_dir=work_tmp,
            speed=speed,
            voice_overrides=voice_overrides,
            video=stream_video,
        )
        if keep_temp:
            click.echo(f"Work dir: {work_tmp}")
        return

    if video:
        _run_video(
            engine,
            markdown,
            output=output or (scratch or Path("output")) / f"{stem}.mp4",
            audio=work_tmp / f"{stem}.wav",
            lang=lang,
            play=play,
            keep_temp=keep_temp,
            tmp_dir=work_tmp,
            speed=speed,
            voice_overrides=voice_overrides,
            config=config,
            images=video_images,
        )
        return

    out_path = output or (scratch or Path("output")) / f"{stem}.wav"
    try:
        final = engine.run(
            markdown,
            output=out_path,
            default_lang=lang,
            play=play,
            keep_temp=keep_temp,
            tmp_dir=work_tmp,
            speed=speed,
            voice_overrides=voice_overrides,
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Generated: {final}")
    if keep_temp:
        click.echo(f"Work dir: {work_tmp}")


if __name__ == "__main__":
    main()
