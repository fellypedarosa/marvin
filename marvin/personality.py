"""Leitura, criacao e edicao da personalidade local."""

import sys
import os
import subprocess
from pathlib import Path

from . import config


def init_prompt_file() -> Path:
    if not config.PROMPT_FILE.exists():
        if config.PROMPT_EXAMPLE.exists():
            try:
                import shutil
                shutil.copy(config.PROMPT_EXAMPLE, config.PROMPT_FILE)
            except Exception:
                pass
        if not config.PROMPT_FILE.exists():
            try:
                with open(config.PROMPT_FILE, "w", encoding="utf-8") as f:
                    f.write(config.DEFAULT_SYSTEM_PROMPT.strip() + "\n")
            except Exception as err:
                sys.stderr.write(f"[Erro ao criar arquivo de personalidade]: {err}\n")
    return config.PROMPT_FILE


def get_system_prompt() -> str:
    init_prompt_file()
    try:
        with open(config.PROMPT_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                return content
    except Exception as err:
        sys.stderr.write(f"[Aviso ao ler personalidade]: {err}. Usando padrao interno.\n")
    return config.DEFAULT_SYSTEM_PROMPT.strip()


def edit_personality():
    p_file = init_prompt_file()
    editor = os.environ.get("EDITOR")
    if not editor:
        for candidate in ["micro", "nvim", "nano", "vim", "vi"]:
            if os.path.exists(f"/usr/bin/{candidate}") or os.path.exists(f"/bin/{candidate}"):
                editor = candidate
                break
    if not editor:
        editor = "vi"

    print(f"[Info]: Abrindo personalidade em {p_file} usando '{editor}'...")
    try:
        subprocess.run([editor, str(p_file)])
    except Exception as err:
        sys.stderr.write(f"[Erro ao abrir editor]: {err}\n")
        print(f"Voce pode editar diretamente o arquivo em: {p_file}")


def show_personality():
    prompt = get_system_prompt()
    print(f"=== Personalidade do Marvin ({config.PROMPT_FILE}) ===\n")
    print(prompt)
    print("\n" + "=" * 50)
