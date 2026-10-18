# Marvin: Assistente Pessoal Local CLI

Marvin e um assistente pessoal local executado diretamente no terminal via comando `mvn`. Ele opera sobre o modelo `Qwen3.5-2B-Q8_0.llamafile`, rodando em segundo plano como um servico de usuario do systemd, sem interface grafica, sem navegadores e com desempenho maximo sem limitacoes artificiais de CPU ou memoria.

---

## 1. Arquitetura

O sistema e composto por tres componentes principais:

1. **Backend Daemon (`marvin.service`)**:
   - Gerenciado via `systemd --user`.
   - Executa `~/marvin/Qwen3.5-2B-Q8_0.llamafile` em modo `--server`.
   - Escuta exclusivamente em `127.0.0.1:8088`.
   - Utiliza buffer de contexto de 8192 tokens e dynamic batching (`--cont-batching`).
   - Inicia automaticamente no boot do sistema operacional via `loginctl enable-linger`.

2. **Cliente CLI (`mvn`)**:
   - Localizado em `~/.local/bin/mvn` (link simbolico para `~/marvin/mvn`).
   - Implementado em Python 3 usando apenas a biblioteca padrao do sistema.
   - Comunica-se com a API local (`/v1/chat/completions`) utilizando Server-Sent Events (SSE) para exibir respostas em streaming instantaneo no terminal.
   - Suporta modo stateless, modo com memoria, sessoes nomeadas, pipes Unix e chat interativo.

3. **Armazenamento de Memoria**:
   - Armazenado em `~/.local/share/marvin/sessions/` em formato JSON legivel.
   - Por padrao, o Marvin e 100% stateless (nao grava nem envia historico). A memoria so e lida e persistida quando explicitamente solicitado pelo usuario (`-m` ou `-s`).

---

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

### Modo Chat Interativo (REPL)
Executar o comando sem argumentos e com terminal interativo inicia uma sessao de conversa continua:
```bash
mvn                 # Chat interativo stateless (contexto mantido apenas durante a sessao)
mvn -m              # Chat interativo persistindo na memoria padrao
mvn -s projeto      # Chat interativo persistindo na sessao 'projeto'
```
Para sair do chat interativo, digite `sair`, `exit` ou pressione `Ctrl+C`.

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

