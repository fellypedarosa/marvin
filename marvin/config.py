"""Configuracao de conexao, caminhos e limites existentes do cliente."""

import os
from pathlib import Path

MARVIN_HOST = os.environ.get("MARVIN_HOST", "127.0.0.1")

MARVIN_PORT = os.environ.get("MARVIN_PORT", "8088")

API_URL = f"http://{MARVIN_HOST}:{MARVIN_PORT}/v1/chat/completions"

HEALTH_URL = f"http://{MARVIN_HOST}:{MARVIN_PORT}/health"

MARVIN_SEARCH_URL = os.environ.get("MARVIN_SEARCH_URL", "http://127.0.0.1:8888/search")

MAX_SESSION_MESSAGES = 30

MAX_SESSION_CHARACTERS = 12000

MAX_WEB_RESULTS = 5

MAX_FETCHED_PAGES = 3

MAX_ACTION_CONTEXT_CHARACTERS = 14000

MAX_ACTION_SEARCH_CONTEXT_CHARACTERS = 4500

MAX_ACTION_HISTORY_MESSAGES = 4

SESSIONS_DIR = Path.home() / ".local" / "share" / "marvin" / "sessions"

REPO_DIR = Path(__file__).resolve().parent.parent

PROMPT_FILE = REPO_DIR / "system_prompt.md"

PROMPT_EXAMPLE = REPO_DIR / "system_prompt.example.md"

DEFAULT_SYSTEM_PROMPT = """Voce e o Marvin, assistente pessoal de desenvolvimento operando no terminal Linux.

Regras de Comportamento Obrigatorias:
- Idioma: Responda sempre em portugues brasileiro, a menos que solicitado expressamente outro idioma.
- Restricao Estrita: DE MANEIRA NENHUMA utilize emojis em suas respostas.
- Duvidas e Ambiguidade: Se um pedido for ambiguo ou faltarem informacoes criticas, faca perguntas para esclarecer antes de assumir premissas.
- Estilo: Seja tecnico, direto e conciso. Va direto ao ponto e evite preambulos ou enrolacoes.
- Ambiente: Priorize comandos, ferramentas e praticas modernas compativeis com Linux."""

MAX_ACTION_TURNS = 4

MAX_ACTION_WEB_SEARCHES_PER_STEP = 3

MAX_ACTION_PLAN_REPAIRS = 3

ACTION_PLAN_BLOCKED = "<<<ACTION_PLAN_BLOCKED>>>"

TASK_PLAN_MARKER = "<<<TASK_PLAN>>>"
