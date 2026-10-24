"""Respostas do modelo em streaming e conversas com pesquisa."""

import sys
import json
from urllib import request, error
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from . import api, config, search, terminal


def _resume_after_search_clarification(
    messages: list,
    system_prompt: str,
    clarification: str,
    reply: str,
    search_options: dict = None,
) -> str:
    updated_messages = list(messages)
    updated_messages.extend([
        {"role": "assistant", "content": clarification},
        {"role": "user", "content": reply},
    ])
    return stream_chat(
        updated_messages,
        system_prompt,
        allow_web_search=True,
        search_options=search_options,
    )


def _respond_after_search_cancellation(
    messages: list,
    system_prompt: str,
    reply: str,
) -> str:
    if search.CASUAL_CANCELLATION.search(reply):
        answer = "Ah, entendi, hehe. Levei a frase ao pé da letra."
        terminal.print_markdown(answer)
        return answer

    updated_messages = list(messages)
    original_index = next(
        (index for index in range(len(updated_messages) - 1, -1, -1)
         if updated_messages[index].get("role") == "user"),
        None,
    )
    original_question = str(updated_messages[original_index].get("content", "")) if original_index is not None else ""
    combined_context = (
        "O usuário comentou: " + repr(original_question) + "\n"
        "Depois de ser perguntado um detalhe para uma pesquisa, respondeu: " + repr(reply) + ".\n"
        "A pesquisa foi cancelada. Responda ao comentário ou ao novo pedido explícito do usuário."
    )
    if original_index is None:
        updated_messages.append({"role": "user", "content": combined_context})
    else:
        updated_messages[original_index] = {"role": "user", "content": combined_context}
    cancellation_instructions = (
        (system_prompt + "\n\n") if system_prompt else ""
    ) + (
        "O usuário cancelou uma pesquisa pendente. A mensagem mais recente contém o comentário original "
        "e a resposta ao esclarecimento; a pergunta de esclarecimento anterior foi removida do contexto. "
        "Responda ao que o usuário realmente quis dizer, de forma natural e breve quando for conversa casual. "
        "Só atenda a um novo pedido se ele estiver explícito. Não retome a pergunta pesquisável original, "
        "não invente tarefas, código ou comandos que não foram pedidos e não use ferramentas nem pesquisa."
    )
    return stream_chat(updated_messages, cancellation_instructions, allow_web_search=False)


def answer_with_web_search(
    messages: list,
    system_prompt: str,
    query: str,
    options: dict = None,
    clarification: str = None,
) -> str:
    original_question = next(
        (str(msg.get("content", "")) for msg in reversed(messages) if msg.get("role") == "user"),
        query,
    )
    explicit_query = search._explicit_search_query(original_question)
    tool_clarification = clarification
    prepared_query, clarification_reason = search._prepare_web_query(original_question)
    weather_needs_location = clarification_reason == "weather_location_required"
    pending_clarification = tool_clarification
    if weather_needs_location:
        pending_clarification = "Para verificar o clima, preciso saber sua cidade ou região."

    if pending_clarification:
        reply = search._ask_search_clarification(
            pending_clarification + " Responda com o dado solicitado, reformule o pedido ou diga que não quer pesquisar."
        )
        if reply is None:
            return pending_clarification
        intent, location = search._classify_search_clarification(reply, expects_location=weather_needs_location)
        if intent == "cancel":
            return _respond_after_search_cancellation(
                messages,
                system_prompt,
                reply,
            )
        if intent == "respond":
            return _respond_after_search_cancellation(
                messages,
                system_prompt,
                reply,
            )
        if intent == "unclear":
            answer = "Não entendi o dado necessário para pesquisar. Não fiz a busca."
            terminal.print_markdown(answer)
            return answer
        if not weather_needs_location or intent == "replan":
            return _resume_after_search_clarification(
                messages,
                system_prompt,
                pending_clarification,
                reply,
                options,
            )
        original_question = f"{original_question} em {location}"
        prepared_query, _ = search._prepare_web_query(original_question)
    if search.WEATHER_QUERY.search(original_question):
        query = prepared_query
    elif search.CURRENT_FACT_QUERY.search(original_question):
        query = prepared_query
    elif explicit_query:
        query = explicit_query
    elif not query.strip():
        query = prepared_query

    terminal.terminal_console().print(Text("Pesquisando na web...", style="bold cyan"))
    try:
        search_data = search.search_web(query, options)
        results = search_data["results"]
        if search.WEATHER_QUERY.search(original_question):
            results = search._weather_results_are_relevant(results)
        results = search._search_results_are_relevant(query, results)
        if not results:
            if search.WEATHER_QUERY.search(original_question):
                explanation = "uma fonte meteorológica confiável com dados de temperatura para essa localidade"
                caution = "Não vou inferir a temperatura a partir de resultados genéricos."
            else:
                explanation = "resultados suficientemente relacionados à pergunta"
                caution = "Não vou formular uma resposta factual com base em fontes fora do assunto."
            answer = f"\n[Pesquisa inconclusiva]: não encontrei {explanation}. {caution}"
            terminal.print_markdown(answer)
            return answer
        search.enrich_web_results(results)
        tool_result = search.format_web_context(query, results, search_data.get("unresponsive_engines"))
    except RuntimeError as err:
        answer = "\n[Pesquisa na web indisponível]: " + str(err)
        terminal.print_markdown(answer)
        return answer

    tool_messages = list(messages)
    tool_messages.append({
        "role": "assistant",
        "content": search.WEB_SEARCH_MARKER + json.dumps({"query": query}, ensure_ascii=False),
    })
    tool_messages.append({"role": "user", "content": "Resultado da ferramenta de pesquisa:\n" + tool_result})
    answer = stream_chat(tool_messages, system_prompt, allow_web_search=False)
    if results:
        refs = []
        for index, item in enumerate(results, 1):
            date = f" (publicado: {item['published_date']})" if item["published_date"] else ""
            refs.append(f"[{index}] {item['title']}{date} — {item['url']}")
        references = "\n".join(refs)
        answer += "\n\nFontes consultadas:\n" + references
        terminal.print_markdown(references, title="Fontes consultadas:")
    return answer


def stream_chat(messages, system_prompt: str = None, allow_web_search: bool = True, search_options: dict = None, render_output: bool = True) -> str:
    if allow_web_search:
        latest_user_message = next(
            (msg.get("content", "") for msg in reversed(messages) if msg.get("role") == "user"),
            "",
        )
        explicit_query = search._explicit_search_query(latest_user_message)
        if search.EXPLICIT_SEARCH_COMMAND.match(latest_user_message) and search.SEARCH_CANCELLATION.search(latest_user_message):
            return stream_chat(messages, system_prompt, allow_web_search=False, search_options=search_options,
                               render_output=render_output)
        if explicit_query or search.WEATHER_QUERY.search(latest_user_message) or search.CURRENT_FACT_QUERY.search(latest_user_message):
            search_query = latest_user_message.split("\n\nContexto / Entrada adicional:", 1)[0].strip()[:500]
            return answer_with_web_search(messages, system_prompt, search_query, search_options)

    system_instructions = []
    if system_prompt:
        system_instructions.append(system_prompt)
    if allow_web_search:
        system_instructions.append(search.WEB_SEARCH_SYSTEM_PROMPT)
    full_messages = []
    if system_instructions:
        full_messages.append({"role": "system", "content": "\n\n".join(system_instructions)})
    full_messages.extend(messages)

    payload = {
        "messages": full_messages,
        "stream": True,
        "temperature": 0.6,
    }

    data_bytes = json.dumps(payload).encode("utf-8")
    req = request.Request(
        config.API_URL,
        data=data_bytes,
        headers={"Content-Type": "application/json"}
    )

    complete_reply = []
    undecided = ""
    search_request = None
    searching = False
    console = terminal.terminal_console() if render_output else None
    interactive_render = bool(
        console
        and console.is_terminal
        and console.is_interactive
        and not console.is_dumb_terminal
    )
    status = console.status("Marvin está elaborando...", spinner="dots") if interactive_render else None
    status_started = False
    live = None
    live_started = False

    def stop_status():
        nonlocal status_started
        if status_started:
            status.stop()
            status_started = False

    def stop_live():
        nonlocal live_started
        if live_started:
            live.stop()
            live_started = False

    def live_preview(content: str) -> str:
        """Mantem a pre-visualizacao no fim da resposta sem ocupar o scrollback."""
        max_lines = max(8, console.size.height - 5)
        lines = content.splitlines()
        if len(lines) > max_lines:
            lines = ["… resposta em andamento …"] + lines[-(max_lines - 1):]
        preview = "\n".join(lines)
        max_chars = max(1200, console.size.width * max_lines)
        if len(preview) > max_chars:
            preview = "… resposta em andamento …\n" + preview[-max_chars:]
        return preview

    if interactive_render:
        status.start()
        status_started = True

    def emit(text: str):
        nonlocal live, live_started
        if text:
            complete_reply.append(text)
            if not render_output:
                return
            if not interactive_render:
                return
            stop_status()
            if live is None:
                live = Live(
                    Markdown(""),
                    console=console,
                    refresh_per_second=4,
                    transient=True,
                    vertical_overflow="ellipsis",
                    redirect_stdout=False,
                    redirect_stderr=False,
                )
                live.start(refresh=True)
                live_started = True
            live.update(Markdown(live_preview("".join(complete_reply))))

    def close_progress(show_partial: bool = False):
        stop_status()
        had_live = live_started
        stop_live()
        if show_partial and render_output and complete_reply:
            # Live previews sao transientes; imprime a parte recebida se a conexao falhar.
            if had_live or not interactive_render:
                terminal.print_markdown("".join(complete_reply))

    try:
        with request.urlopen(req, timeout=120) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    chunk_str = line[6:].strip()
                    if chunk_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(chunk_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            if searching:
                                search_request = (search_request or "") + token
                            elif allow_web_search:
                                undecided += token
                                candidate = undecided.lstrip()
                                if not candidate or search._web_search_marker_prefix(candidate):
                                    continue
                                marker_match = search.WEB_SEARCH_MARKER_PATTERN.match(candidate)
                                if marker_match:
                                    searching = True
                                    search_request = candidate[marker_match.end():]
                                else:
                                    emit(undecided)
                                    undecided = ""
                                    allow_web_search = False
                            else:
                                emit(token)
                    except json.JSONDecodeError:
                        continue

            if not searching and undecided:
                emit(undecided)
            if searching:
                close_progress()
                raw_request = search.WEB_SEARCH_MARKER + (search_request or "")
                request_data, _repaired_response, parse_error = search._repair_web_search_request(full_messages, raw_request)
                if request_data is None:
                    answer = (
                        "\n[Pesquisa na web não realizada]: o modelo gerou um pedido em formato inválido "
                        "mesmo após uma tentativa de correção. Tente reformular a consulta."
                    )
                    if render_output:
                        terminal.print_markdown(answer)
                    complete_reply = [answer]
                    return "".join(complete_reply)
                search_query = str(request_data.get("query", "")).strip()
                search_clarification = str(request_data.get("clarification", "")).strip()
                answer = answer_with_web_search(
                    messages,
                    system_prompt,
                    search_query,
                    {
                        **{key: request_data.get(key) for key in ("categories", "time_range", "engines", "language")},
                        **{key: value for key, value in (search_options or {}).items() if value is not None},
                    },
                    clarification=search_clarification or None,
                )
                complete_reply = [answer]
            else:
                close_progress()
                if render_output and complete_reply:
                    terminal.print_markdown("".join(complete_reply))
    except error.HTTPError as e:
        close_progress(show_partial=True)
        api.report_api_http_error(e)
        sys.exit(1)
    except error.URLError as e:
        close_progress(show_partial=True)
        sys.stderr.write(f"\n[Erro de conexao]: Nao foi possivel comunicar com o Marvin ({e.reason}).\n")
        sys.stderr.write("Verifique se o servico esta ativo executando: systemctl --user status marvin\n")
        sys.exit(1)
    except Exception as e:
        close_progress(show_partial=True)
        sys.stderr.write(f"\n[Erro inesperado]: {e}\n")
        sys.exit(1)

    return "".join(complete_reply)
