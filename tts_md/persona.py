from __future__ import annotations

import json
from pathlib import Path

from tts_md.models import SpeechBlock

# Catalogo id -> {idioma: voz}, um por instalacao (mesma logica de
# DEFAULT_INDEX_PATH em lang_index.py: ancorado na raiz do pacote, nao no
# diretorio de onde o comando roda).
CATALOG_PATH = Path(__file__).resolve().parent.parent / "personas.json"

# Persona ativa por diretorio de projeto (cwd), uma entrada por alvo: "local"
# e depois "<host>:<port>" para cada servidor remoto ja usado dali. "local"
# e' sempre a primeira chave.
ACTIVE_FILENAME = ".tts-md.persona"


class PersonaError(Exception):
    """Persona ou arquivo de persona invalido/ausente."""


def _active_path(base: Path | None = None) -> Path:
    return (base or Path.cwd()) / ACTIVE_FILENAME


def target_key(host: str | None, port: int | None) -> str:
    """Chave usada tanto no arquivo ativo quanto para decidir se a resolucao
    de voz acontece localmente (local) ou e' delegada ao servidor remoto."""
    if not host:
        return "local"
    return f"{host}:{port}"


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        try:
            data = json.load(f) or {}
        except json.JSONDecodeError as exc:
            raise PersonaError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PersonaError(f"{path} must contain a JSON object of id -> {{lang: voice}}.")
    return data


def save_catalog(data: dict[str, dict[str, str]], path: Path = CATALOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_active(base: Path | None = None) -> dict[str, str]:
    path = _active_path(base)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        try:
            data = json.load(f) or {}
        except json.JSONDecodeError as exc:
            raise PersonaError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PersonaError(f"{path} must contain a JSON object of target -> persona id.")
    return data


def _ordered_active(data: dict[str, str]) -> dict[str, str]:
    ordered: dict[str, str] = {}
    if "local" in data:
        ordered["local"] = data["local"]
    for key in sorted(k for k in data if k != "local"):
        ordered[key] = data[key]
    return ordered


def save_active(data: dict[str, str], base: Path | None = None) -> None:
    path = _active_path(base)
    path.write_text(
        json.dumps(_ordered_active(data), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def set_persona(
    persona_id: str,
    lang: str,
    voice: str,
    *,
    host: str | None,
    port: int | None,
    catalog_path: Path = CATALOG_PATH,
    base: Path | None = None,
) -> None:
    """Registra/atualiza `lang: voice` dentro da persona `persona_id` no
    catalogo, e a torna ativa para o alvo atual (local ou <host>:<port>).
    Chamadas repetidas com idiomas diferentes vao empilhando o pacote da
    mesma persona em vez de sobrescreve-lo."""
    catalog = load_catalog(catalog_path)
    bundle = dict(catalog.get(persona_id, {}))
    bundle[lang] = voice
    catalog[persona_id] = bundle
    save_catalog(catalog, catalog_path)

    active = load_active(base)
    active[target_key(host, port)] = persona_id
    save_active(active, base)


def resolve_persona_id(
    explicit: str | None,
    *,
    host: str | None,
    port: int | None,
    base: Path | None = None,
) -> str | None:
    """--persona explicito vence; senao olha o que esta ativo no
    .tts-md.persona do diretorio atual para o alvo (local/host:port)."""
    if explicit:
        return explicit
    active = load_active(base)
    return active.get(target_key(host, port))


def get_voice_bundle(
    persona_id: str, catalog_path: Path = CATALOG_PATH
) -> dict[str, str]:
    catalog = load_catalog(catalog_path)
    if persona_id not in catalog:
        raise PersonaError(
            f"Persona '{persona_id}' not found in {catalog_path}. "
            "Run --set-persona to create it on this machine."
        )
    return catalog[persona_id]


def apply_voice_overrides(
    blocks: list[SpeechBlock], overrides: dict[str, str]
) -> list[SpeechBlock]:
    """Aplica um pacote idioma->voz por cima do parse, respeitando qualquer
    voz que o proprio Markdown ja tenha fixado por linha (block.voice)."""
    if not overrides:
        return blocks
    for block in blocks:
        if block.voice is None:
            voice = overrides.get(block.lang)
            if voice:
                block.voice = voice
    return blocks
