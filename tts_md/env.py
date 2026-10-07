"""Leitura do .env do projeto.

O CLI le TTS_MD_HOST/TTS_MD_PORT/TTS_MD_CHECK do ambiente (envvar do Click),
o que so serve pra quem exporta as variaveis no shell. Este modulo carrega o
mesmo par chave=valor de um arquivo .env, pra que uma maquina possa ter um
destino padrao sem depender do perfil do shell.

Sem dependencia externa: o formato aceito e' o minimo util (KEY=VALUE, com
`export` opcional, comentarios com # e aspas ao redor do valor).
"""

from __future__ import annotations

import os
from pathlib import Path

# O .env pode escrever tanto TTS_MD_HOST quanto o nome curto HOST. O apelido
# curto vale SO pra valor vindo do arquivo: promover um HOST qualquer do
# ambiente real a destino de TTS sequestraria uma variavel comum demais.
_ALIASES = {
    "HOST": "TTS_MD_HOST",
    "PORT": "TTS_MD_PORT",
    "CHECK": "TTS_MD_CHECK",
}


def project_root() -> Path:
    """Raiz do repositorio (o diretorio que contem o pacote tts_md)."""
    return Path(__file__).resolve().parent.parent


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_dotenv(*, root: Path | None = None) -> dict[str, str]:
    """Popula os.environ com o .env da raiz do projeto, sem sobrescrever nada.

    So a raiz do TTS_MD e' lida, nunca o .env do diretorio de onde o comando
    foi chamado: HOST e' um nome comum demais em .env de aplicacao, e ler o do
    cwd faria um `tts-md` rodado dentro de outro projeto mandar o audio pro
    host daquele projeto.

    O ambiente ja definido ganha do arquivo, entao exportar no shell (ou
    passar --host na linha de comando) continua sobrescrevendo o .env.
    """
    path = (root or project_root()) / ".env"
    if not path.is_file():
        return {}

    applied: dict[str, str] = {}
    for key, value in parse_env(path.read_text(encoding="utf-8")).items():
        name = _ALIASES.get(key, key)
        if os.environ.get(name):
            continue
        os.environ[name] = value
        applied[name] = value
    return applied
