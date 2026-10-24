"""Filtro de comandos do modo operacional."""

import re


FORBIDDEN_PATTERNS = [
    r"\brm\s+-[rRfF]*\s+/(?:\s|$)",             # rm -rf /
    r"\brm\s+-[rRfF]*\s+/\*",                   # rm -rf /*
    r"\brm\s+-[rRfF]*\s+~(?:\s|/|$)",           # rm -rf ~
    r"\brm\s+-[rRfF]*\s+\$HOME(?:\s|/|$)",      # rm -rf $HOME
    r"\brm\s+-[rRfF]*\s+\*(?:\s|$)",            # rm -rf *
    r"\bmkfs(?:\.[a-z0-9]+)?\b",                # formatacao de disco
    r"\bfdisk\b",                               # particionamento
    r"\bparted\b",                              # particionamento
    r"\bwipefs\b",                              # limpeza de disco
    r"\bdd\s+if=",                              # gravacao direta dd
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", # fork bomb
    r"\bchmod\s+-[rRfF]*\s+777\s+/",            # chmod 777 na raiz
    r"\bchown\s+-[rRfF]*\s+.*\s+/",             # chown na raiz
    r">\s*/dev/sd[a-z]",                        # sobrescrita de disco
    r">\s*/dev/nvme[0-9]",                      # sobrescrita de disco
    r"\b(shutdown|reboot|poweroff|init\s+[06])\b", # desligamento/reinicio
    r"\bkillall\s+-9\s+(systemd|init|Xorg|wayland|dbus-daemon)\b", # matar processos vitais
]


def is_command_forbidden(cmd: str) -> tuple[bool, str]:
    cmd_clean = cmd.strip()
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, cmd_clean, re.IGNORECASE):
            return True, pattern
    return False, ""
