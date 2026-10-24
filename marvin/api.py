"""Comunicacao HTTP com o modelo local e tratamento de erros."""

import sys
import re
import json
from urllib import request, error

from . import config


def check_status():
    req = request.Request(f"http://{config.MARVIN_HOST}:{config.MARVIN_PORT}/v1/models", method="GET")
    try:
        with request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                print(f"[Status]: Marvin online em http://{config.MARVIN_HOST}:{config.MARVIN_PORT}")
                return True
    except Exception as err:
        print(f"[Status]: Offline ou inacessivel ({err})")
        print("Sugestao: inicie com 'systemctl --user start marvin'")
        return False


def report_api_http_error(err: error.HTTPError):
    try:
        body = err.read(2000).decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
            error_payload = payload.get("error") if isinstance(payload, dict) else None
            detail = error_payload.get("message", body) if isinstance(error_payload, dict) else body
        except json.JSONDecodeError:
            detail = body
        detail = re.sub(r"\s+", " ", str(detail)).strip()[:500]
    except Exception:
        detail = ""
    sys.stderr.write(f"\n[Erro do modelo]: A API local respondeu HTTP {err.code} ({err.reason}).\n")
    if detail:
        sys.stderr.write(f"Detalhe: {detail}\n")


def query_messages_raw(messages: list) -> str:
    payload = {
        "messages": messages,
        "stream": False,
        "temperature": 0.2,
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = request.Request(
        config.API_URL,
        data=data_bytes,
        headers={"Content-Type": "application/json"}
    )
    try:
        with request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except error.HTTPError as e:
        report_api_http_error(e)
        sys.exit(1)
    except error.URLError as e:
        sys.stderr.write(f"\n[Erro de conexao]: Nao foi possivel comunicar com o Marvin ({e.reason}).\n")
        sys.stderr.write("Verifique se o servico esta ativo executando: systemctl --user status marvin\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"\n[Erro inesperado]: {e}\n")
        sys.exit(1)
