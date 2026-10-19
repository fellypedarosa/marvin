#!/bin/sh

set -eu

REPOSITORY_URL="https://github.com/fellypedarosa/marvin"
MODEL_URL="https://huggingface.co/mozilla-ai/llamafile_0.10/resolve/main/Qwen3.5-2B-Q8_0.llamafile"
MODEL_NAME="Qwen3.5-2B-Q8_0.llamafile"

say_error() {
    printf 'Erro: %s\n' "$1" >&2
    exit 1
}

is_project_root() {
    [ -f "$1/compose.yaml" ] && [ -f "$1/mvn" ] && [ -f "$1/marvin.service" ]
}

confirm() {
    printf '%s [s/N]: ' "$1"
    read -r answer || answer=n
    case "$answer" in
        s|S|sim|SIM|Sim) return 0 ;;
        *) return 1 ;;
    esac
}

install_arch_packages() {
    shift
    [ "$#" -gt 0 ] || return 0
    if ! confirm "Instalar dependências do sistema via sudo pacman: $*?"; then
        say_error 'instalação de dependências recusada; instale-as e execute o script novamente.'
    fi
    command -v sudo >/dev/null 2>&1 || say_error 'sudo não foi encontrado; instale as dependências manualmente.'
    sudo pacman -S --needed "$@"
}

ensure_bootstrap_tools() {
    missing=""
    command -v git >/dev/null 2>&1 || missing="$missing git"
    command -v curl >/dev/null 2>&1 || missing="$missing curl"
    [ -z "$missing" ] && return 0

    if [ -r /etc/os-release ]; then
        . /etc/os-release
    else
        ID=unknown
    fi
    case "${ID:-unknown}" in
        arch|endeavouros|manjaro)
            # shellcheck disable=SC2086
            install_arch_packages unused $missing
            ;;
        *)
            say_error "faltam:$missing. Instale esses pacotes com o gerenciador da sua distribuição e execute o script novamente."
            ;;
    esac
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd) || SCRIPT_DIR=""
ensure_bootstrap_tools
if [ -n "$SCRIPT_DIR" ] && is_project_root "$(dirname -- "$SCRIPT_DIR")"; then
    PROJECT_DIR=$(dirname -- "$SCRIPT_DIR")
else
    [ -n "${HOME:-}" ] || say_error 'a variável HOME não está definida.'
    PROJECT_DIR="$HOME/marvin"

    if [ -e "$PROJECT_DIR" ]; then
        [ -d "$PROJECT_DIR/.git" ] || say_error "$PROJECT_DIR existe, mas não parece ser um clone Git do Marvin."
        REMOTE_URL=$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || true)
        case "$REMOTE_URL" in
            https://github.com/fellypedarosa/marvin|https://github.com/fellypedarosa/marvin.git|git@github.com:fellypedarosa/marvin|git@github.com:fellypedarosa/marvin.git) ;;
            *) say_error "o repositório existente em $PROJECT_DIR não corresponde a $REPOSITORY_URL." ;;
        esac
    else
        command -v git >/dev/null 2>&1 || say_error 'Git é necessário para clonar o repositório. Instale-o e execute este script novamente.'
        printf 'Clonando o repositório Marvin em %s...\n' "$PROJECT_DIR"
        git clone "$REPOSITORY_URL" "$PROJECT_DIR"
    fi

    exec "$PROJECT_DIR/scripts/marvin.sh" "$@"
fi

missing=""
command -v python3 >/dev/null 2>&1 || missing="$missing python"
python3 -c 'import venv, ensurepip' >/dev/null 2>&1 || missing="$missing python-pip"
command -v curl >/dev/null 2>&1 || missing="$missing curl"
if ! docker compose version >/dev/null 2>&1; then
    missing="$missing docker docker-compose"
fi
if [ -n "$missing" ]; then
    if [ -r /etc/os-release ]; then . /etc/os-release; else ID=unknown; fi
    case "${ID:-unknown}" in
        arch|endeavouros|manjaro)
            # shellcheck disable=SC2086
            install_arch_packages unused $missing
            ;;
        *)
            say_error "faltam dependências:$missing. Este instalador só automatiza a instalação de pacotes no Arch Linux e derivados. Para Docker, siga https://docs.docker.com/engine/install/ e depois execute o instalador novamente."
            ;;
    esac
fi
command -v systemctl >/dev/null 2>&1 || say_error 'este instalador requer Linux com systemd.'
systemctl --user show-environment >/dev/null 2>&1 || say_error 'não consegui acessar o systemd do usuário. Execute o instalador em uma sessão Linux ativa.'

if ! docker info >/dev/null 2>&1; then
    if confirm 'O daemon Docker não está acessível. Deseja iniciá-lo agora com sudo systemctl enable --now docker?'; then
        command -v sudo >/dev/null 2>&1 || say_error 'sudo não foi encontrado; inicie Docker manualmente.'
        sudo systemctl enable --now docker || say_error 'não foi possível iniciar o Docker.'
    else
        say_error 'inicie o daemon Docker e execute este script novamente.'
    fi
    docker info >/dev/null 2>&1 || say_error 'Docker continua inacessível; confira o serviço e as permissões do usuário.'
fi

printf '\nConfigurando o Marvin em %s\n' "$PROJECT_DIR"

if [ ! -f "$PROJECT_DIR/system_prompt.md" ]; then
    cp "$PROJECT_DIR/system_prompt.example.md" "$PROJECT_DIR/system_prompt.md"
    printf 'Criado system_prompt.md a partir do modelo público.\n'
fi

if [ ! -f "$PROJECT_DIR/.env" ]; then
    SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))') || say_error 'não foi possível gerar a chave do SearXNG.'
    (umask 077; printf 'SEARXNG_SECRET=%s\n' "$SECRET" > "$PROJECT_DIR/.env")
    unset SECRET
    printf 'Criado .env com uma chave aleatória para o SearXNG.\n'
fi

if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
    python3 -m venv "$PROJECT_DIR/.venv" || say_error 'não foi possível criar o ambiente Python. Instale o suporte a venv e tente novamente.'
fi
"$PROJECT_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -r "$PROJECT_DIR/requirements.txt"

find_models() {
    MODEL_COUNT=0
    MODEL_PATH=""
    for candidate in "$PROJECT_DIR"/*.llamafile; do
        [ -f "$candidate" ] || continue
        MODEL_COUNT=$((MODEL_COUNT + 1))
        MODEL_PATH=$candidate
    done
}

find_models
if [ "$MODEL_COUNT" -eq 0 ]; then
    printf '\nOutros modelos llamafile estão disponíveis em:\n%s\n' \
        'https://docs.mozilla.ai/llamafile/getting-started/pre-built-llamafiles'
    printf '\nDeseja baixar o modelo Qwen3.5-2B-Q8_0 (~2,9 GB)? [S/n]: '
    read -r DOWNLOAD_ANSWER || DOWNLOAD_ANSWER=n
    case "$DOWNLOAD_ANSWER" in
        n|N|nao|não|NAO|NÃO)
            ;;
        *)
            MODEL_TEMP="$PROJECT_DIR/$MODEL_NAME.part"
            printf '\nAguardando o download do arquivo llamafile...\n'
            if ! curl --fail --location --show-error --output "$MODEL_TEMP" "$MODEL_URL"; then
                rm -f "$MODEL_TEMP"
                say_error 'o download do modelo falhou; execute o instalador novamente para tentar de novo.'
            fi
            [ -s "$MODEL_TEMP" ] || say_error 'o arquivo baixado está vazio.'
            mv "$MODEL_TEMP" "$PROJECT_DIR/$MODEL_NAME"
            chmod u+x "$PROJECT_DIR/$MODEL_NAME"
            ;;
    esac
fi

find_models
while [ "$MODEL_COUNT" -ne 1 ]; do
    if [ "$MODEL_COUNT" -eq 0 ]; then
        printf '\nNenhum arquivo .llamafile foi encontrado.\n'
        printf 'Salve exatamente um arquivo .llamafile em %s e pressione Enter para verificar novamente.\n' "$PROJECT_DIR"
    else
        printf '\nEncontrei mais de um arquivo .llamafile em %s.\n' "$PROJECT_DIR"
        printf 'Mova os modelos que não deseja usar para outro diretório e pressione Enter para verificar novamente.\n'
    fi
    read -r _ || say_error 'a seleção do modelo foi interrompida.'
    find_models
done

if [ ! -x "$MODEL_PATH" ]; then
    chmod u+x "$MODEL_PATH"
fi

VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
CLIENT="$PROJECT_DIR/mvn"
BIN_DIR=${XDG_BIN_HOME:-"$HOME/.local/bin"}
BIN_PATH="$BIN_DIR/mvn"
mkdir -p "$BIN_DIR"

if [ -L "$BIN_PATH" ]; then
    OLD_TARGET=$(readlink -f "$BIN_PATH" 2>/dev/null || true)
    case "$OLD_TARGET" in
        "$CLIENT") rm -f "$BIN_PATH" ;;
        *) say_error "$BIN_PATH já aponta para outro programa; remova ou mova esse atalho antes de continuar." ;;
    esac
elif [ -e "$BIN_PATH" ]; then
    say_error "$BIN_PATH já existe e não será sobrescrito. Mova-o antes de continuar."
fi

python3 - "$BIN_PATH" "$VENV_PYTHON" "$CLIENT" <<'PY'
from pathlib import Path
import shlex
import sys

destination, python, client = map(Path, sys.argv[1:])
script = "#!/bin/sh\nexec " + shlex.quote(str(python)) + " " + shlex.quote(str(client)) + ' "$@"\n'
destination.write_text(script, encoding="utf-8")
destination.chmod(0o755)
PY

SERVICE_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
SERVICE_PATH="$SERVICE_DIR/marvin.service"
mkdir -p "$SERVICE_DIR"
if [ -L "$SERVICE_PATH" ]; then
    OLD_SERVICE=$(readlink -f "$SERVICE_PATH" 2>/dev/null || true)
    [ "$OLD_SERVICE" = "$PROJECT_DIR/marvin.service" ] || say_error "$SERVICE_PATH já aponta para outro serviço; não foi alterado."
elif [ -e "$SERVICE_PATH" ]; then
    say_error "$SERVICE_PATH já existe e não será sobrescrito."
else
    ln -s "$PROJECT_DIR/marvin.service" "$SERVICE_PATH"
fi

printf '\nIniciando o SearXNG...\n'
docker compose -f "$PROJECT_DIR/compose.yaml" --project-directory "$PROJECT_DIR" up -d
systemctl --user daemon-reload
systemctl --user enable --now marvin.service

printf '\nMarvin instalado e iniciado.\n'
printf 'Modelo: %s\n' "$(basename -- "$MODEL_PATH")"
printf 'Comando: %s\n' "$BIN_PATH"
case ":${PATH}:" in
    *":$BIN_DIR:"*) ;;
    *) printf 'Adicione %s ao PATH do seu shell para executar mvn de qualquer diretório.\n' "$BIN_DIR" ;;
esac
