# Marvin: Assistente Pessoal Local CLI

Marvin e um assistente pessoal local executado diretamente no terminal via comando `mvn`. Ele opera sobre um modelo llamafile escolhido pelo usuario, rodando em segundo plano como um servico de usuario do systemd, sem interface grafica, sem navegadores e com desempenho maximo sem limitacoes artificiais de CPU ou memoria.

## Instalacao rapida

Em Linux com systemd, baixe e execute o instalador:

```bash
curl -fsSL https://raw.githubusercontent.com/fellypedarosa/marvin/main/scripts/marvin.sh -o marvin.sh
sh marvin.sh
```

O instalador clona o repositorio em `~/marvin`, prepara `system_prompt.md`, cria `.env` com um `SEARXNG_SECRET` aleatorio, configura o ambiente Python, instala o atalho `~/.local/bin/mvn`, inicia o SearXNG e instala/inicia o servico de usuario. Ele preserva `.env` e `system_prompt.md` se ja existirem. Se faltar algum pacote no Arch Linux ou derivados, pergunta antes de instalar com `sudo`; em outras distribuicoes, dependencias ausentes devem ser instaladas conforme a documentacao da distribuicao e do [Docker Engine](https://docs.docker.com/engine/install/).

Quando nao houver um modelo `.llamafile` na raiz do projeto, o instalador pergunta se deseja baixar o Qwen3.5-2B-Q8_0. Tambem e possivel responder nao, salvar um unico arquivo `.llamafile` em `~/marvin` e pressionar Enter para o instalador procurar novamente. Modelos alternativos estao listados na [documentacao do llamafile](https://docs.mozilla.ai/llamafile/getting-started/pre-built-llamafiles). O servico exige exatamente um arquivo `.llamafile` na raiz.

Para trocar o modelo depois, mantenha somente o modelo desejado na raiz de `~/marvin` e execute:

```bash
~/marvin/scripts/trocar-model.sh
```

Esse script valida a selecao e reinicia `marvin.service`. O `scripts/run-model.sh` e chamado pelo systemd e inicia o unico modelo encontrado; nao e o comando de troca.

---
<img width="852" height="683" alt="bash" src="https://github.com/user-attachments/assets/a9938209-b4fe-4678-a524-d28f5a4a8541" />

## 1. Arquitetura

O sistema e composto por tres componentes principais:

1. **Backend Daemon (`marvin.service`)**:
   - Gerenciado via `systemd --user`.
   - Executa o unico arquivo `*.llamafile` encontrado na raiz de `~/marvin` em modo `--server`.
   - Escuta exclusivamente em `127.0.0.1:8088`.
   - Utiliza buffer de contexto de 8192 tokens e dynamic batching (`--cont-batching`).
   - Inicia automaticamente no boot do sistema operacional via `loginctl enable-linger`.

2. **Cliente CLI (`mvn`)**:
   - Atalho executavel em `~/.local/bin/mvn`, que chama o cliente Python do projeto usando o ambiente virtual local.
   - Implementado em Python 3 e usa Rich para formatar Markdown e paineis no terminal.
   - Comunica-se com a API local (`/v1/chat/completions`) utilizando Server-Sent Events (SSE) e exibe a resposta formatada quando ela fica pronta.
   - Suporta modo stateless, modo com memoria, sessoes nomeadas, pipes Unix e chat interativo.

3. **Armazenamento de Memoria**:
   - Armazenado em `~/.local/share/marvin/sessions/` em formato JSON legivel.
   - Por padrao, o Marvin e 100% stateless (nao grava nem envia historico). A memoria so e lida e persistida quando explicitamente solicitado pelo usuario (`-m` ou `-s`).
   - Sessoes persistidas retêm no maximo 30 mensagens (normalmente 15 trocas) e 12.000 caracteres, removendo as trocas mais antigas para limitar o contexto enviado ao modelo e o crescimento dos arquivos.

4. **Pesquisa na Web (opcional)**:
   - O modelo pode solicitar uma pesquisa quando a pergunta exigir informacoes atuais ou fontes externas.
   - O cliente consulta uma instancia local do SearXNG, devolve ate cinco resultados ao modelo e tenta extrair o texto das tres primeiras paginas publicas para fundamentar melhor a resposta. Se a extração falhar, usa o resumo do resultado.
   - O SearXNG e software livre e nao exige conta, chave de API ou pagamento por consulta. Os mecanismos de busca agregados podem aplicar seus proprios limites.
   - Google e Bing recebem peso maior na ordenacao; os demais mecanismos ativos continuam disponiveis como fontes adicionais. O Marvin informa no contexto quando o SearXNG reporta mecanismos sem resposta.
   - A instancia fica vinculada a `127.0.0.1:8888`. As consultas sao encaminhadas pelo SearXNG aos mecanismos externos configurados; o modelo continua rodando localmente. O cliente tenta abrir e extrair texto de ate tres paginas HTML publicas.

---

## Testes Automatizados

Instale a dependencia do cliente:

```bash
python3 -m pip install -r requirements.txt
```

O Rich renderiza respostas Markdown, titulos, listas e blocos de codigo. As cores sao detectadas pelo terminal e podem ser desativadas com `NO_COLOR=1`. Quando a saida e redirecionada, o Rich remove os codigos de cor.

Execute a suite com a biblioteca `unittest` do Python:

```bash
python3 -m unittest discover -s tests -v
```

A mesma suíte verifica se arquivos públicos ou ainda não ignorados contêm
padrões de credenciais, dados pessoais ou endereços de rede privados. O
relatório mostra apenas o arquivo, a linha e a categoria detectada, sem imprimir
o conteúdo encontrado. O arquivo `system_prompt.md`, que pode conter instruções
pessoais, deve permanecer fora do Git; use `system_prompt.example.md` como
modelo público. Essa verificação cobre o conteúdo atual da árvore de trabalho,
não o histórico de commits. Para procurar segredos no histórico, use um scanner
dedicado como o Gitleaks e habilite a proteção de envio do GitHub.

### Configuracao local do SearXNG

O Compose carrega automaticamente as variaveis do arquivo `.env`. O instalador
cria esse arquivo com uma chave aleatoria. Em uma configuracao manual, crie-o a
partir do modelo e ajuste o valor para sua instalacao:

```bash
cp .env.example .env
```

O arquivo `.env` e ignorado pelo Git. Mantenha nele os valores locais; o
`.env.example` e apenas uma referencia versionada.

Os testes simulam as respostas do modelo e do SearXNG e usam uma pasta temporaria para as sessoes. Eles nao precisam do daemon, Docker ou acesso a internet; o caso `-x` simula tanto a confirmacao quanto a execucao do comando.

O GitHub Actions executa essa mesma suíte em cada `push` e `pull_request`, usando Python 3.10 e 3.13.

## 2. Guia de Uso do Comando `mvn`

### Modo Padrao: Pergunta Direta (Stateless)
Nao grava historico e nao envia contexto anterior. O modelo responde e esquece imediatamente.
```bash
mvn o que e RAG?
mvn explique decoradores em Python em tres frases
```

### Modo com Memoria Continua (`-m`)
Usa a sessao de memoria padrao (`default.json`). O assistente lembra das interacoes anteriores dessa sessao.
```bash
mvn -m "estou desenvolvendo uma API com FastAPI"
mvn -m "adicione um endpoint GET para listar usuarios"
```

### Sessoes Nomeadas (`-s <nome>`)
Permite isolar contextos para diferentes tarefas ou projetos.
```bash
mvn -s docker "gere um Dockerfile multi-stage para Go"
mvn -s docker "adicione um usuario nao-root"
```

### Inspecao e Limpeza de Memoria
```bash
mvn -l              # Exibe o historico da sessao padrao
mvn -l docker       # Exibe o historico da sessao 'docker'
mvn -c              # Limpa o historico da sessao padrao
mvn -c docker       # Limpa o historico da sessao 'docker'
mvn --sessions      # Lista todas as sessoes de memoria existentes
```

### Entrada via Pipe Unix (Stdin)
Envie saidas de comandos ou conteudos de arquivos diretamente para analise do Marvin:
```bash
cat script.py | mvn "analise a complexidade deste codigo"
git diff | mvn "escreva uma mensagem de commit em portugues"
journalctl -n 50 | mvn "identifique possiveis erros nestes logs"
```

### Pesquisa na Web
O Marvin pode pesquisar durante uma conversa quando precisar de dados atuais, como cotacoes, ou de informacoes que nao consegue confirmar apenas com o modelo local. O cliente reconhece alguns tipos de pergunta atual (por exemplo, cotacoes e clima); em outras perguntas, o modelo pode solicitar a ferramenta. A palavra "hoje" ou "agora" sozinha nao força uma busca. Tambem e possivel forcar ou desativar a pesquisa por pergunta. O cliente executa no maximo uma consulta, tenta extrair o conteudo das tres primeiras paginas e mostra os links consultados.

A busca automatica considera o assunto junto com a atualidade; palavras como
"hoje" sozinhas nao iniciam uma pesquisa. Antes de enviar resultados ao modelo,
o cliente verifica se os títulos e resumos têm relação lexical mínima com a
consulta. Para perguntas sobre clima, também reformula a consulta para buscar
temperatura e previsão. Se a cidade ou região não estiver indicada, pergunta a
localidade antes de pesquisar. Resultados de sites que não sejam fontes
meteorológicas reconhecidas são descartados para evitar que páginas virais ou
memes fundamentem uma resposta sobre temperatura. Se não houver resultados
relacionados ou uma fonte meteorológica adequada, o Marvin informa que a busca
foi inconclusiva. Quando uma busca aguarda esclarecimento, o Marvin interpreta a
resposta antes de agir: pode continuar com os dados fornecidos, reavaliar um
novo pedido ou cancelar a busca. Os resultados são enviados ao modelo para
compor uma resposta; eles não são apresentados como substituto da resposta.

O Marvin usa o [SearXNG](https://github.com/searxng/searxng), um metabuscador de codigo aberto, executado localmente via Docker Compose. E necessario ter Docker Engine com o plugin Compose instalado. Nao e necessario criar conta nem configurar chave de API ou pagar por consulta ao SearXNG. Ele encaminha as consultas aos mecanismos externos; esses mecanismos podem limitar ou bloquear requisicoes.

```bash
docker compose up -d
```

O endpoint JSON fica habilitado na configuracao versionada em `searxng/settings.yml`, e o servico escuta somente no loopback (`127.0.0.1:8888`). Para conferir se respondeu:

```bash
curl -fsS 'http://127.0.0.1:8888/search?q=teste&format=json' | head
```

Exemplo:
```bash
mvn qual e a cotacao atual do dolar em reais?
mvn --web-search como configurar o recurso mais recente do Python
mvn --web-search --search-category it --search-time-range month "mudancas recentes na API do FastAPI"
mvn --web-search --search-engines google,bing "documentacao oficial do systemd user service"
mvn --no-web-search "explique o que e uma closure em Python"
```

Opcoes disponiveis: `--web-search` forca a pesquisa; `--no-web-search` a desativa; `--search-engines` escolhe mecanismos separados por virgula; `--search-category` aceita `general`, `it`, `news`, `science`, `files`, `images`, `videos`, `music` ou `map`; `--search-time-range` aceita `day`, `week`, `month` ou `year`. Sem uma escolha explicita de mecanismos, o SearXNG usa os mecanismos configurados e disponiveis. Os filtros de categoria e periodo dependem do suporte dos motores selecionados.

O Marvin deve citar os resultados numerados (`[1]`, `[2]`) e listar os links ao final. A data e hora da consulta indicam quando a busca foi feita; a data de publicacao depende do que os mecanismos retornarem. A extracao de paginas e limitada a HTML publico, nao segue instrucoes encontradas no conteudo e pode falhar em sites que bloqueiam acesso automatizado. Para cotacoes, confirme qual tipo de valor a fonte apresenta (por exemplo, compra, venda ou turismo). A busca requer internet. Para parar o SearXNG, execute `docker compose down`.

O formato JSON precisa estar habilitado no SearXNG para a integracao funcionar; a configuracao fornecida ja faz isso. A [API do SearXNG](https://docs.searxng.org/dev/search_api.html) documenta o parametro `format=json` e observa que cada instancia deve habilitar explicitamente os formatos que aceita.

### Modo Chat Interativo (REPL)
Executar o comando sem argumentos e com terminal interativo inicia uma sessao de conversa continua:
```bash
mvn                 # Chat interativo stateless (contexto mantido apenas durante a sessao)
mvn -m              # Chat interativo persistindo na memoria padrao
mvn -s projeto      # Chat interativo persistindo na sessao 'projeto'
```
Para sair do chat interativo, digite `sair`, `exit` ou pressione `Ctrl+C`. No modo interativo, voce tambem pode usar `/x <pedido>` para disparar acoes operacionais.

No chat interativo, `/web <pergunta>` força a busca e `/no-web <pergunta>` responde sem pesquisar naquela troca.

### Modo Operacional: Agente Iterativo com Seguranca (`-x`)
Permite solicitar investigacoes e tarefas no sistema operacional em linguagem natural. O Marvin opera em um ciclo iterativo do tipo **ReAct (Reason + Act + Observe)**, propondo comandos, analisando a saida real do terminal e sintetizando um relatorio final:

```bash
mvn -x verifique o espaco livre no /home e qual o maior diretorio
mvn -x investigue a particao com maior ocupacao e detalhe o consumo
mvn -x crie a pasta /tmp/projeto e clone o repositorio https://...
```

Como Funciona o Ciclo de Execucao:
1. **Inspecao e Acao**: O Marvin analisa o pedido e propoe o comando Bash exato, descrevendo a finalidade.
2. **Confirmacao Humana**: O terminal pausa e solicita sua autorizacao previa (`[s/N]`).
3. **Filtro de Seguranca**: Se confirmado, o comando e inspecionado contra o Safety Filter (bloqueia comandos destrutivos como `rm -rf /`, `mkfs`, `dd`, `reboot`, etc.).
4. **Execucao e Feedback**: O script executa o comando aprovado, mostra um resumo da saida no terminal e entrega o retorno para o Marvin como contexto. Use `--verbose` para exibir a saida completa:
   ```bash
   mvn -x --verbose investigue os logs do servico
   ```
5. **Decisao Multi-Passo**: Com base na saida real do sistema, o Marvin decide autonomamente:
   - Se precisa de outro comando para aprofundar ou corrigir a busca (ate 4 comandos por ciclo);
   - Ou se ja concluiu o pedido e pode entregar o **Relatorio Final**. Uma falha explicada ou um proximo passo sugerido nao conta como tarefa concluida; o Marvin verifica essa condicao antes de encerrar.
6. **Pedido de Orientacao**: Se ainda precisar agir depois de 4 comandos, o Marvin resume o que tentou e aguarda sua orientacao no mesmo processo. Sua resposta inicia outro ciclo de ate 4 comandos; digite `encerrar` para parar. Cada comando continua exigindo confirmacao.

### Personalidade e Regras de Comportamento
O Marvin carrega suas instrucoes mestras diretamente do arquivo:
`~/marvin/system_prompt.md` (ou copiado de `system_prompt.example.md`)

Voce pode visualizar ou editar as regras a qualquer momento:
```bash
mvn --show-prompt   # Exibe a personalidade e regras ativas
mvn --prompt        # Abre o arquivo no seu editor padrao (micro, nvim, nano)
```
Qualquer alteracao salva no arquivo entra em vigor imediatamente na proxima chamada, sem necessidade de reiniciar o servico.

### Verificacao de Conectividade
```bash
mvn --status
```

---

## 3. Gerenciamento do Servico Systemd

O modelo roda como um servico de usuario no Linux. Comandos para administracao:

```bash
# Verificar status da execucao
systemctl --user status marvin

# Parar o servico
systemctl --user stop marvin

# Iniciar o servico
systemctl --user start marvin

# Reiniciar o servico
systemctl --user restart marvin

# Visualizar logs em tempo real
journalctl --user -u marvin -f
```

---

## 4. Estrutura de Arquivos e Diretorios Utilizados

O Marvin organiza seus arquivos de acordo com as especificacoes padrao do ecossistema Linux/XDG. Abaixo esta o mapa completo dos diretorios utilizados e as razoes arquiteturais:

### Mapa de Diretorios

| Caminho | Finalidade | Arquivos Contidos | Motivo e Justificativa |
| :--- | :--- | :--- | :--- |
| `~/marvin/` | Raiz do Repositorio | `mvn`, `marvin.service`, `system_prompt.example.md`, `system_prompt.md`, `README.md`, `*.llamafile` | Diretorio principal do projeto. O binario pesado de IA e o codigo-fonte residem aqui. |
| `~/.local/bin/` | Atalho de Execucao | Symlink: `mvn` -> `~/marvin/mvn` | Diretorio padrao do `$PATH` do usuario. Permite executar o comando `mvn` de qualquer pasta do terminal. |
| `~/.config/systemd/user/` | Servicos do Usuario | Symlink: `marvin.service` -> `~/marvin/marvin.service` | Caminho obrigatorio onde o `systemd --user` busca e gerencia servicos em segundo plano. |
| `~/.local/share/marvin/` | Dados e Estado (Memoria) | `sessions/default.json`, `sessions/*.json` | Especificacao XDG para dados dinâmicos gerados em tempo de execucao. Evita poluir o repositorio com historicos de chat. |
| `~/.ape-1.10/` | Cache de Runtime | Binarios ELF temporarios do Cosmopolitan APE | Cache interno criado pelo runtime do Llamafile para carregar a inferencia em CPU/GPU. |

### Arquivos de Personalidade do Modelo

* `~/marvin/system_prompt.example.md`: Modelo generico versionado no Git contendo instrucoes base.
* `~/marvin/system_prompt.md`: Seu arquivo pessoal com as regras e personalidade do assistente. Fica ignorado pelo `.gitignore` para nao ser publicado no GitHub.

Ao clonar o repositorio em uma nova maquina, configure sua personalidade copiando o modelo:
```bash
cp system_prompt.example.md system_prompt.md
```


---

## 5. Variaveis de Ambiente Opcionais

Caso deseje alterar o host ou porta padrao, defina no seu `.zshrc` ou `.bashrc`:

```bash
export MARVIN_HOST="127.0.0.1"
export MARVIN_PORT="8088"
export MARVIN_SEARCH_LANGUAGE="pt-BR"
# Opcionais: restringir os mecanismos e as categorias usadas por padrao
export MARVIN_SEARCH_ENGINES="google,bing"
export MARVIN_SEARCH_CATEGORIES="general"
```

---

## 6. Integracao com Shell Zsh (noglob)

Por padrao, shells como o Zsh interpretam `?` e `*` como busca de arquivos no disco (globbing). Para permitir perguntas com ponto de interrogacao diretamente sem precisar colocar aspas, foi configurado o alias `noglob` no seu `~/.zshrc`:

```bash
alias mvn="noglob ~/.local/bin/mvn"
```

Isso permite executar diretamente:
```bash
mvn o que e RAG?
mvn diferenca entre * e + em regex?
```

Nota: Simbolos de controle de fluxo de comandos do proprio shell (como `;`, `|`, `&`, `>`, `<`) continuam exigindo o uso de aspas ou a utilizacao do modo interativo (`mvn`).

