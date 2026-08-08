# TTS_MD

Pipeline modular que converte Markdown em áudio usando parsers especializados e motores TTS offline (Kokoro + Piper).

## Requisitos

- Python 3.11+
- ffmpeg
- Modelos Kokoro (`.onnx` + `voices-v1.0.bin`), das [releases do kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0)
- Modelos Piper (`.onnx`) — opcional, só se algum idioma usar `engine: piper`

> Os modelos que o Speech Note baixa (`kokoro-v1_0.pth` + vozes `.pt`) são PyTorch e **não** funcionam aqui: o `kokoro-onnx` carrega o modelo via ONNX Runtime e as vozes via `np.load`.

Opcional para reprodução: `mpv`, `aplay` ou `ffplay`.

## Setup

```bash
cd TTS_MD
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

cp config.example.yaml config.yaml
# Edite config.yaml com os caminhos dos seus modelos
```

## Uso

```bash
tts-md examples/sample.md
tts-md examples/sample.md --output livro.mp3
tts-md examples/sample.md --lang pt-BR
tts-md examples/sample.md --play
tts-md examples/sample.md --stream
tts-md examples/sample.md --stream --output ~/audios/livro
tts-md examples/sample.md --stream --play
tts-md examples/sample.md --play --temp
tts-md examples/sample.md --stream --play --temp
tts-md examples/sample.md --debug-parser
tts-md examples/sample.md --keep-temp
tts-md examples/sample.md --config config.yaml
tts-md --text="Chame Payment.approve() antes" --play
```

Saída padrão: `output/<nome>.wav`

## Opção `--text`

Recebe o Markdown direto na linha de comando, no lugar do arquivo de entrada. Aceita as
mesmas opções (`--play`, `--stream`, `--temp`, `--debug-parser`, ...):

```bash
tts-md --text="Chame Payment.approve() antes" --play --temp
tts-md --text="# Título
Uma segunda linha." --stream
```

O arquivo e o `--text` são mutuamente exclusivos — passe um ou outro. Sem `--output`, o
nome da saída vem de um slug do próprio texto: `output/chame-payment-approve-antes.wav`.

## Modo `--stream`

Em vez de concatenar tudo num arquivo só, grava **um áudio por linha do Markdown**
num diretório (padrão `output/<nome>/`), junto de um `playlist.m3u`:

```
output/sample/
├── 001-aprovacao.wav
├── 002-o-debito-e-sincrono-dentro-do-approve.wav
├── 003-arquivo-arquivo-config-yaml.wav
└── playlist.m3u
```

- O prefixo numérico mantém a ordem em qualquer player; o slug do texto identifica a faixa.
- Cada arquivo aparece completo no disco assim que fica pronto (gravado no temporário e
  movido para o destino), e a playlist cresce junto — dá para começar a ouvir antes de a
  última linha ser sintetizada.
- Uma linha que mistura idiomas (`texto {lang:en-US}approve{/lang}`) continua sendo uma
  faixa só, com as vozes trocando dentro dela.
- Com `--play`, a primeira faixa toca enquanto as seguintes ainda são geradas.
- Aqui `--output` é um **diretório**, não um arquivo.

## Modo `--temp`

Para quando você só quer ouvir e não guardar nada: grava num diretório descartável sob o
temp do sistema (`/tmp/tts-md-XXXXXXXX/`) em vez de `output/`, e deixa a limpeza para o SO.

```bash
tts-md notas.md --play --temp           # arquivo único, tocado e esquecido
tts-md notas.md --stream --play --temp  # playlist descartável
```

Exige `--play` — sem reprodução o áudio ficaria num diretório que você nunca abre. Como o
destino é o scratch, não pode ser combinado com `--output`.

Não confundir com `--keep-temp`, que preserva o diretório de trabalho descrito abaixo.

## Diretório de trabalho

Os WAVs intermediários (um por bloco, mais os normalizados do ffmpeg) vão para um
diretório próprio de cada execução, nomeado pela origem do Markdown:

```
/tmp/tts-md/20260807-170603-func-md/          # a partir de func.md
/tmp/tts-md/20260807-170458-teste-rapido/     # a partir de --text="teste rapido..."
```

Com `--text`, o rótulo são os 15 primeiros caracteres do texto. O diretório é apagado no
fim; `--keep-temp` o preserva e o caminho é impresso na saída.

Antes era um `tmp/` fixo no diretório atual, compartilhado por todas as execuções: dois
`tts-md` ao mesmo tempo escreviam nos mesmos `001.wav`, `002.wav`… e um truncava o áudio
do outro **sem erro nenhum**.

## Índice de idioma por termo

`lang_index.yaml` diz em que idioma cada termo deve ser lido. O que não está no índice
cai no idioma padrão (`--lang`, ou `default_lang` do config):

```yaml
arquivo: pt
commit: en
dev:
  lang: en
  text: development   # troca também o que é falado
```

O valor pode ser só o idioma (`en`) ou um objeto com `lang` e `text` — útil para
abreviações: `dev` é falado como *development*, com voz inglesa. Termos são
case-insensitive e `pt`/`en` viram `pt-BR`/`en-US`.

Gerenciando pela CLI (adiciona e sai, sem sintetizar nada):

```bash
tts-md --add-term commit=en
tts-md --add-term dev=en:development
tts-md --add-term commit=pt              # já existe: mantém e avisa
tts-md --add-term commit=pt --replace    # substitui
tts-md --list-terms
```

Em lote, separando por vírgula:

```bash
tts-md --add-term "k8s=en, ingress=en, dev=en:development"
tts-md --add-term "sidecar=en, helm=en" --add-term nginx=en   # as duas formas se misturam
```

O lote inteiro é validado antes de gravar, então uma entrada malformada não deixa metade
dos termos aplicados — e o arquivo é escrito uma vez só, não uma por termo. Como a vírgula
separa as entradas, um texto falado que contenha vírgula precisa ir numa flag própria.

Consultando termos:

```bash
tts-md --exist-term commit          # en
tts-md --exist-term dev             # en:development
tts-md --exist-term "commit, dev"   # um valor por linha
```

Imprime o valor no mesmo formato que o `--add-term` recebe depois do `=`. Termo ausente
não escreve nada em `stdout` — vai um `not found:` para `stderr` e a saída é `1`, então dá
para usar em script:

```bash
if lang=$(tts-md --exist-term deploy 2>/dev/null); then
  echo "deploy é lido como $lang"
fi
```

O índice é aplicado **depois** dos outros parsers, sobre o texto que sobrou no idioma
padrão — quem já tem idioma definido (`{lang:en-US}...{/lang}`, nome de função) não é
tocado. Palavras vizinhas do mesmo idioma viram um bloco só, senão a fala sairia
picotada palavra a palavra.

## Pipeline

```
Markdown → Parsers → SpeechBlock IR → TTS Router → ffmpeg → áudio final
```

## Parsers incluídos

- Code blocks (ignorados)
- Tags multilíngua `{lang:en-US}...{/lang}` e `{en-US}...{/en-US}`
- Tabelas Markdown (ignoradas, com aviso "Leia a tabela no arquivo.")
- UUIDs (`3fa85f64-5717-4562-b3fc-2c963f66afa6`) — falados como "uuid de final a f a 6"
- Caminhos de arquivo (`/path/to/file.ext`)
- Nomes de função (`approve()`, `Payment.approve()`) — lidos com voz `en-US`
- URLs
- Headings, inline code e negrito
- Emojis (removidos)

## Configuração

Veja `config.example.yaml` para mapear idiomas para engines. Padrão:

- `pt-BR` → Kokoro, voz `pf_dora` (também há `pm_alex` e `pm_santa`)
- `en-US` → Kokoro, voz `af_bella`

A seção `piper` é opcional; omita-a se nenhum idioma usar `engine: piper`.

O idioma de cada bloco é repassado ao espeak-ng na fonemização (`pt-BR` → `pt-br`), então texto em português não é lido com fonemas de inglês.

## Nomes de função

`approve()` e `Payment.approve()` viram blocos em `en-US`, então a voz troca só no
identificador e volta ao português no resto da linha:

| Markdown                       | Fala                                |
| ------------------------------ | ----------------------------------- |
| `approve()`                    | `approve` (en-US)                   |
| `Payment.approve()`            | `Payment dot approve` (en-US)       |
| `getUserById()`                | `get User By Id` (en-US)            |
| `retry_failed(payment_id)`     | `retry failed` (en-US), args mudos  |

Parênteses vazios sempre casam. Com argumentos, o conteúdo precisa ter um marcador de
código (`,` `=` `_` `.` aspas ou dígito) para que o plural do português — `arquivo(s)`,
`item(ns)` — não seja lido como chamada de função.
