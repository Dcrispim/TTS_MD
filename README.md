# TTS_MD

Pipeline modular que converte Markdown em áudio usando parsers especializados e motores TTS offline (Kokoro + Piper).

## Requisitos

- Python 3.11+
- ffmpeg
- Modelos Kokoro (`.onnx` + `voices-v1.0.bin`), das [releases do kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0)
- Modelos Piper (`.onnx`) — opcional, só se algum idioma usar `engine: piper`

> Os modelos que o Speech Note baixa (`kokoro-v1_0.pth` + vozes `.pt`) são PyTorch e **não** funcionam aqui: o `kokoro-onnx` carrega o modelo via ONNX Runtime e as vozes via `np.load`.

Opcional para reprodução: `mpv`, `aplay` ou `ffplay`.

Opcional para o [modo `--video`](#modo---video): o extra `[video]` (Pillow + Pygments).

## Setup

```bash
cd TTS_MD
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
pip install -e '.[video]'   # opcional, só para --video

cp config.example.yaml config.yaml
# Edite config.yaml com os caminhos dos seus modelos
```

## Uso

```bash
tts-md examples/sample.md
tts-md examples/sample.md --output livro.mp3
tts-md examples/sample.md --lang pt-BR
tts-md examples/sample.md --play
tts-md examples/sample.md --speed 1.5
tts-md examples/sample.md --stream
tts-md examples/sample.md --stream --output ~/audios/livro
tts-md examples/sample.md --stream --play
tts-md examples/sample.md --play --temp
tts-md examples/sample.md --stream --play --temp
tts-md examples/sample.md --debug-parser
tts-md examples/sample.md --keep-temp
tts-md examples/sample.md --config config.yaml
tts-md --text="Chame Payment.approve() antes" --play
tts-md examples/video.md --video
```

Saída padrão: `output/<nome>.wav`

`--speed` multiplica a velocidade de todas as vozes (ex.: `1.5` para 50% mais rápido, `0.8`
para mais lento). Vale para o modo padrão e para `--stream`; precisa ser maior que zero.

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

## Servidor remoto (`--serve` / `--host`)

Uma máquina roda `tts-md --serve` e vira um "alto-falante de rede": qualquer outro
`tts-md` na LAN pode mandar texto pra ela falar, em vez de falar localmente.

```bash
# Na máquina que vai falar (ex.: a que tem caixa de som):
tts-md --serve --play

# Em qualquer outro PC da rede:
tts-md notas.md --host 192.168.1.50
tts-md --text="build quebrou" --host 192.168.1.50 --play  # --play aqui só vale no fallback, veja abaixo
```

- Quem decide **como** o áudio é tratado — `--play`, `--stream`, `--temp`,
  `--output`, `--keep-temp` — é o `--serve`, na hora que ele sobe, e vale pra
  todo pedido que chegar enquanto ele estiver de pé. Ex.: `tts-md --serve
  --stream --output ~/audios/live` grava cada pedido como uma playlist,
  `tts-md --serve --temp --play` fala e descarta.
- Quem manda o pedido (`--host`) só manda o **conteúdo**: o markdown/texto,
  `--lang` e `--speed`. As flags de execução do lado de quem manda não têm
  efeito enquanto o servidor responde — elas só voltam a valer se cair pro
  fallback local (veja `--check` abaixo), porque nesse caso é como se
  `--host` não tivesse sido passado.
- Pedidos concorrentes de PCs diferentes entram numa fila e tocam em ordem de
  chegada — o servidor nunca sobrepõe dois áudios.
- Porta padrão: `8420` (`--port`). Interface padrão do `--serve`: `0.0.0.0`
  (todas). Sem autenticação nessa versão — qualquer PC na rede que souber o
  host:porta pode mandar o servidor falar; use numa LAN confiável.
- `--check` faz um probe rápido (`~1.5s`) antes de mandar o texto de verdade;
  se o servidor não responder, cai pro TTS local com um aviso, em vez de
  falhar. Sem `--check`, host inalcançável é erro (saída 1).

Também dá pra configurar por variável de ambiente, pra não repetir `--host`
em toda chamada de uma máquina que sempre fala num servidor fixo:

```bash
export TTS_MD_HOST=192.168.1.50
export TTS_MD_PORT=8420   # opcional, já é o padrão
export TTS_MD_CHECK=1     # opcional, ativa o fallback local

tts-md notas.md   # usa o host/porta/check das envs, sem precisar repetir as flags
```

As mesmas variáveis podem ficar num arquivo `.env` **na raiz do projeto**, se você
prefere não mexer no perfil do shell. O prefixo `TTS_MD_` é opcional ali:

```bash
# TTS_MD/.env
HOST=192.168.1.50
PORT=8420
CHECK=1
```

Só o `.env` da raiz do TTS_MD é lido — nunca o do diretório de onde você chamou o
comando, já que `HOST` é um nome comum demais em `.env` de aplicação e um
`tts-md` rodado dentro de outro projeto acabaria mandando o áudio pro host dele.
O que estiver exportado no shell, e as flags da linha de comando, continuam
ganhando do arquivo.

Vídeo pelo servidor (`--serve --video`): veja [Modo `--video` › Servidor e cliente](#servidor-e-cliente).

## Modo `--video`

Em vez de só áudio, gera um **`.mp4`**: a narração continua igual, e a tela mostra imagens,
um ponteiro, blocos de código com destaque e, opcionalmente, legendas — tudo sincronizado
com a linha que está sendo falada. As tags que controlam a tela **nunca são faladas**.

```bash
pip install -e '.[video]'                         # Pillow + Pygments (extra [video])
tts-md examples/video.md --video                  # → output/video.mp4
tts-md examples/video.md --video --output aula.mp4
tts-md notas.md --video --images ~/prints          # refs relativas partem de ~/prints
tts-md examples/video.md --video --stream          # → output/video/NNN-*.mp4 + playlist.m3u
tts-md examples/video.md --video --play --temp     # toca no mpv/ffplay e descarta
```

[`examples/video.md`](examples/video.md) mostra imagem, grupo, ponteiros (px, %, nomeado), código
com `!{Lx}`/`!{Lx-y}`, `!{clear}` e duração, com as imagens de `examples/images/`.

Requisitos além dos do áudio: o extra `[video]` (sem ele: `--video requires pillow, pygments.
Install the video extra: pip install 'tts-md[video]'`), o `ffmpeg` (que já é exigido) e,
só para ponteiro customizado em SVG, um ffmpeg compilado com `librsvg`.

### Tags

| Tag | Efeito |
| --- | --- |
| `![[img.png]]` | Mostra a imagem até a próxima imagem, bloco de código visível, `!{clear}` ou heading |
| `![[img.png\|5s]]` | Idem, mas some 5 s depois da âncora (`s` ou `ms`, ex.: `1.5s`, `500ms`) |
| `![[a.png]] ![[b.png]]` | Grupo: lado a lado (grade de 2 linhas a partir de 4; aviso acima de 4) |
| `!{120,340}` | Ponteiro em px da imagem **original** (a última do grupo citada antes do ponteiro) |
| `!{50%,25%}` | Ponteiro em % da imagem (dá para misturar: `!{50%,120}`) |
| `!{b.png@120,340}` | Ponteiro numa imagem específica do grupo |
| `!{120,340\|2s}` | Ponteiro com duração; sem ela, fica até o próximo ponteiro ou a troca de tela |
| `!{L3}` / `!{L3-5}` | Destaca linhas do bloco de código na tela (1-based, relativas ao bloco); aceita `\|Ns` |
| `!{clear}` | Limpa a tela (volta ao fundo/`cover`) |
| ```` ```python !hide ```` | Bloco de código que **não** vai para a tela |

- A referência é um caminho, resolvido com uma base só, sem fallback: o `--images DIR`, se
  dado; senão o diretório do `.md`; com `--text`, o diretório atual. Absolutos e `~` valem
  como estão.
- Uma tag malformada (`!{abc}`, `![[x|5]]`, `!{L0}`) é removida e vira aviso; uma tag sem
  fechamento (`!{10,20`) continua sendo falada, com aviso.
- Dentro de código (inline `` `!{clear}` `` ou bloco cercado) a tag é texto comum: nem
  vira cue nem some — é assim que dá para documentar a sintaxe.
- Uma tag colada antes de uma cerca (`!{clear}```py`) não é removida (removê-la abriria um
  bloco de código): a linha é falada como está, com aviso. Tag na cerca de fechamento é
  ignorada, com aviso.
- Os avisos vão para o `stderr`, prefixados com `warning:`. Os das tags (`warning: line N: ...`)
  saem **antes** da síntese. Os da timeline e do render (fonte pequena, grupo grande,
  ponteiro fora da imagem, legenda cortada, tela visível por menos de 0,5 s) saem no fim,
  em português (`linha N: ...`, `grupo com ...`, `python: ...`).

### Regras de tempo

- **Âncora:** a posição da tag na linha. Ela é proporcional ao texto: no meio da frase,
  aponta para o meio do áudio daquela linha. Uma tag sozinha numa linha (sem fala) vale
  para o início da próxima linha falada — inclusive a imagem logo acima de um `# Título`,
  que pertence à seção desse heading.
- **`lead`** (padrão 1 s): a tela aparece esse tanto **antes** da âncora, com piso em 0.
  Ela nunca entra antes da âncora da tela anterior, e uma tag nunca aparece antes de uma tag
  anterior no texto.
- **`group_gap`** (padrão 1 s): imagens cujas âncoras ficam a menos disso uma da outra (em
  cadeia) formam um grupo e aparecem juntas desde o início. `clear`, heading e código
  visível quebram o grupo. A duração de um grupo conta a partir da última referência.
- **Fim de uma tela:** próxima imagem (fora do grupo), bloco de código visível, heading,
  `!{clear}` ou a duração explícita (`|Ns`, contada da âncora, sem o `lead`).

### Código

Um bloco cercado (sem `!hide`) entra na tela ancorado na primeira linha falada depois
dele (com o mesmo `lead` das imagens: por padrão, 1 s antes dela) e substitui a imagem. Ele continua mudo no áudio, como sempre.

- Mostra no máximo `code.max_lines` linhas (padrão 60, por linguagem; mínimo 7). Com
  `!{Lx-y}`, a janela mostra 5 linhas acima do trecho e o resto abaixo, deslocada nas
  bordas; as linhas cortadas aparecem como `⋯ +N linhas` em cima/embaixo.
- Números de linha reais, realce do Pygments (`code.theme`, padrão `monokai`; tema
  inexistente cai no `default` com aviso) e o trecho apontado com uma faixa
  `code.highlight`. Depois que o destaque expira (`|Ns`), a janela fica onde estava.
- A fonte é a monoespaçada do sistema (`fc-match monospace`) ou `code.font`, dimensionada
  para caber; a altura do bloco define o tamanho: abaixo de `code.min_font_px` (14) sai um aviso
  (reduza `max_lines`). Linhas largas demais não reduzem a fonte abaixo do mínimo: são
  cortadas com `…`, com um aviso próprio (quebre as linhas longas).

### Legendas

Com `captions.enabled: true`, a linha falada aparece como legenda (sem `#`, ênfase e
links; código inline fica literal). Acima de `captions.max_lines` linhas, o texto é
dividido em pedaços que se alternam no tempo, proporcionalmente.

- Uma faixa fixa é **reservada** para a legenda sempre que elas estão ligadas, mesmo nos
  trechos sem texto (~171 px em 1080p com os padrões), para a imagem e o código não mudarem
  de tamanho entre estados. A capa (`cover`) continua ocupando o quadro todo.
- `position: auto` (padrão) põe a legenda embaixo e a sobe quando o ponteiro está na
  metade de baixo do quadro — por isso, no `auto`, a imagem desce/sobe quando o ponteiro
  troca de metade. `top`/`bottom` fixam a posição.
- Se o texto ainda passar de `max_lines` depois da quebra em pixels, é cortado com `…` e
  aviso.

### Configuração (`video:`)

Seção opcional do config; sem ela valem os defaults. O bloco comentado e explicado está em
`config.example.yaml`. Resumo:

```yaml
video:
  size: [1920, 1080]
  fps: 2                 # grade mínima de quadros; veja abaixo
  background: "#000000"
  cover: null            # fundo quando não há imagem nem código na tela
  fit: contain           # contain | cover
  lead: 1.0
  group_gap: 1.0
  pointer: {image: null, size: 48, color: "#ff3b30", hotspot: [0, 0]}
  code:
    theme: monokai
    font: null
    min_font_px: 14
    highlight: "#ffd60a40"
    max_lines: {default: 60}   # ex.: {default: 60, tsx: 100}
  captions: {enabled: false, position: auto, max_lines: 2, font_px: 36, background: "#000000b0"}
  upload_max_side: 1280  # cliente --host: lado máximo das imagens enviadas
  max_body_mb: 50        # --serve: corpo máximo da requisição
```

- Cores em `#rrggbb` ou `#rrggbbaa`. Caminhos relativos (`cover`, `pointer.image`) partem
  do diretório do arquivo de config (o real, se for symlink).
- `fps` **não** é a precisão das trocas: o vídeo é VFR e cada troca cai na fronteira exata
  (quantizada em ~0,04 s pelo ffmpeg); o `fps` só garante um quadro a cada `1/fps` s, para
  seek e preview.
- `pointer.color` só vale para a seta embutida. Um `pointer.image` PNG/SVG mantém as cores
  dele, é escalado para `pointer.size` no maior lado, e o `hotspot` (em px da imagem
  natural) é o ponto que marca o alvo.

### Saída, `--stream` e `--play`

- `--video` grava `output/<nome>.mp4` (H.264 + AAC, mesma duração do áudio); `--output`
  precisa terminar em `.mp4`. Com `--temp`, vai para o scratch, como no áudio.
- Antes de sintetizar qualquer coisa, todas as imagens são abertas; se faltar alguma (ou
  não for imagem), o comando aborta listando **todas** — nada é sintetizado:

  ```
  Error: 2 image(s) referenced in the Markdown could not be loaded (relative paths resolve against /home/eu/notas); nothing was synthesized:
    diagrama.png: not found: /home/eu/notas/diagrama.png
    quebrada.png: not a readable image: /home/eu/notas/quebrada.png
  ```

- `--stream --video`: um `.mp4` por linha (os mesmos nomes do `--stream` de áudio) mais
  um `playlist.m3u` com os `.mp4`. Cada segmento começa com a tela que estava valendo
  (imagem, ponteiro ou código abertos em linhas anteriores); o `lead` não atravessa
  segmentos. `--output` é um diretório.
- `--play --video` toca no `mpv` (com vídeo) ou no `ffplay`. O `aplay` só toca áudio, então
  é recusado antes da síntese. `--last` reabre um `.mp4` como vídeo.

### Servidor e cliente

Quem liga o vídeo é o operador do servidor, como as outras flags de execução de
[`--serve`](#servidor-remoto---serve----host):

```bash
tts-md --serve --video                                   # todo pedido vira output/<slug>.mp4
tts-md --serve --video --stream --output ~/videos/live   # um diretório de .mp4 por pedido

tts-md examples/video.md --host 192.168.1.50             # o cliente envia as imagens
tts-md notas.md --host 192.168.1.50 --images ~/prints
```

- `--serve --video` gera mp4 para **todo** pedido, até sem tags. Exige o extra `[video]`
  no servidor (e mpv/ffplay com `--play`); `--images` não se aplica ao `--serve`.
- O servidor **nunca** lê imagens do próprio disco: o cliente resolve as referências no
  disco dele (mesma base do modo local, `--images` vale com `--host`), aborta localmente
  se faltar alguma, reduz cada imagem para no máximo `video.upload_max_side` px no maior
  lado (da config **do cliente**; sem ampliar), recodifica em PNG e envia. O cliente
  também precisa do extra `[video]`.
- Isso só acontece quando o texto tem tags `!`; sem tags, o pedido é o mesmo de antes.
- Servidor sem vídeo (ou de versão antiga): o cliente remove as tags localmente, manda só
  o texto e avisa `warning: server at H:P has no video support; only the audio will be
  generated (the ! tags were removed locally)`.
- O `--video` do cliente não muda o pedido remoto; ele só vale no fallback local do
  `--check`. Os avisos do render no servidor voltam na resposta e saem no `stderr` do
  cliente.

Contrato HTTP, para quem integra sem o cliente:

- `GET /health` → `{"status": "ok", "video": true|false}`.
- `POST /speak`:

  ```json
  {"text": "![[a.png]] Veja !{50%,50%} aqui.", "lang": null, "speed": 1.0, "persona": null,
   "images": {"a.png": {"data": "<base64>", "size": [3000, 2000]}}}
  ```

  A chave de `images` é a referência **literal** do `![[...]]`. `size` é o tamanho
  original (opcional; sem ele vale o da imagem enviada) e serve para converter as
  coordenadas em px do ponteiro — por isso uma imagem reduzida no envio aponta para o
  mesmo lugar. Formatos aceitos: PNG, JPEG, GIF, WEBP, BMP e TIFF; no máximo 16 Mpx por
  imagem e 64 Mpx somando o pedido; `size` com inteiros de 1 a 100000. Com vídeo, a
  resposta 200 ganha `"video": true` e `"warnings": [...]`.
- `400` (antes de enfileirar, nada é sintetizado): referência sem upload ou upload
  inválido, com `{"error": "...", "missing": {"a.png": "not uploaded"}}`. Também para
  `Content-Length` inválido ou corpo que não é um objeto JSON. Servidor sem vídeo ignora
  `images` e só remove as tags.
- `413`: corpo acima de `video.max_body_mb` (MiB, padrão 50, da config do servidor). Vale
  para **qualquer** `--serve`, com ou sem vídeo, e é checado pelo `Content-Length`, sem
  ler o corpo. O cliente vê a conexão fechada e cita o `video.max_body_mb` no erro.

### Limitações conhecidas

- As trocas de tela podem atrasar até ~0,04 s (arredondamento do ffmpeg). Ao extrair
  quadros, `ffmpeg -ss T` antes do `-i` pode pegar o quadro seguinte perto de uma troca.
- A imagem/código sempre fica com pelo menos metade do quadro: legenda grande num quadro
  pequeno volta a sobrepor alguns pixels.
- Uma mesma imagem repetida num grupo recebe o ponteiro na primeira ocorrência.
- Uma palavra única mais larga que o quadro, na última linha da legenda, vira só `…`.

## Personas

Uma persona é um pacote de vozes por idioma (`{pt-BR: pf_dora, en-US: af_bella, ...}`)
guardado com um id, para não ter que lembrar/repetir `--voice` toda vez. `--set-persona`
só registra e ativa — não sintetiza nada:

```bash
# Registra "diego" para pt-BR e para en-US (chamadas separadas, mesma persona):
tts-md --set-persona diego --lang pt-BR --voice pf_dora
tts-md --set-persona diego --lang en-US --voice af_bella

# A partir daqui, toda chamada nesse diretório já fala com "diego":
tts-md notas.md
tts-md --text "Hello there" --lang en-US
```

- `--voice <id>` sozinho (sem `--set-persona`) troca a voz só para essa execução, sem
  mexer em persona nenhuma. Vale para `--lang` (ou o idioma padrão do config) e precisa
  bater com o idioma — `--voice pf_dora --lang en-US` é rejeitado, porque `pf_dora` é uma
  voz `pt-BR` (Kokoro identifica o idioma pelo prefixo da voz/nome do modelo).
- O catálogo fica em `personas.json`, na raiz da instalação do tts-md — não versionado,
  igual ao `config.yaml`. Cada máquina tem o seu; o mesmo id pode apontar para uma voz
  diferente numa instalação remota.
- A persona ativa por diretório fica em `.tts-md.persona` (JSON, também não versionado),
  com uma entrada por alvo: `"local"` (sempre a primeira) e uma `"<host>:<port>"` por
  servidor `--host` já usado dali. Rodar `--set-persona ... --host X --port Y` ativa a
  persona só para aquele servidor, sem mexer na entrada `"local"`.
- Com `--host`, o id é mandado como está no pedido (`persona`) — quem resolve pra voz é o
  `personas.json` **do servidor**, não o desta máquina. Se o servidor não tiver esse id
  cadastrado, o pedido falha com erro, igual a qualquer outra falha de síntese.
- `--persona <id>` usa uma persona pontualmente, sem tocar no `.tts-md.persona`; é o que
  vale quando dado junto, por cima do que estiver ativo no diretório.

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
