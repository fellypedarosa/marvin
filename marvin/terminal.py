"""Renderizacao e resumos de saida no terminal."""

import sys
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text


def terminal_console() -> Console:
    """Cria o console por chamada para respeitar redirecionamentos de stdout."""
    return Console(file=sys.stdout, highlight=False, emoji=False)


def print_markdown(content: str, title: str = None):
    console = terminal_console()
    if title:
        console.print(Text(title, style="bold cyan"))
    console.print(Markdown(content))


def summarize_command_output(stdout: str, stderr: str, max_lines: int = 12, max_chars: int = 1600) -> str:
    """Resume a saida visivel sem reduzir o conteudo enviado ao modelo."""
    sections = []
    if stdout.strip():
        sections.append(stdout.strip())
    if stderr.strip():
        sections.append("[stderr]\n" + stderr.strip())
    combined = "\n".join(sections).strip()
    if not combined:
        return "(sem saida)"

    lines = combined.splitlines()
    if len(lines) > max_lines:
        head_count = max_lines - 4
        tail_count = 3
        omitted = len(lines) - head_count - tail_count
        lines = lines[:head_count] + [f"... ({omitted} linhas omitidas) ..."] + lines[-tail_count:]
    summary = "\n".join(lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "\n... (texto omitido)"
    return summary
