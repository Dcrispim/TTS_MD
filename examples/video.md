# Modo vídeo do tts-md

Este exemplo mostra imagens, ponteiros, código e a limpeza da tela.

![[images/arquitetura.png]]
A requisição sai do cliente !{150,300}, passa pelo servidor !{400,300} e termina no banco !{650,300}.
O servidor !{50%,50%} é quem decide a resposta.

!{clear}
Com a tela limpa, a narração continua sem imagem.

## Antes e depois

![[images/antes.png]] ![[images/depois.png]] Compare a versão antiga, à esquerda, com a nova, à direita.
Na versão nova, o botão !{images/depois.png@300,280} ficou maior e verde.

## Código

```python
def total(itens):
    soma = 0
    for item in itens:
        soma += item.preco
    return soma
```

A função começa aqui !{L1} e soma o preço de cada item !{L3-4}.
No fim, a linha !{L5} devolve o total.

## Fim

![[images/logo.png|2s]] Obrigado por assistir.
A tela volta para o fundo depois de dois segundos.
