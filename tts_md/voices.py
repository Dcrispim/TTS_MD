from __future__ import annotations

from tts_md.lang_index import normalize_lang

# kokoro-onnx (pacote de vozes v1.0): o prefixo antes do "_" identifica o
# idioma/sotaque da voz. Mesma convencao de codigo do espeak_lang() em
# tts_md/tts/kokoro.py, entao os dois lados comparam o mesmo vocabulario.
KOKORO_PREFIX_LANGS = {
    "a": "en-us",
    "b": "en-gb",
    "e": "es",
    "f": "fr-fr",
    "h": "hi",
    "i": "it",
    "j": "ja",
    "p": "pt-br",
    "z": "cmn",
}


class VoiceLangMismatch(ValueError):
    """A voz escolhida nao pertence ao idioma alvo."""


def _target_lang(lang: str) -> str:
    """Normaliza o idioma alvo para o mesmo vocabulario usado nas checagens
    abaixo (pt/en curtos viram pt-BR/en-US, como no lang_index)."""
    return normalize_lang(lang).lower()


def _kokoro_voice_lang(voice: str) -> str | None:
    prefix = voice.split("_", 1)[0][:1].lower()
    return KOKORO_PREFIX_LANGS.get(prefix)


def _piper_model_lang(model: str) -> str | None:
    # ex: pt_BR-faber-medium.onnx -> pt-br
    stem = model.split("-", 1)[0]
    if "_" not in stem:
        return None
    return stem.replace("_", "-").lower()


def _edge_voice_lang(voice: str) -> str | None:
    # ex: pt-BR-AntonioNeural -> pt-br
    parts = voice.split("-")
    if len(parts) < 2:
        return None
    return f"{parts[0]}-{parts[1]}".lower()


def _voice_lang(engine: str, voice: str) -> str | None:
    if engine == "kokoro":
        return _kokoro_voice_lang(voice)
    if engine == "piper":
        return _piper_model_lang(voice)
    if engine == "edge":
        return _edge_voice_lang(voice)
    return None


def check_voice_lang(engine: str, voice: str, lang: str) -> None:
    """Levanta VoiceLangMismatch se `voice` claramente pertence a outro
    idioma que nao `lang`. Quando o formato da voz nao e' reconhecido, a
    checagem e' pulada em vez de bloquear (melhor nao travar do que travar
    errado)."""
    inferred = _voice_lang(engine, voice)
    if inferred is None:
        return

    target = _target_lang(lang)
    if inferred != target:
        raise VoiceLangMismatch(
            f"Voice '{voice}' looks like it belongs to '{inferred}', not "
            f"'{lang}'. Voices must match the language they're assigned to."
        )
