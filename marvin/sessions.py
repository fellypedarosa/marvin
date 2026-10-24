"""Persistencia e limites do historico de conversas."""

import sys
import json
from pathlib import Path

from . import config


def get_session_file(session_name: str) -> Path:
    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    sanitized = "".join(c for c in session_name if c.isalnum() or c in ("-", "_")).strip()
    if not sanitized:
        sanitized = "default"
    return config.SESSIONS_DIR / f"{sanitized}.json"


def load_session(session_name: str):
    s_file = get_session_file(session_name)
    if s_file.exists():
        try:
            with open(s_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_session(session_name: str, messages):
    s_file = get_session_file(session_name)
    try:
        with open(s_file, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
    except Exception as err:
        sys.stderr.write(f"[Erro ao salvar sessao]: {err}\n")


def trim_history(messages: list) -> list:
    """Keep a bounded number of recent chat messages to limit prompt growth."""
    recent = messages[-config.MAX_SESSION_MESSAGES:]
    if recent and recent[0].get("role") == "assistant":
        recent = recent[1:]
    while len(recent) > 1 and sum(len(str(item.get("content", ""))) for item in recent) > config.MAX_SESSION_CHARACTERS:
        if len(recent) >= 2 and recent[0].get("role") == "user" and recent[1].get("role") == "assistant":
            del recent[:2]
        else:
            del recent[0]
    if recent and len(str(recent[-1].get("content", ""))) > config.MAX_SESSION_CHARACTERS:
        recent[-1]["content"] = str(recent[-1].get("content", ""))[:config.MAX_SESSION_CHARACTERS]
    return recent


def clear_session(session_name: str):
    s_file = get_session_file(session_name)
    if s_file.exists():
        s_file.unlink()
        print(f"[Memoria limpa]: Sessao '{session_name}' foi apagada.")
    else:
        print(f"[Info]: Nenhuma memoria encontrada para a sessao '{session_name}'.")


def show_history(session_name: str):
    messages = load_session(session_name)
    if not messages:
        print(f"[Info]: A sessao '{session_name}' esta vazia.")
        return
    print(f"=== Historico da Sessao: {session_name} ===")
    for msg in messages:
        role = msg.get("role", "desconhecido").upper()
        content = msg.get("content", "")
        print(f"\n[{role}]:\n{content}")
    print("\n" + "=" * 40)


def list_sessions():
    if not config.SESSIONS_DIR.exists():
        print("[Info]: Nenhuma sessao de memoria criada.")
        return
    files = list(config.SESSIONS_DIR.glob("*.json"))
    if not files:
        print("[Info]: Nenhuma sessao de memoria criada.")
        return
    print("=== Sessoes de Memoria ===")
    for p in files:
        name = p.stem
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                count = len(data)
        except Exception:
            count = 0
        print(f"- {name} ({count} mensagens)")
