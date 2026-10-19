#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(dirname -- "$SCRIPT_DIR")

if [ ! -d "$PROJECT_DIR/.git" ]; then
    printf '%s\n' "Erro: não encontrei o repositório Marvin em $PROJECT_DIR." >&2
    exit 1
fi

model=""
for candidate in "$PROJECT_DIR"/*.llamafile; do
    [ -f "$candidate" ] || continue

    if [ -n "$model" ]; then
        printf '%s\n' "Erro: há mais de um arquivo .llamafile na raiz do Marvin." >&2
        printf '%s\n' "Deixe somente o modelo que deseja usar e execute este script novamente." >&2
        exit 1
    fi

    model=$candidate
done

if [ -z "$model" ]; then
    printf '%s\n' "Erro: nenhum arquivo .llamafile encontrado em $PROJECT_DIR." >&2
    exit 1
fi

if ! systemctl --user cat marvin.service >/dev/null 2>&1; then
    printf '%s\n' "Erro: o serviço do Marvin ainda não está instalado." >&2
    printf '%s\n' "Execute scripts/marvin.sh para instalar o Marvin primeiro." >&2
    exit 1
fi

if [ ! -x "$model" ]; then
    chmod u+x "$model"
fi

systemctl --user daemon-reload
systemctl --user restart marvin.service

if systemctl --user is-active --quiet marvin.service; then
    printf '%s\n' "Modelo selecionado: $(basename -- "$model")"
    printf '%s\n' "Serviço Marvin reiniciado com sucesso."
else
    printf '%s\n' "O serviço Marvin não iniciou. Consulte: systemctl --user status marvin.service" >&2
    exit 1
fi
