from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_INDEX_PATH = Path(__file__).resolve().parent.parent / "lang_index.yaml"

# Semente: usada quando lang_index.yaml ainda nao existe.
LANG_INDEX: dict[str, str | dict[str, str]] = {
    "arquivo": "pt",
    "configuração": "pt",
    "usuário": "pt",
    "approve": "en",
    "commit": "en",
    "deploy": "en",
    "API": "pt",
}

# O indice usa codigos curtos; o config.yaml mapeia vozes por codigo completo.
LANG_ALIASES = {"pt": "pt-BR", "en": "en-US"}

HEADER = (
    "# Idioma de leitura por termo. Editavel a mao ou via `tts-md --add-term`.\n"
    "#\n"
    "#   termo: en                        # so o idioma\n"
    "#   dev: {lang: en, text: development}  # idioma + o que falar no lugar\n"
    "#\n"
    "# Idiomas aceitos: pt, en (ou pt-BR, en-US). Termos sao case-insensitive.\n"
)


def normalize_lang(lang: str) -> str:
    """'pt' e 'en' viram os codigos completos que o config.yaml conhece."""
    stripped = lang.strip()
    return LANG_ALIASES.get(stripped.lower(), stripped)


@dataclass(frozen=True)
class Term:
    lang: str
    text: str | None = None  # fala no lugar do termo: "dev" -> "development"

    @classmethod
    def from_entry(cls, term: str, entry: str | dict[str, str]) -> Term:
        # O valor pode ser so o idioma ("en") ou um objeto com o texto falado.
        if isinstance(entry, dict):
            if "lang" not in entry:
                raise ValueError(f"Term '{term}' is an object without a 'lang' key.")
            text = entry.get("text")
            return cls(
                lang=normalize_lang(str(entry["lang"])),
                text=str(text) if text else None,
            )
        return cls(lang=normalize_lang(str(entry)))


def load_raw(path: Path = DEFAULT_INDEX_PATH) -> dict[str, str | dict[str, str]]:
    """Le o arquivo como esta em disco, preservando a grafia dos termos."""
    if not path.exists():
        return dict(LANG_INDEX)
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_index(path: Path = DEFAULT_INDEX_PATH) -> dict[str, Term]:
    """Indice pronto para consulta: chaves em minusculas, idiomas normalizados."""
    return {
        str(term).strip().lower(): Term.from_entry(str(term), entry)
        for term, entry in load_raw(path).items()
    }


def save_raw(
    data: dict[str, str | dict[str, str]],
    path: Path = DEFAULT_INDEX_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=True, default_flow_style=False)
    path.write_text(HEADER + body, encoding="utf-8")


def format_entry(entry: str | dict[str, str]) -> str:
    """Valor no formato compacto — o mesmo que o --add-term aceita depois do '='."""
    if isinstance(entry, dict):
        text = entry.get("text")
        lang = str(entry.get("lang", ""))
        return f"{lang}:{text}" if text else lang
    return str(entry)


def find_key(data: dict[str, str | dict[str, str]], term: str) -> str | None:
    """Chave do termo como esta escrita no arquivo, ignorando a caixa."""
    wanted = term.strip().lower()
    return next((key for key in data if str(key).strip().lower() == wanted), None)


def lookup_term(
    term: str,
    path: Path = DEFAULT_INDEX_PATH,
) -> tuple[str, str | dict[str, str]] | None:
    """(chave, valor) do termo no indice, ou None se ele nao estiver la."""
    data = load_raw(path)
    key = find_key(data, term)
    return None if key is None else (key, data[key])


def split_specs(raw: str) -> list[str]:
    """Quebra um lote — 'k8s=en, deploy=en' — nas entradas individuais.

    A virgula separa entradas, entao o texto falado de um termo nao pode
    conter virgula; use uma flag so para ele nesse caso.
    """
    return [part.strip() for part in raw.split(",") if part.strip()]


def expand_specs(specs: Iterable[str]) -> list[str]:
    """Junta as flags repetidas e os lotes de cada uma numa lista unica."""
    return [entry for spec in specs for entry in split_specs(spec)]


def parse_spec(spec: str) -> tuple[str, str, str | None]:
    """Le 'termo=idioma' ou 'termo=idioma:texto' vindo do --add-term."""
    term, sep, value = spec.partition("=")
    if not sep:
        raise ValueError(
            f"Expected TERM=LANG[:TEXT], got '{spec}'. Example: dev=en:development"
        )

    term = term.strip()
    lang, _, text = value.partition(":")
    lang = lang.strip()
    text = text.strip()

    if not term:
        raise ValueError(f"Missing term in '{spec}'.")
    if not lang:
        raise ValueError(f"Missing language in '{spec}'. Example: commit=en")

    return term, lang, text or None


def add_terms(
    entries: Iterable[tuple[str, str, str | None]],
    *,
    replace: bool = False,
    path: Path = DEFAULT_INDEX_PATH,
) -> list[tuple[str, str, str | dict[str, str]]]:
    """Grava varios termos numa unica leitura/escrita do indice.

    Devolve (termo, acao, valor) por entrada, na ordem recebida — acao e
    added, replaced ou kept. Um lote grande gravado termo a termo reescreveria
    o arquivo inteiro a cada um, e uma falha no meio deixaria metade aplicada.
    """
    data = load_raw(path)
    results: list[tuple[str, str, str | dict[str, str]]] = []
    changed = False

    for term, lang, text in entries:
        existing_key = find_key(data, term)

        if existing_key is not None and not replace:
            results.append((term, "kept", data[existing_key]))
            continue

        entry: str | dict[str, str] = {"lang": lang, "text": text} if text else lang
        if existing_key is not None:
            # Sai a chave antiga para a grafia nova valer.
            del data[existing_key]

        data[term] = entry
        changed = True
        results.append(
            (term, "replaced" if existing_key is not None else "added", entry)
        )

    if changed:
        save_raw(data, path)
    return results


def add_term(
    term: str,
    lang: str,
    *,
    text: str | None = None,
    replace: bool = False,
    path: Path = DEFAULT_INDEX_PATH,
) -> tuple[str, str | dict[str, str]]:
    """Grava um termo no indice. Devolve (acao, valor) — added, replaced ou kept."""
    (_, action, entry) = add_terms(
        [(term, lang, text)], replace=replace, path=path
    )[0]
    return action, entry
