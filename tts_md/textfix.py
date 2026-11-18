"""Conserto de palavras partidas por conversoes ruins de PDF para ebook.

Alguns livros (tipicamente gerados de um PDF) trazem um espaco depois de
todo "m" e "j": "tom ei o elevador e cam inhei algum as quadras", "j ogada",
"m em ória". O TTS le os pedacos como palavras soltas.

O conserto so roda num livro que tem o defeito de fato: conta as junções
inequivocas (um pedaco invalido, a junção valida: "prim eiro", "j ogada") e,
passado o limiar, junta toda sequencia "...m"/"...j" + pedacos cujo
resultado seja palavra do dicionario (hunspell). Pares curtos ambiguos
("com o", "um a", "tem a") ficam como estao.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

SPLIT_ENDINGS = ("m", "j")
MAX_PIECES = 4
# Livro afetado: pelo menos isso de junções inequivocas, absolutas e relativas.
MIN_CLEAR_SPLITS = 30
MIN_CLEAR_RATIO = 0.001

# "um" fica de fora: num livro afetado, "um a" e' quase sempre "uma".
AMBIGUOUS_FIRST = {"com", "em", "tem", "sem", "nem", "bem", "quem", "cem", "sim"}
AMBIGUOUS_SECOND = {"o", "a", "os", "as", "e", "é", "à", "ao", "aos", "às", "um", "uns"}

# Nucleo pode ter hifen ("diga-m", "udá-lo"); pontuacao fica nas pontas.
TOKEN_RE = re.compile(r"^([^\w]*)(\w+(?:-\w+)*)([^\w]*)$", re.UNICODE)


def find_dictionary(lang: str = "pt-BR") -> Path | None:
    """Caminho base (sem extensao) do dicionario hunspell do idioma, se houver."""
    name = lang.replace("-", "_")
    dirs = [
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "hunspell",
        Path("/usr/share/hunspell"),
        Path("/usr/share/myspell"),
        Path("/usr/share/myspell/dicts"),
    ]
    for base in dirs:
        if (base / f"{name}.dic").exists() and (base / f"{name}.aff").exists():
            return base / name
    return None


def valid_words(words: set[str], dictionary: Path) -> set[str]:
    """Quais palavras o hunspell aceita (uma unica chamada para o lote todo)."""
    exe = shutil.which("hunspell")
    if exe is None or not words:
        return set()
    result = subprocess.run(
        [exe, "-i", "utf-8", "-d", str(dictionary), "-G"],
        input="\n".join(sorted(words)),
        capture_output=True,
        text=True,
        timeout=300,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _split(token: str) -> tuple[str, str, str] | None:
    match = TOKEN_RE.match(token)
    if not match or not match.group(2).replace("-", "").replace("_", "").isalpha():
        return None
    return match.group(1), match.group(2), match.group(3)


def _chains(tokens: list[str]):
    """Gera (i, k, palavra juntada) para cada sequencia candidata tokens[i..k]."""
    parts = [_split(t) for t in tokens]
    for i, part in enumerate(parts):
        if part is None or part[2] or not part[1].lower().endswith(SPLIT_ENDINGS):
            continue
        joined = part[1]
        for k in range(i + 1, min(i + MAX_PIECES, len(tokens))):
            nxt = parts[k]
            if nxt is None or nxt[0]:
                break
            joined += nxt[1]
            yield i, k, joined
            if nxt[2] or not nxt[1].lower().endswith(SPLIT_ENDINGS):
                break


def _is_word(word: str, valid: set[str]) -> bool:
    """O hunspell separa palavras com hifen: "diga-me" vale se "diga" e "me" valem."""
    return all(part in valid for part in word.split("-") if part)


def _is_piece(word: str, valid: set[str]) -> bool:
    """Pedaco que nao se sustenta sozinho. "m"/"j" soltos contam como pedaco:
    o hunspell os aceita (abreviacoes), mas num livro afetado sao sempre restos."""
    last = word.split("-")[-1]
    return not _is_word(word, valid) or last.lower() in SPLIT_ENDINGS


def _name_split(tokens: list[str], i: int, k: int, valid: set[str]) -> bool:
    """Nome proprio partido ("Sham ira", "Am ael"): o primeiro pedaco nao e'
    palavra e o seguinte comeca minusculo e nao e' artigo/conjuncao solta."""
    if k != i + 1:
        return False
    first, second = _split(tokens[i]), _split(tokens[k])
    return (
        _is_piece(first[1], valid)
        and _is_piece(first[1].lower(), valid)
        and second[1][:1].islower()
        and second[1] not in AMBIGUOUS_SECOND
    )


def _ambiguous(tokens: list[str], i: int, k: int) -> bool:
    if k != i + 1:
        return False
    first, second = _split(tokens[i]), _split(tokens[k])
    return first[1].lower() in AMBIGUOUS_FIRST and second[1].lower() in AMBIGUOUS_SECOND


def repair_split_words(text: str, dictionary: Path | None) -> tuple[str, int]:
    """Devolve (texto consertado, quantas palavras foram juntadas).

    Sem dicionario, ou num livro sem o defeito, devolve o texto intacto e 0.
    """
    if dictionary is None:
        return text, 0
    lines = text.split("\n")
    tokenized = [line.split(" ") for line in lines]

    candidates: set[str] = set()
    pieces: set[str] = set()
    total_words = 0
    for tokens in tokenized:
        total_words += len(tokens)
        for i, k, joined in _chains(tokens):
            candidates.update(joined.split("-"))
            for t in tokens[i : k + 1]:
                part = _split(t)
                if part:
                    pieces.update(part[1].split("-"))
    if not candidates:
        return text, 0

    valid = valid_words(candidates | pieces, dictionary)

    clear = 0
    for tokens in tokenized:
        for i, k, joined in _chains(tokens):
            if _is_word(joined, valid) and any(
                _is_piece((_split(t) or ("", "", ""))[1], valid) for t in tokens[i : k + 1]
            ):
                clear += 1
    if clear < MIN_CLEAR_SPLITS or clear < MIN_CLEAR_RATIO * total_words:
        return text, 0

    fixed = 0
    out_lines: list[str] = []
    for tokens in tokenized:
        best: dict[int, int] = {}
        for i, k, joined in _chains(tokens):
            if (_is_word(joined, valid) and not _ambiguous(tokens, i, k)) or _name_split(tokens, i, k, valid):
                best[i] = max(best.get(i, i), k)
        if not best:
            out_lines.append(" ".join(tokens))
            continue
        merged: list[str] = []
        i = 0
        while i < len(tokens):
            k = best.get(i)
            if k is None:
                merged.append(tokens[i])
                i += 1
                continue
            merged.append("".join(tokens[i : k + 1]))
            fixed += 1
            i = k + 1
        out_lines.append(" ".join(merged))
    return "\n".join(out_lines), fixed
