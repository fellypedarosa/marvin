#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(dirname -- "$SCRIPT_DIR")
cd "$PROJECT_DIR"

model=""
for candidate in ./*.llamafile; do
    [ -f "$candidate" ] || continue

    if [ -n "$model" ]; then
        printf '%s\n' "Erro: mais de um arquivo .llamafile encontrado na raiz de $(pwd)." >&2
        printf '%s\n' "Mantenha apenas o modelo que deseja iniciar e tente novamente." >&2
        exit 1
    fi

    model=$candidate
done

if [ -z "$model" ]; then
    printf '%s\n' "Erro: nenhum arquivo .llamafile encontrado na raiz de $(pwd)." >&2
    printf '%s\n' "Adicione um modelo .llamafile ao diretório do Marvin e tente novamente." >&2
    exit 1
fi

if [ ! -x "$model" ]; then
    chmod u+x "$model"
fi

exec "$model" --server --host 127.0.0.1 --port 8088 -c 8192 --cont-batching
