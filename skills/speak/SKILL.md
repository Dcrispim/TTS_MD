---
name: speak
description: Convert Markdown (or plain text) to speech using the tts-md project, an offline TTS pipeline (Kokoro/Piper) installed at ~/projs/TTS_MD. Use whenever the user asks to "read this out loud", "turn this into audio/podcast", "narrate this file", or otherwise wants Markdown/text synthesized to speech — from any project, not just TTS_MD itself.
user-invocable: true
allowed-tools:
  - Bash
---

# /speak — Markdown/text to speech (tts-md)

Wraps the `tts-md` CLI so it can be used from **any** project/chat, not just
from inside `~/projs/TTS_MD`. The tool, its Python venv, its config, and its
models all live in that one project directory — this skill just points at
them.

Arguments passed: `$ARGUMENTS`

## Fixed locations

- Project dir: `~/projs/TTS_MD`
- Binary: `~/projs/TTS_MD/.venv/bin/tts-md` (installed via `pip install -e .`
  in that venv — do not expect a global `tts-md` on `PATH`)
- Config: `~/projs/TTS_MD/config.yaml` (Kokoro/Piper model paths + voices)
- Language index: `~/projs/TTS_MD/lang_index.yaml`
- Default output dir: `output/` relative to the current working directory

Always invoke the binary by its full path so it works regardless of the
caller's cwd:

```bash
~/projs/TTS_MD/.venv/bin/tts-md <args>
```

If that binary is missing, tell the user the venv isn't set up and point them
at `~/projs/TTS_MD/README.md` (`python3 -m venv .venv && pip install -r
requirements.txt && pip install -e .`) — don't try to recreate it yourself.

## Deciding what to synthesize

- A path to a Markdown/text file the user already has → pass it as the input
  file, e.g. `~/projs/TTS_MD/.venv/bin/tts-md /path/to/file.md --play`.
- Ad-hoc text, a message, or content you (Claude) generated in this
  conversation → use `--text`, quoting carefully since it's shell-escaped:
  `~/projs/TTS_MD/.venv/bin/tts-md --text="conteúdo aqui" --play`.
- File and `--text` are mutually exclusive.
- If the user just wants to *hear* it and doesn't care about keeping the
  file, add `--temp` (requires `--play`) so nothing is left behind in
  `output/`.
- Relative output paths (including the default `output/`) are relative to
  the current working directory, not to `~/projs/TTS_MD` — prefer an
  absolute `--output` so the audio lands where intended.

## Common invocations

```bash
# Read a file out loud, default language (pt-BR), keep nothing
~/projs/TTS_MD/.venv/bin/tts-md notas.md --play --temp

# Synthesize arbitrary text and play it, throwaway
~/projs/TTS_MD/.venv/bin/tts-md --text="Chame Payment.approve() antes" --play --temp

# Save an mp3 next to the source file
~/projs/TTS_MD/.venv/bin/tts-md relatorio.md --output relatorio.mp3

# Force a language
~/projs/TTS_MD/.venv/bin/tts-md notas.md --lang en-US --play

# Faster/slower playback (multiplier on every voice, must be > 0)
~/projs/TTS_MD/.venv/bin/tts-md notas.md --speed 1.5 --play

# Long content: one audio file per line + playlist.m3u, so playback can
# start before synthesis finishes
~/projs/TTS_MD/.venv/bin/tts-md livro.md --stream --play

# Inspect how the parsers split a file into speech blocks, without
# synthesizing anything
~/projs/TTS_MD/.venv/bin/tts-md notas.md --debug-parser
```

`--config` defaults to `~/projs/TTS_MD/config.yaml` when run from that
directory; if invoking from elsewhere, pass it explicitly:
`--config ~/projs/TTS_MD/config.yaml`.

## Video mode (`--video`)

`--video` turns the same Markdown into an `.mp4`: the audio is unchanged and
a screen track (images, a pointer, highlighted code, optional captions) is
laid over it. Use it automatically, with judgment — the user doesn't need to
ask for video.

**Use `--video` when the screen carries information the speech can't:**
- code longer than ~3 lines that the narration *explains* (point at it with
  `!{L…}`);
- an existing diagram or screenshot that the narration refers to ("in this
  flow…");
- a side-by-side comparison (before/after, two versions).

**Don't use it when:**
- the code is incidental (a one-line command, a function name already said
  out loud);
- the summary is short (fewer than ~4 spoken lines);
- the image would only be decorative.

**When you generate a video, say so in your reply**, so the player window
isn't a surprise.

Screen tags start with `!` and are never spoken (a `--video`-capable install
strips them even without `--video`):

| Tag | Effect |
|---|---|
| `![[img.png]]` | show the image (until the next image, a visible code block, a heading or `!{clear}`) |
| `![[a.png]] ![[b.png]]` | group: side by side (grid from 4 on) |
| `!{x,y}` / `!{x%,y%}` | pointer, in px of the original image or in % |
| `!{b.png@x,y}` | pointer on a specific image of the group |
| `!{L12}` / `!{L12-15}` | highlight lines of the code block on screen |
| `!{clear}` | clear the screen |

Any image, pointer or highlight takes an explicit duration after a pipe
(`s` or `ms`), written exactly like this:

```text
![[img.png|5s]]   !{120,340|3s}   !{L12-15|1.5s}
```

A fenced code block becomes a screen automatically when its first following
line is spoken (add `!hide` to the info string, e.g. ` ```python !hide`, to
keep it off screen). Put each tag on the line where the narration mentions
it — the timing is anchored there.

Rules that matter when invoking:
- Relative image paths resolve against a single base: `--images DIR` when
  given, otherwise the `.md` file's directory (the cwd with `--text`).
  Absolute paths also work. A missing image aborts
  **before** synthesis and lists every missing ref — fix the paths, don't
  drop `--video` silently.
- Only reference images that already exist; never invent paths. For content
  you generated, prefer writing a temp `.md` next to the images (or pass
  `--images`) over a long `--text`.
- Output must be `.mp4` (default extension with `--video`); `--stream
  --video` writes one `.mp4` per line + `playlist.m3u`.
- `--play` with video needs `mpv` or `ffplay` (`aplay` is refused).
- Needs the `[video]` extra (Pillow + Pygments). If the install is older and
  `--video` is an unknown option (`No such option '--video'`), or the deps
  are missing, fall back to plain audio and tell the user why. An older
  install also **speaks the tags literally**, so remove every `![[…]]` and
  `!{…}` tag (and `!hide` from fence info strings) from the text before
  synthesizing the fallback.
- With `--host`, the client also needs the `[video]` extra; it uploads the
  images only if the server runs `--serve --video` (the server operator
  decides — the client's `--video` doesn't change that). Otherwise the tags
  are removed and only the audio is generated, with a warning — mention that
  to the user.

```bash
# Code-only explanation (no image files to sit next to): a temp .md is fine
~/projs/TTS_MD/.venv/bin/tts-md --config ~/projs/TTS_MD/config.yaml /tmp/explica.md --video --play --temp

# Narrate a doc that references a diagram, saving the video next to it
~/projs/TTS_MD/.venv/bin/tts-md --config ~/projs/TTS_MD/config.yaml docs/fluxo.md --video --output docs/fluxo.mp4
```

Full syntax, timing rules and the `video:` config block: the "Modo `--video`"
section of `~/projs/TTS_MD/README.md`.

## Language index management (pronunciation overrides)

`lang_index.yaml` controls which language (and optionally which spoken text)
a specific term uses, e.g. so `commit` or `dev` are read in English inside
Portuguese text. Manage it with the same binary, from any cwd:

```bash
~/projs/TTS_MD/.venv/bin/tts-md --config ~/projs/TTS_MD/config.yaml --add-term commit=en
~/projs/TTS_MD/.venv/bin/tts-md --config ~/projs/TTS_MD/config.yaml --add-term dev=en:development
~/projs/TTS_MD/.venv/bin/tts-md --config ~/projs/TTS_MD/config.yaml --exist-term commit
~/projs/TTS_MD/.venv/bin/tts-md --config ~/projs/TTS_MD/config.yaml --list-terms
```

These flags exit without synthesizing anything. `--add-term` accepts a
comma-separated batch and validates the whole batch before writing (no
partial writes). `--exist-term` exits 1 and prints nothing to stdout when a
term is missing (writes `not found: <term>` to stderr) — usable in scripts.

## Notes

- Full flag reference and pipeline details live in
  `~/projs/TTS_MD/README.md` — read it if the user needs something this
  skill doesn't cover (e.g. code-block/UUID/function-name handling, the
  `{lang:en-US}...{/lang}` inline tag syntax, or the temp work-directory
  layout).
- Playback needs `mpv`, `aplay`, or `ffplay` on the system; synthesis itself
  needs `ffmpeg` and the Kokoro/Piper model files configured in
  `config.yaml`. If a command fails on a missing dependency, tell the user
  rather than trying to install system packages yourself.
- This skill only shells out to the existing `tts-md` install — it never
  modifies `~/projs/TTS_MD` itself (code, config, or models). Point config
  changes back at the user if they want the defaults (voice, engine, model
  paths) changed.
