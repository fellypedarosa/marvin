"""Argumentos da linha de comando e conversa interativa."""

import signal
import sys
import argparse

from . import actions, api, chat, personality, sessions


def sigint_handler(signum, frame):
    sys.stdout.write("\n[Interrompido pelo usuario]\n")
    sys.exit(0)


def interactive_repl(session_name: str, use_memory: bool, system_prompt: str):
    print(f"Marvin Chat Interativo (Modo: {'Memoria: ' + session_name if use_memory else 'Stateless'})")
    print("Digite 'sair', 'exit' ou Ctrl+C para encerrar.")
    print("Dica: digite '/x <pedido>' para executar uma acao operacional.\n")

    history = sessions.trim_history(sessions.load_session(session_name)) if use_memory else []

    while True:
        try:
            prompt = input("voce> ").strip()
        except EOFError:
            break

        if not prompt:
            continue
        if prompt.lower() in ("sair", "exit", "quit"):
            break
        if prompt == "/clear":
            if use_memory:
                sessions.clear_session(session_name)
                history = []
            else:
                print("[Info]: Modo stateless nao possui historico gravado.")
            continue
        force_web_search = prompt.startswith("/web ")
        disable_web_search = prompt.startswith("/no-web ")
        if force_web_search or disable_web_search:
            prompt = prompt.split(" ", 1)[1].strip()
            if not prompt:
                print("[Info]: Informe a pergunta depois de /web ou /no-web.")
                continue
        if prompt.startswith("/x ") or prompt.startswith("/exec "):
            action_query = prompt.split(" ", 1)[1].strip()
            actions.handle_action_mode(action_query)
            print()
            continue

        print("marvin> ", end="", flush=True)
        current_conversation = history + [{"role": "user", "content": prompt}]
        if force_web_search:
            answer = chat.answer_with_web_search(current_conversation, system_prompt, prompt)
        else:
            answer = chat.stream_chat(current_conversation, system_prompt, allow_web_search=not disable_web_search)

        if use_memory:
            history.append({"role": "user", "content": prompt})
            history.append({"role": "assistant", "content": answer})
            history = sessions.trim_history(history)
            sessions.save_session(session_name, history)
        print()


def main():
    signal.signal(signal.SIGINT, sigint_handler)
    parser = argparse.ArgumentParser(
        prog="mvn",
        description="Cliente de terminal para o assistente local Marvin.",
        add_help=False
    )
    parser.add_argument("-h", "--help", action="store_true", help="Mostra esta ajuda.")
    parser.add_argument("-m", "--memory", action="store_true", help="Usa a memoria/sessao padrao.")
    parser.add_argument("-s", "--session", type=str, default=None, help="Nome da sessao de memoria a ser usada.")
    parser.add_argument("-x", "--exec", dest="execute_action", action="store_true", help="Modo operacional: gera e executa comando apos confirmacao.")
    parser.add_argument("--verbose", action="store_true", help="No modo -x, exibe a saida completa dos comandos.")
    parser.add_argument("-c", "--clear", nargs="?", const="", default=None, help="Limpa o historico da memoria.")
    parser.add_argument("-l", "--history", nargs="?", const="", default=None, help="Exibe o historico da memoria.")
    parser.add_argument("--sessions", action="store_true", help="Lista todas as sessoes salvas.")
    parser.add_argument("--status", action="store_true", help="Verifica se o servico Marvin esta respondendo.")
    parser.add_argument("--prompt", "--personality", dest="edit_prompt", action="store_true", help="Abre o arquivo de personalidade no editor.")
    parser.add_argument("--show-prompt", action="store_true", help="Exibe a personalidade ativa do Marvin.")
    parser.add_argument("--system", type=str, default=None, help="Sobrescreve o prompt de sistema para esta chamada.")
    web_group = parser.add_mutually_exclusive_group()
    web_group.add_argument("--web-search", action="store_true", help="Força uma pesquisa web para esta pergunta.")
    web_group.add_argument("--no-web-search", action="store_true", help="Desativa a pesquisa web para esta pergunta.")
    parser.add_argument("--search-engines", type=str, default=None, help="Mecanismos SearXNG separados por vírgula, por exemplo google,bing.")
    parser.add_argument("--search-category", choices=["general", "it", "news", "science", "files", "images", "videos", "music", "map"], default=None, help="Categoria de pesquisa do SearXNG.")
    parser.add_argument("--search-time-range", choices=["day", "week", "month", "year"], default=None, help="Recorte temporal da pesquisa.")
    parser.add_argument("query", nargs="*", help="Pergunta ou comando para o Marvin.")

    args = parser.parse_args()

    if args.help:
        parser.print_help()
        print("\nExemplos de uso:")
        print("  mvn o que e RAG?                  (Pergunta direta sem memoria)")
        print("  mvn -m continue a explicacao      (Pergunta usando memoria padrao)")
        print("  mvn -s projeto 'como fazer X?'    (Pergunta usando a sessao 'projeto')")
        print("  mvn -x verifique o uso de memoria (Confirma comandos; mostra saidas resumidas)")
        print("  mvn -x --verbose verifique os logs (Mostra a saida completa dos comandos)")
        print("  mvn -c                            (Limpa memoria padrao)")
        print("  mvn -c projeto                    (Limpa memoria da sessao 'projeto')")
        print("  mvn -l                            (Exibe historico da sessao padrao)")
        print("  mvn --show-prompt                 (Exibe as instrucoes/personalidade ativa)")
        print("  mvn --prompt                      (Abre o arquivo de personalidade no editor)")
        print("  cat script.py | mvn 'explique'    (Le entrada via pipe)")
        print("  mvn                               (Inicia chat interativo)")
        return

    if args.status:
        api.check_status()
        return

    if args.edit_prompt:
        personality.edit_personality()
        return

    if args.show_prompt:
        personality.show_personality()
        return

    if args.sessions:
        sessions.list_sessions()
        return

    # Limpar sessao
    if args.clear is not None:
        target = args.clear.strip() or args.session or "default"
        sessions.clear_session(target)
        return

    # Exibir historico
    if args.history is not None:
        target = args.history.strip() or args.session or "default"
        sessions.show_history(target)
        return

    # Resolver system prompt
    active_system_prompt = args.system if args.system is not None else personality.get_system_prompt()

    # Juntar os argumentos da pergunta
    user_query = " ".join(args.query).strip()

    # Verificar se ha entrada via pipe (apenas se nao for modo acao com pergunta ja informada)
    pipe_input = ""
    if not (args.execute_action and user_query) and not sys.stdin.isatty():
        try:
            pipe_input = sys.stdin.read().strip()
        except Exception:
            pipe_input = ""

    if pipe_input:
        if user_query:
            user_query = f"{user_query}\n\nContexto / Entrada adicional:\n{pipe_input}"
        else:
            user_query = pipe_input

    # Modo Acao / Execucao Operacional (-x)
    if args.execute_action:
        if not user_query:
            sys.stderr.write("[Erro]: Especifique a acao desejada. Exemplo: mvn -x verifique o uso de memoria\n")
            sys.exit(1)
        action_search_options = {
            "engines": args.search_engines,
            "categories": args.search_category,
            "time_range": args.search_time_range,
        }
        actions.handle_action_mode(
            user_query,
            verbose=args.verbose,
            search_options=action_search_options,
            allow_web_search=not args.no_web_search,
        )
        return

    # Se nao ha pergunta e stdin e um terminal, inicia REPL interativo
    if not user_query:
        if sys.stdin.isatty():
            use_memory = bool(args.session or args.memory)
            session_name = args.session or "default"
            interactive_repl(session_name, use_memory, active_system_prompt)
            return
        else:
            sys.stderr.write("[Erro]: Nenhuma pergunta fornecida via argumento ou pipe.\n")
            sys.exit(1)

    # Determinar se usa memoria e qual sessao
    use_memory = False
    session_name = "default"

    if args.session:
        use_memory = True
        session_name = args.session
    elif args.memory:
        use_memory = True
        session_name = "default"

    # Modo pergunta direta (stateless ou com memoria)
    if use_memory:
        history = sessions.trim_history(sessions.load_session(session_name))
        conversation = history + [{"role": "user", "content": user_query}]
        search_options = {"engines": args.search_engines, "categories": args.search_category, "time_range": args.search_time_range}
        if args.web_search:
            answer = chat.answer_with_web_search(conversation, active_system_prompt, user_query, search_options)
        else:
            answer = chat.stream_chat(conversation, active_system_prompt, allow_web_search=not args.no_web_search, search_options=search_options)
        history.append({"role": "user", "content": user_query})
        history.append({"role": "assistant", "content": answer})
        history = sessions.trim_history(history)
        sessions.save_session(session_name, history)
    else:
        conversation = [{"role": "user", "content": user_query}]
        search_options = {"engines": args.search_engines, "categories": args.search_category, "time_range": args.search_time_range}
        if args.web_search:
            chat.answer_with_web_search(conversation, active_system_prompt, user_query, search_options)
        else:
            chat.stream_chat(conversation, active_system_prompt, allow_web_search=not args.no_web_search, search_options=search_options)
