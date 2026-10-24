import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest import mock

from marvin import actions, api, chat, cli, config, personality, planning, search as web, sessions


def stream_response(content):
    event = {"choices": [{"delta": {"content": content}}]}
    body = f"data: {json.dumps(event)}\n\ndata: [DONE]\n".encode("utf-8")
    return io.BytesIO(body)


class MarvinCLITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sessions_dir = Path(self.temp_dir.name) / "sessions"
        self.sessions_patcher = mock.patch.object(config, "SESSIONS_DIR", self.sessions_dir)
        self.sessions_patcher.start()
        self.addCleanup(self.sessions_patcher.stop)
        self.addCleanup(self.temp_dir.cleanup)

    def run_cli(self, arguments, urlopen=None):
        stdout = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"))
            stack.enter_context(mock.patch.object(sys, "argv", ["mvn", *arguments]))
            stack.enter_context(mock.patch.object(sys, "stdin", io.StringIO("")))
            if urlopen is not None:
                stack.enter_context(mock.patch.object(api.request, "urlopen", side_effect=urlopen))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            cli.main()
        return stdout.getvalue()

    def captured_payload(self, urlopen_mock, call_index=0):
        request = urlopen_mock.call_args_list[call_index].args[0]
        return json.loads(request.data.decode("utf-8"))

    def test_web_search_parser_repairs_extra_braces_and_missing_marker_character(self):
        payload, error = web._parse_web_search_request(
            '<<<WEB_SEARCH>>>{{"query": "filme Homem-Aranha 2 lançamento"}}'
        )

        self.assertEqual(error, "")
        self.assertEqual(payload["query"], "filme Homem-Aranha 2 lançamento")

        payload, error = web._parse_web_search_request(
            '<<<WEB_SEARCH>>\n{"query": "presidente atual da Argentina"}'
        )
        self.assertEqual(error, "")
        self.assertEqual(payload["query"], "presidente atual da Argentina")

    def test_normal_chat_retries_malformed_search_request_once(self):
        initial = '<<<WEB_SEARCH>>>\n{"query": "lançamento Homem-Aranha 2",}'
        repaired = '<<<WEB_SEARCH>>>\n{"query": "lançamento Homem-Aranha 2"}'
        messages = [{"role": "user", "content": "qual foi o lançamento de Homem-Aranha 2?"}]
        with mock.patch.object(api.request, "urlopen", return_value=stream_response(initial)):
            with mock.patch.object(api, "query_messages_raw", return_value=repaired) as repair:
                with mock.patch.object(chat, "answer_with_web_search", return_value="O filme foi lançado em 2004.") as answer:
                    result = chat.stream_chat(messages, "Prompt de teste", render_output=False)

        self.assertEqual(result, "O filme foi lançado em 2004.")
        repair.assert_called_once()
        answer.assert_called_once()
        self.assertEqual(answer.call_args.args[2], "lançamento Homem-Aranha 2")

    def test_explicit_search_request_uses_web_evidence_flow(self):
        messages = [{"role": "user", "content": "pesquise quando foi lançado o filme Homem-Aranha 2"}]
        with mock.patch.object(chat, "answer_with_web_search", return_value="Resposta baseada nas fontes.") as answer:
            result = chat.stream_chat(messages, "Prompt de teste", render_output=False)

        self.assertEqual(result, "Resposta baseada nas fontes.")
        answer.assert_called_once()
        self.assertEqual(answer.call_args.args[2], messages[0]["content"])

    def test_explicit_search_cancellation_does_not_call_web_search(self):
        messages = [{"role": "user", "content": "pesquise o clima, não quero saber do tempo"}]
        with mock.patch.object(chat, "answer_with_web_search") as answer:
            with mock.patch.object(api.request, "urlopen", return_value=stream_response("Entendi.")) as model:
                result = chat.stream_chat(messages, "Prompt de teste", render_output=False)

        answer.assert_not_called()
        model.assert_called_once()
        self.assertEqual(result, "Entendi.")

    def test_action_mode_repairs_malformed_web_search_request(self):
        replies = iter([
            '<<<WEB_SEARCH>>>\n{"query": "presidente atual da Argentina",}',
            '<<<WEB_SEARCH>>>\n{"query": "presidente atual da Argentina"}',
            "<<<FINAL_ANSWER>>>Pesquisa concluída com evidências.",
        ])
        query = mock.Mock(side_effect=lambda _messages: next(replies))
        messages = [
            {"role": "system", "content": "Use a pesquisa."},
            {"role": "user", "content": "Pesquise o presidente atual da Argentina."},
        ]

        with mock.patch.object(api, "query_messages_raw", query):
            with mock.patch.object(actions, "_run_action_web_search", return_value="Consulta: presidente atual. [1] fonte") as search:
                result = actions.query_action_model(messages)

        self.assertEqual(result, "<<<FINAL_ANSWER>>>Pesquisa concluída com evidências.")
        self.assertEqual(query.call_count, 3)
        search.assert_called_once_with({"query": "presidente atual da Argentina"}, None)

    def test_basic_question_uses_one_system_message(self):
        urlopen = mock.Mock(return_value=stream_response("Sim."))

        output = self.run_cli(["consegue me entender?"], urlopen=urlopen)

        payload = self.captured_payload(urlopen)
        roles = [message["role"] for message in payload["messages"]]
        self.assertEqual(roles, ["system", "user"])
        self.assertIn("Ferramenta de pesquisa na web", payload["messages"][0]["content"])
        self.assertIn("Sim.", output)

    def test_http_500_is_reported_as_model_error_not_connection_error(self):
        body = json.dumps({"error": {"message": "System message must be at the beginning."}}).encode("utf-8")
        http_error = HTTPError(config.API_URL, 500, "Internal Server Error", {}, io.BytesIO(body))
        stderr = io.StringIO()

        with mock.patch.object(api.request, "urlopen", side_effect=http_error):
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    chat.stream_chat([{"role": "user", "content": "oi"}], "Prompt de teste")

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("[Erro do modelo]", stderr.getvalue())
        self.assertIn("System message must be at the beginning", stderr.getvalue())
        self.assertNotIn("[Erro de conexao]", stderr.getvalue())

    def test_default_memory_question_loads_and_saves_history(self):
        sessions.save_session("default", [
            {"role": "user", "content": "Lembre-se do nome Ada."},
            {"role": "assistant", "content": "Vou lembrar."},
        ])
        urlopen = mock.Mock(return_value=stream_response("Ada."))

        self.run_cli(["-m", "qual nome mencionei?"], urlopen=urlopen)

        payload = self.captured_payload(urlopen)
        contents = [message["content"] for message in payload["messages"]]
        self.assertIn("Lembre-se do nome Ada.", contents)
        self.assertEqual(contents[-1], "qual nome mencionei?")
        saved = sessions.load_session("default")
        self.assertEqual(len(saved), 4)
        self.assertEqual(saved[-1]["content"], "Ada.")

    def test_named_memory_question_loads_and_saves_its_own_history(self):
        sessions.save_session("memoriapersonalizada", [
            {"role": "user", "content": "O projeto se chama Atlas."},
            {"role": "assistant", "content": "Entendido."},
        ])
        urlopen = mock.Mock(return_value=stream_response("Atlas."))

        self.run_cli(["-s", "memoriapersonalizada", "qual o nome do projeto?"], urlopen=urlopen)

        payload = self.captured_payload(urlopen)
        self.assertEqual(payload["messages"][-3]["content"], "O projeto se chama Atlas.")
        saved = sessions.load_session("memoriapersonalizada")
        self.assertEqual(len(saved), 4)
        self.assertEqual(saved[-1]["content"], "Atlas.")
        self.assertFalse(sessions.get_session_file("default").exists())

    def test_action_mode_confirms_and_executes_proposed_command(self):
        model_replies = iter([
            "<<<ACTION>>>\nComando: printf seguro\nFinalidade: verificar o fluxo de teste",
            "<<<FINAL_ANSWER>>>\nComando concluído.",
            "<<<TASK_COMPLETE>>>",
        ])
        completed = subprocess.CompletedProcess("printf seguro", 0, stdout="seguro\n", stderr="")
        output = io.StringIO()
        with mock.patch.object(config, "SESSIONS_DIR", self.sessions_dir):
            with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
                with mock.patch.object(sys, "argv", ["mvn", "-x", "confirme uma operação segura"]):
                    with mock.patch.object(sys, "stdin", io.StringIO("")):
                        with mock.patch.object(api, "query_messages_raw", side_effect=lambda _messages: next(model_replies)):
                            with mock.patch.object(web, "search_web") as search:
                                with mock.patch("builtins.input", return_value="s"):
                                    with mock.patch.object(actions.subprocess, "run", return_value=completed) as run_command:
                                        with contextlib.redirect_stdout(output):
                                            cli.main()

        run_command.assert_called_once()
        search.assert_not_called()
        self.assertEqual(run_command.call_args.args[0], "printf seguro")
        self.assertIn("seguro", output.getvalue())
        self.assertIn("Comando concluído.", output.getvalue())

    def test_simple_named_directory_request_builds_exact_idempotent_mkdir(self):
        action = planning._simple_directory_creation_action("crie uma pasta chamada senna em /tmp")

        self.assertIn("<<<ACTION>>>", action)
        self.assertIn("Comando: mkdir -p -- /tmp/senna", action)
        self.assertEqual(
            planning._simple_directory_creation_action("crie uma pasta em /tmp"),
            "",
            "não deve inventar o nome que falta",
        )
        self.assertEqual(
            planning._simple_directory_creation_action("pesquise o presidente atual e crie uma pasta com o nome dele em /tmp"),
            "",
            "a etapa dependente de pesquisa não pode ser convertida em um mkdir antecipado",
        )

    def test_action_mode_creates_named_directory_without_search_and_waits_for_confirmation(self):
        model_replies = iter([
            "<<<FINAL_ANSWER>>>A pasta /tmp/senna foi criada.",
            "<<<TASK_COMPLETE>>>",
        ])
        query = mock.Mock(side_effect=lambda _messages: next(model_replies))
        completed = subprocess.CompletedProcess("mkdir", 0, stdout="", stderr="")
        output = io.StringIO()
        events = []

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", "crie uma pasta chamada senna em /tmp"]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch.object(web, "search_web") as search:
                            with mock.patch("builtins.input", return_value="s") as confirmation:
                                with mock.patch.object(
                                    actions.subprocess,
                                    "run",
                                    side_effect=lambda *args, **kwargs: (events.append("command"), completed)[1],
                                ) as run_command:
                                    with contextlib.redirect_stdout(output):
                                        cli.main()

        search.assert_not_called()
        confirmation.assert_called_once()
        run_command.assert_called_once()
        self.assertEqual(run_command.call_args.args[0], "mkdir -p -- /tmp/senna")
        self.assertEqual(events, ["command"])
        self.assertEqual(query.call_count, 2, "o modelo só deve auditar depois da execução")
        self.assertIn("Comando proposto", output.getvalue())
        self.assertIn("Relatório final", output.getvalue())

    def test_action_mode_rejects_unsolicited_search_and_repairs_plain_response(self):
        model_replies = iter([
            web.WEB_SEARCH_MARKER + json.dumps({"query": "como criar arquivo em /tmp"}),
            "Sugiro primeiro verificar o diretório com ls.",
            "<<<ACTION>>>\nComando: touch /tmp/marvin-action-test\nFinalidade: criar o arquivo solicitado",
            "<<<FINAL_ANSWER>>>O arquivo foi criado.",
            "<<<TASK_COMPLETE>>>",
        ])
        query = mock.Mock(side_effect=lambda _messages: next(model_replies))
        completed = subprocess.CompletedProcess("touch", 0, stdout="", stderr="")
        output = io.StringIO()

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", "crie um arquivo chamado marvin-action-test em /tmp"]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch.object(web, "search_web") as search:
                            with mock.patch("builtins.input", return_value="s"):
                                with mock.patch.object(actions.subprocess, "run", return_value=completed) as run_command:
                                    with contextlib.redirect_stdout(output):
                                        cli.main()

        search.assert_not_called()
        self.assertEqual(run_command.call_count, 1)
        self.assertEqual(run_command.call_args.args[0], "touch /tmp/marvin-action-test")
        self.assertIn("Não responda com uma explicação", query.call_args_list[2].args[0][-1]["content"])
        self.assertIn("Comando proposto", output.getvalue())

    def test_action_mode_researches_before_proposing_dependent_command(self):
        plan = config.TASK_PLAN_MARKER + json.dumps({"steps": [
            {"id": "s1", "kind": "research", "goal": "Pesquisar o nome do presidente atual de Cuba", "depends_on": []},
            {"id": "s2", "kind": "command", "goal": "Criar a pasta em /tmp com o nome confirmado", "depends_on": ["s1"]},
        ]}, ensure_ascii=False)
        search_request = web.WEB_SEARCH_MARKER + json.dumps({
            "query": "nome do atual presidente de Cuba",
            "categories": "general",
        })
        replies = [
            plan,
            search_request,
            "<<<ACTION>>>\nEtapa: s2\nComando: mkdir -p '/tmp/Miguel Díaz-Canel'\nFinalidade: criar a pasta com o nome confirmado pela pesquisa",
            "<<<FINAL_ANSWER>>>\nA pasta foi criada com o nome do presidente identificado nas fontes [1].",
            "<<<TASK_COMPLETE>>>",
        ]
        query = mock.Mock(side_effect=replies)
        search_results = {
            "results": [{
                "title": "Quem é o presidente de Cuba? Miguel Díaz-Canel",
                "url": "https://example.org/cuba-presidente",
                "content": "Miguel Díaz-Canel é o atual presidente de Cuba.",
                "published_date": "2026-10-18",
            }],
            "unresponsive_engines": [],
        }
        completed = subprocess.CompletedProcess("mkdir", 0, stdout="", stderr="")
        output = io.StringIO()

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", "pesquise quem é o presidente atual de Cuba e crie uma pasta em /tmp com o nome dele"]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch.object(web, "search_web", return_value=search_results) as search:
                            with mock.patch.object(web, "enrich_web_results"):
                                with mock.patch("builtins.input", return_value="s"):
                                    with mock.patch.object(actions.subprocess, "run", return_value=completed) as run_command:
                                        with contextlib.redirect_stdout(output):
                                            cli.main()

        search.assert_called_once_with("nome do atual presidente de Cuba", {
            "categories": "general",
        })
        run_command.assert_called_once()
        self.assertIn("Miguel Díaz-Canel", run_command.call_args.args[0])
        self.assertEqual(query.call_count, 5)
        research_context = "\n".join(message["content"] for message in query.call_args_list[2].args[0])
        self.assertIn("Miguel Díaz-Canel é o atual presidente de Cuba", research_context)
        self.assertIn("https://example.org/cuba-presidente", research_context)
        self.assertIn("Comando proposto", output.getvalue())

    def test_action_mode_recovers_from_invalid_plan_then_researches_and_confirms_dependent_command(self):
        user_request = (
            "pesquise qual é o nome do atual presidente da Argentina, "
            "e crie uma pasta em /tmp com o nome dele"
        )
        valid_plan = config.TASK_PLAN_MARKER + json.dumps({"steps": [
            {"id": "s1", "kind": "research", "goal": "Identificar o nome oficial atual do Presidente da Argentina", "depends_on": []},
            {"id": "s2", "kind": "command", "goal": "Criar em /tmp uma pasta com o nome encontrado", "depends_on": ["s1"]},
        ]}, ensure_ascii=False)
        search_request = web.WEB_SEARCH_MARKER + json.dumps({
            "step_id": "s1",
            "query": "nome atual do presidente da Argentina",
            "categories": "general",
        }, ensure_ascii=False)
        replies = [
            "Não consegui organizar essa tarefa.",  # Primeira tentativa de plano inválida.
            valid_plan,  # O reparo do planejador recupera o fluxo.
            search_request,
            (
                "<<<ACTION>>>\nEtapa: s2\n"
                "Comando: mkdir -p '/tmp/Javier Milei'\n"
                "Finalidade: criar a pasta com o nome confirmado pela pesquisa"
            ),
            "<<<FINAL_ANSWER>>>\nA pasta /tmp/Javier Milei foi criada com sucesso.",
            "<<<TASK_COMPLETE>>>",
        ]
        query = mock.Mock(side_effect=replies)
        search_results = {
            "results": [{
                "title": "Javier Milei, presidente da Argentina",
                "url": "https://example.org/presidente-argentina",
                "content": "Javier Milei é o atual presidente da Argentina.",
                "published_date": "2026-10-19",
            }],
            "unresponsive_engines": [],
        }
        completed = subprocess.CompletedProcess("mkdir", 0, stdout="", stderr="")
        output = io.StringIO()
        events = []

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", user_request]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch.object(web, "search_web", side_effect=lambda *args: (events.append("search"), search_results)[1]) as search:
                            with mock.patch.object(web, "enrich_web_results"):
                                with mock.patch("builtins.input", return_value="s") as confirmation:
                                    with mock.patch.object(actions.subprocess, "run", side_effect=lambda *args, **kwargs: (events.append("command"), completed)[1]) as run_command:
                                        with contextlib.redirect_stdout(output):
                                            cli.main()

        self.assertEqual(query.call_count, 6)
        self.assertEqual(search.call_count, 1)
        self.assertEqual(run_command.call_count, 1)
        self.assertEqual(events, ["search", "command"])
        self.assertEqual(search.call_args.args[0], "nome atual do presidente da Argentina")
        self.assertEqual(run_command.call_args.args[0], "mkdir -p '/tmp/Javier Milei'")
        confirmation.assert_called_once()
        self.assertIn("Comando proposto", output.getvalue())
        self.assertIn("/tmp/Javier Milei foi criada com sucesso", output.getvalue())
        self.assertNotIn("Plano pendente", output.getvalue())

    def test_action_mode_retries_inconclusive_research_before_dependent_action(self):
        plan = config.TASK_PLAN_MARKER + json.dumps({"steps": [
            {"id": "s1", "kind": "research", "goal": "Encontrar o nome atual solicitado", "depends_on": []},
            {"id": "s2", "kind": "command", "goal": "Criar a pasta usando o nome encontrado", "depends_on": ["s1"]},
        ]}, ensure_ascii=False)
        first_search = web.WEB_SEARCH_MARKER + json.dumps({
            "step_id": "s1", "query": "presidente atual Cuba",
        })
        second_search = web.WEB_SEARCH_MARKER + json.dumps({
            "step_id": "s1", "query": "quem é Miguel Díaz-Canel presidente de Cuba",
        }, ensure_ascii=False)
        replies = [
            plan,
            first_search,
            second_search,
            "<<<ACTION>>>\nEtapa: s2\nComando: mkdir -p '/tmp/Miguel Díaz-Canel'\nFinalidade: criar a pasta com o nome encontrado",
            "<<<FINAL_ANSWER>>>\nA pasta foi criada com o nome confirmado pelas fontes [1].",
            "<<<TASK_COMPLETE>>>",
        ]
        query = mock.Mock(side_effect=replies)
        usable_results = {
            "results": [{
                "title": "Presidente cubano Miguel Díaz-Canel",
                "url": "https://example.org/presidente-cuba",
                "content": "Miguel Díaz-Canel é o presidente atual de Cuba.",
                "published_date": "2026-10-18",
            }],
            "unresponsive_engines": [],
        }
        search_responses = [
            {"results": [], "unresponsive_engines": []},
            usable_results,
        ]
        completed = subprocess.CompletedProcess("mkdir", 0, stdout="", stderr="")
        output = io.StringIO()

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", [
                "mvn", "-x", "pesquise o presidente atual de Cuba e crie uma pasta em /tmp com o nome dele",
            ]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch.object(web, "search_web", side_effect=search_responses) as search:
                            with mock.patch.object(web, "enrich_web_results"):
                                with mock.patch("builtins.input", return_value="s"):
                                    with mock.patch.object(actions.subprocess, "run", return_value=completed) as run_command:
                                        with contextlib.redirect_stdout(output):
                                            cli.main()

        self.assertEqual(search.call_count, 2)
        self.assertEqual(run_command.call_count, 1)
        self.assertEqual(run_command.call_args.args[0], "mkdir -p '/tmp/Miguel Díaz-Canel'")
        self.assertEqual(search.call_args_list[0].args[0], "presidente atual Cuba")
        self.assertEqual(search.call_args_list[1].args[0], "quem é Miguel Díaz-Canel presidente de Cuba")
        self.assertLess(output.getvalue().index("Pesquisando na web"), output.getvalue().index("Comando proposto"))

    def test_action_mode_accepts_followup_search_for_completed_research_step(self):
        task_steps, error = planning._validate_task_plan({"steps": [
            {"id": "s1", "kind": "research", "goal": "Pesquisar o fato solicitado", "depends_on": []},
            {"id": "s2", "kind": "command", "goal": "Usar o fato na ação", "depends_on": ["s1"]},
        ]})
        self.assertEqual(error, "")
        action_plan = planning._build_action_plan("pesquise quem é o presidente atual e crie uma pasta com o nome dele")
        messages = [
            {"role": "system", "content": "Plano estruturado"},
            {"role": "user", "content": "Solicitação de teste"},
        ]
        requests = [
            web.WEB_SEARCH_MARKER + json.dumps({"step_id": "s1", "query": "consulta inicial"}),
            web.WEB_SEARCH_MARKER + json.dumps({"step_id": "s1", "query": "consulta complementar"}),
            "<<<ACTION>>>\nEtapa: s2\nComando: mkdir -p '/tmp/Miguel Díaz-Canel'\nFinalidade: usar o dado pesquisado",
        ]
        query = mock.Mock(side_effect=requests)

        with mock.patch.object(api, "query_messages_raw", query):
            with mock.patch.object(actions, "_run_action_web_search",
                side_effect=[
                    "Consulta: quem é o presidente atual?\n"
                    "[1] Miguel Díaz-Canel é o presidente atual de Cuba.\n"
                    "URL: https://example.org/presidente",
                    "Consulta: confirmação do presidente de Cuba\n"
                    "[1] Miguel Díaz-Canel ocupa a presidência de Cuba.\n"
                    "URL: https://example.org/confirmacao",
                ],
            ) as search:
                result = actions.query_action_model(
                    messages,
                    allow_web_search=True,
                    action_plan=action_plan,
                    task_steps=task_steps,
                )

        self.assertIn("<<<ACTION>>>", result)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(task_steps[0]["status"], "complete")
        self.assertIn("Miguel Díaz-Canel é o presidente atual de Cuba", task_steps[0]["result"])
        self.assertIn("Miguel Díaz-Canel ocupa a presidência de Cuba", task_steps[0]["result"])

    def test_action_mode_compacts_old_messages_before_model_request(self):
        messages = [
            {"role": "system", "content": "instruções"},
            {"role": "user", "content": "tarefa original"},
            {"role": "assistant", "content": "mensagem antiga " * 3000},
            {"role": "user", "content": "resultado antigo " * 3000},
            {"role": "assistant", "content": "ação recente"},
            {"role": "user", "content": "evidência mais recente"},
        ]

        compacted = actions._compact_action_messages(messages)

        self.assertEqual(compacted[0]["content"], "instruções")
        self.assertEqual(compacted[1]["content"], "tarefa original")
        self.assertNotIn("mensagem antiga", "\n".join(item["content"] for item in compacted))
        self.assertIn("evidência mais recente", compacted[-1]["content"])
        self.assertLessEqual(
            sum(len(item["content"]) for item in compacted),
            config.MAX_ACTION_CONTEXT_CHARACTERS,
        )

    def test_action_search_context_is_capped_before_adding_to_model_history(self):
        results = [{
            "title": f"Resultado relevante {index}",
            "url": f"https://example.org/{index}",
            "content": "informação relevante " * 200,
            "published_date": "",
        } for index in range(6)]
        with mock.patch.object(config, "MAX_ACTION_SEARCH_CONTEXT_CHARACTERS", 2500):
            with mock.patch.object(web, "search_web", return_value={"results": results, "unresponsive_engines": []}):
                with mock.patch.object(web, "_search_results_are_relevant", return_value=results):
                    with mock.patch.object(web, "enrich_web_results", side_effect=lambda items: [
                        item.update(page_text="texto da página " * 300) for item in items
                    ]):
                        context = actions._run_action_web_search({"query": "informação relevante"})

        self.assertLessEqual(len(context), 2500)
        self.assertIn("Resultados adicionais omitidos", context)

    def test_search_web_replaces_null_language_with_configured_default(self):
        response = io.BytesIO(json.dumps({"results": []}).encode())
        with mock.patch.object(api.request, "urlopen", return_value=response) as urlopen:
            web.search_web("quando foi lançado o filme", {"language": None})

        request = urlopen.call_args.args[0]
        params = web.parse.parse_qs(web.parse.urlsplit(request.full_url).query)
        self.assertEqual(params["language"], ["pt-BR"])

    def test_action_mode_blocks_unrelated_command_until_required_research_is_done(self):
        user_request = "pesquise qual é o nome do atual presidente de cuba, e crie uma pasta em /tmp com o nome dele"
        plan = config.TASK_PLAN_MARKER + json.dumps({"steps": [
            {"id": "s1", "kind": "research", "goal": "Pesquisar quem é o presidente atual de Cuba", "depends_on": []},
            {"id": "s2", "kind": "command", "goal": "Criar uma pasta em /tmp com o nome confirmado", "depends_on": ["s1"]},
        ]}, ensure_ascii=False)
        search_request = web.WEB_SEARCH_MARKER + json.dumps({
            "query": "nome do atual presidente de Cuba",
            "categories": "general",
        })
        replies = [
            plan,
            "<<<ACTION>>>\nComando: whoami\nFinalidade: verificar o usuário atual",
            search_request,
            "<<<ACTION>>>\nComando: whoami\nFinalidade: verificar novamente o usuário atual",
            "<<<ACTION>>>\nComando: whoami\nFinalidade: confirmar permissões antes da criação",
            "<<<ACTION>>>\nEtapa: s2\nComando: mkdir -p '/tmp/Miguel Díaz-Canel'\nFinalidade: criar a pasta usando o nome identificado na pesquisa",
            "<<<FINAL_ANSWER>>>\nA pasta foi criada com o nome obtido na pesquisa [1].",
            "<<<TASK_COMPLETE>>>",
        ]
        query = mock.Mock(side_effect=replies)
        search_results = {
            "results": [{
                "title": "Presidente de Cuba: Miguel Díaz-Canel",
                "url": "https://example.org/cuba-presidente",
                "content": "Miguel Díaz-Canel é o atual presidente de Cuba.",
                "published_date": "2026-10-18",
            }],
            "unresponsive_engines": [],
        }
        completed = subprocess.CompletedProcess("mkdir", 0, stdout="", stderr="")
        output = io.StringIO()

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", user_request]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch.object(web, "search_web", return_value=search_results) as search:
                            with mock.patch.object(web, "enrich_web_results"):
                                with mock.patch("builtins.input", return_value="s"):
                                    with mock.patch.object(actions.subprocess, "run", return_value=completed) as run_command:
                                        with contextlib.redirect_stdout(output):
                                            cli.main()

        search.assert_called_once_with("nome do atual presidente de Cuba", {
            "categories": "general",
        })
        run_command.assert_called_once()
        self.assertEqual(run_command.call_args.args[0], "mkdir -p '/tmp/Miguel Díaz-Canel'")
        self.assertNotIn("whoami", run_command.call_args.args[0])
        self.assertNotIn("whoami", output.getvalue())
        self.assertIn("Pesquisando na web", output.getvalue())
        self.assertLess(output.getvalue().index("Pesquisando na web"), output.getvalue().index("Comando proposto"))
        self.assertIn("Miguel Díaz-Canel é o atual presidente de Cuba", "\n".join(
            message["content"] for message in query.call_args_list[3].args[0]
        ))
        self.assertEqual(query.call_count, 8)
        repair_context = "\n".join(
            message["content"] for message in query.call_args_list[4].args[0]
        ).casefold()
        self.assertIn("etapa: id", repair_context)
        self.assertIn("Etapas estruturadas do pedido", "\n".join(
            message["content"] for message in query.call_args_list[2].args[0]
        ))

    def test_action_mode_enforces_dependencies_between_generic_command_steps(self):
        plan = config.TASK_PLAN_MARKER + json.dumps({"steps": [
            {"id": "s1", "kind": "command", "goal": "Inspecionar o estado atual", "depends_on": []},
            {"id": "s2", "kind": "command", "goal": "Executar a ação com base na inspeção", "depends_on": ["s1"]},
        ]}, ensure_ascii=False)
        replies = [
            plan,
            "<<<ACTION>>>\nEtapa: s2\nComando: printf dependente\nFinalidade: agir usando a inspeção",
            "<<<ACTION>>>\nEtapa: s1\nComando: printf estado\nFinalidade: inspecionar o estado atual",
            "<<<ACTION>>>\nEtapa: s2\nComando: printf acao\nFinalidade: executar a ação após a inspeção",
            "<<<FINAL_ANSWER>>>\nAs duas etapas foram concluídas.",
            "<<<TASK_COMPLETE>>>",
        ]
        query = mock.Mock(side_effect=replies)
        completed = [
            subprocess.CompletedProcess("printf estado", 0, stdout="estado atual\n", stderr=""),
            subprocess.CompletedProcess("printf acao", 0, stdout="ação feita\n", stderr=""),
        ]
        output = io.StringIO()

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", "inspecione o estado e depois execute a ação baseada nele"]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch("builtins.input", return_value="s"):
                            with mock.patch.object(actions.subprocess, "run", side_effect=completed) as run_command:
                                with contextlib.redirect_stdout(output):
                                    cli.main()

        self.assertEqual(run_command.call_count, 2)
        self.assertEqual(run_command.call_args_list[0].args[0], "printf estado")
        self.assertEqual(run_command.call_args_list[1].args[0], "printf acao")
        dependency_context = "\n".join(
            message["content"] for message in query.call_args_list[2].args[0]
        )
        self.assertIn("Essa etapa ainda não está pronta", dependency_context)
        self.assertIn("As duas etapas foram concluídas.", output.getvalue())

    def test_task_plan_rejects_cycles_and_unknown_dependencies(self):
        cyclic, error = planning._validate_task_plan({"steps": [
            {"id": "s1", "kind": "command", "goal": "primeira", "depends_on": ["s2"]},
            {"id": "s2", "kind": "command", "goal": "segunda", "depends_on": ["s1"]},
        ]})
        self.assertIsNone(cyclic)
        self.assertIn("circulares", error)

        unknown, error = planning._validate_task_plan({"steps": [
            {"id": "s1", "kind": "command", "goal": "primeira", "depends_on": ["inexistente"]},
        ]})
        self.assertIsNone(unknown)
        self.assertIn("Dependência inválida", error)

    def test_task_planner_repairs_a_plan_that_omits_the_requested_command(self):
        research_only = config.TASK_PLAN_MARKER + json.dumps({"steps": [
            {"id": "s1", "kind": "research", "goal": "Encontrar o nome", "depends_on": []},
        ]}, ensure_ascii=False)
        complete_plan = config.TASK_PLAN_MARKER + json.dumps({"steps": [
            {"id": "s1", "kind": "research", "goal": "Encontrar o nome", "depends_on": []},
            {"id": "s2", "kind": "command", "goal": "Criar a pasta com o nome encontrado", "depends_on": ["s1"]},
        ]}, ensure_ascii=False)
        query = mock.Mock(side_effect=[research_only, complete_plan])

        with mock.patch.object(api, "query_messages_raw", query):
            steps, error = planning._plan_task_with_model(
                "pesquise o nome atual e crie uma pasta com ele",
                "Prompt de teste",
                research_required=True,
                command_required=True,
            )

        self.assertEqual(error, "")
        self.assertEqual([step["kind"] for step in steps], ["research", "command"])
        repair_prompt = "\n".join(message["content"] for message in query.call_args_list[1].args[0])
        self.assertIn("inclua uma etapa command", repair_prompt)

    def test_failed_task_step_stays_open_and_blocks_dependent_step_until_retry(self):
        plan = config.TASK_PLAN_MARKER + json.dumps({"steps": [
            {"id": "s1", "kind": "command", "goal": "Preparar o recurso", "depends_on": []},
            {"id": "s2", "kind": "command", "goal": "Usar o recurso preparado", "depends_on": ["s1"]},
        ]}, ensure_ascii=False)
        replies = [
            plan,
            "<<<ACTION>>>\nEtapa: s1\nComando: false\nFinalidade: preparar o recurso",
            "<<<ACTION>>>\nEtapa: s2\nComando: printf dependente\nFinalidade: usar o recurso",
            "<<<ACTION>>>\nEtapa: s1\nComando: printf preparado\nFinalidade: preparar o recurso corretamente",
            "<<<ACTION>>>\nEtapa: s2\nComando: printf usado\nFinalidade: usar o recurso preparado",
            "<<<FINAL_ANSWER>>>\nA sequência foi concluída.",
            "<<<TASK_COMPLETE>>>",
        ]
        query = mock.Mock(side_effect=replies)
        completed = [
            subprocess.CompletedProcess("false", 1, stdout="", stderr="falha simulada"),
            subprocess.CompletedProcess("printf preparado", 0, stdout="pronto\n", stderr=""),
            subprocess.CompletedProcess("printf usado", 0, stdout="usado\n", stderr=""),
        ]
        output = io.StringIO()

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", "prepare o recurso e depois use o recurso preparado"]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch("builtins.input", return_value="s"):
                            with mock.patch.object(actions.subprocess, "run", side_effect=completed) as run_command:
                                with contextlib.redirect_stdout(output):
                                    cli.main()

        self.assertEqual(run_command.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in run_command.call_args_list],
            ["false", "printf preparado", "printf usado"],
        )
        blocked_context = "\n".join(
            message["content"] for message in query.call_args_list[3].args[0]
        )
        self.assertIn("dependências estejam concluídas", blocked_context)
        self.assertIn("resultado anterior: [stderr]\nfalha simulada", blocked_context)
        self.assertIn("A sequência foi concluída.", output.getvalue())

    def test_user_guidance_replans_only_remaining_task_steps(self):
        initial_plan = config.TASK_PLAN_MARKER + json.dumps({"steps": [
            {"id": "s1", "kind": "command", "goal": "Preparar o diretório", "depends_on": []},
            {"id": "s2", "kind": "command", "goal": "Criar o arquivo no diretório", "depends_on": ["s1"]},
        ]}, ensure_ascii=False)
        revised_plan = config.TASK_PLAN_MARKER + json.dumps({"steps": [
            {"id": "s1", "kind": "command", "goal": "Criar o diretório sem sudo conforme solicitado", "depends_on": []},
        ]}, ensure_ascii=False)
        replies = [
            initial_plan,
            "<<<ACTION>>>\nEtapa: s1\nComando: sudo mkdir -p /tmp/novo\nFinalidade: preparar o diretório",
            revised_plan,
            "<<<ACTION>>>\nEtapa: r1\nComando: mkdir -p /tmp/novo\nFinalidade: criar o diretório sem privilégios administrativos",
            "<<<FINAL_ANSWER>>>\nO diretório foi criado conforme a orientação.",
            "<<<TASK_COMPLETE>>>",
        ]
        query = mock.Mock(side_effect=replies)
        completed = subprocess.CompletedProcess("mkdir", 0, stdout="", stderr="")
        output = io.StringIO()
        answers = iter(["n", "Não use sudo; faça apenas a criação local.", "s"])

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", "prepare o diretório e depois crie o arquivo nele"]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch("builtins.input", side_effect=lambda _prompt: next(answers)):
                            with mock.patch.object(actions.subprocess, "run", return_value=completed) as run_command:
                                with contextlib.redirect_stdout(output):
                                    cli.main()

        run_command.assert_called_once_with(
            "mkdir -p /tmp/novo",
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertIn("Plano atualizado", output.getvalue())
        self.assertNotIn("Executando: sudo", output.getvalue())
        self.assertIn("Orientação nova do usuário", "\n".join(
            message["content"] for message in query.call_args_list[2].args[0]
        ))

    def test_action_plan_detects_required_research_and_respects_explicit_negative(self):
        plan = planning._build_action_plan(
            "pesquise qual é o nome do atual presidente de cuba e crie uma pasta com o nome dele"
        )
        self.assertTrue(plan["research_required"])
        self.assertIn("Pesquisar na web", plan["steps"][0])

        self.assertFalse(
            planning._build_action_plan("não pesquise o clima, estou só quebrando o gelo")[
                "research_required"
            ]
        )

    def test_action_mode_disables_search_for_local_task_without_forcing_it(self):
        replies = [
            "<<<ACTION>>>\nComando: printf teste\nFinalidade: verificar sem pesquisa",
            "<<<FINAL_ANSWER>>>\nConcluído.",
            "<<<TASK_COMPLETE>>>",
        ]
        query = mock.Mock(side_effect=replies)
        completed = subprocess.CompletedProcess("printf teste", 0, stdout="teste\n", stderr="")

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", "verifique o fluxo local"]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch.object(web, "search_web") as search:
                            with mock.patch("builtins.input", return_value="s"):
                                with mock.patch.object(actions.subprocess, "run", return_value=completed):
                                    with contextlib.redirect_stdout(io.StringIO()):
                                        cli.main()

        search.assert_not_called()
        system_content = query.call_args_list[0].args[0][0]["content"]
        self.assertNotIn("Ferramenta de pesquisa na web", system_content)
        self.assertIn("pesquisa web não é necessária", system_content)

    def test_action_mode_no_web_search_disables_research_tool(self):
        search_request = web.WEB_SEARCH_MARKER + json.dumps({"query": "informação atual"})
        replies = [
            search_request,
            "<<<ACTION>>>\nComando: printf sem-web\nFinalidade: continuar sem pesquisa",
            "<<<FINAL_ANSWER>>>\nConcluído sem pesquisa.",
            "<<<TASK_COMPLETE>>>",
        ]
        query = mock.Mock(side_effect=replies)
        completed = subprocess.CompletedProcess("printf sem-web", 0, stdout="sem-web\n", stderr="")

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", "--no-web-search", "continue sem pesquisa"]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch.object(web, "search_web") as search:
                            with mock.patch("builtins.input", return_value="s"):
                                with mock.patch.object(actions.subprocess, "run", return_value=completed):
                                    with contextlib.redirect_stdout(io.StringIO()):
                                        cli.main()

        search.assert_not_called()
        self.assertEqual(query.call_count, 4)
        self.assertTrue(any(
            "pesquisa web está desativada pelo usuário" in message["content"]
            for message in query.call_args_list[1].args[0]
        ))

    def test_action_mode_rejection_accepts_guidance_and_proposes_an_alternative(self):
        replies = [
            "<<<ACTION>>>\nComando: sudo mkdir -p /tmp/mvn\nFinalidade: criar o diretorio solicitado",
            "<<<ACTION>>>\nComando: mkdir -p /tmp/mvn\nFinalidade: criar o diretorio sem privilegios administrativos",
            "<<<FINAL_ANSWER>>>\nO diretorio foi criado sem usar sudo.",
            "<<<TASK_COMPLETE>>>",
        ]
        query = mock.Mock(side_effect=replies)
        completed = subprocess.CompletedProcess("mkdir", 0, stdout="", stderr="")
        output = io.StringIO()
        answers = iter(["n", "Não use sudo; prefiro uma alternativa sem privilégios.", "s"])

        def answer_confirmation(prompt):
            return next(answers)

        with mock.patch.object(config, "MAX_ACTION_TURNS", 2):
            with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
                with mock.patch.object(sys, "argv", ["mvn", "-x", "crie /tmp/mvn"]):
                    with mock.patch.object(sys, "stdin", io.StringIO("")):
                        with mock.patch.object(api, "query_messages_raw", query):
                            with mock.patch("builtins.input", side_effect=answer_confirmation):
                                with mock.patch.object(actions.subprocess, "run", return_value=completed) as run_command:
                                    with contextlib.redirect_stdout(output):
                                        cli.main()

        run_command.assert_called_once()
        self.assertEqual(run_command.call_args.args[0], "mkdir -p /tmp/mvn")
        self.assertIn("Comando recusado", output.getvalue())
        self.assertIn("O diretorio foi criado sem usar sudo.", output.getvalue())
        resumed_messages = query.call_args_list[1].args[0]
        refusal_context = "\n".join(message["content"] for message in resumed_messages)
        self.assertIn("foi recusada e NÃO foi executada", refusal_context)
        self.assertIn("sem privilégios", refusal_context)

    def test_action_mode_ctrl_c_during_rejection_guidance_cancels_without_execution(self):
        query = mock.Mock(return_value=(
            "<<<ACTION>>>\nComando: sudo mkdir -p /tmp/mvn\nFinalidade: criar o diretorio solicitado"
        ))
        output = io.StringIO()

        def interrupt_at_guidance(prompt):
            if prompt.startswith("Sua orientação para continuar"):
                raise KeyboardInterrupt
            return "n"

        with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", "crie /tmp/mvn"]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(api, "query_messages_raw", query):
                        with mock.patch("builtins.input", side_effect=interrupt_at_guidance):
                            with mock.patch.object(actions.subprocess, "run") as run_command:
                                with contextlib.redirect_stdout(output):
                                    cli.main()

        run_command.assert_not_called()
        query.assert_called_once()
        self.assertIn("Operação cancelada.", output.getvalue())

    def test_action_mode_waits_for_guidance_and_continues_in_same_run(self):
        replies = [
            "<<<ACTION>>>\nComando: printf tentativa1\nFinalidade: testar a primeira tentativa",
            "<<<ACTION>>>\nComando: printf verificar-url\nFinalidade: ainda preciso investigar",
            "O primeiro teste foi executado. Ainda nao confirmei a causa do problema.",
            "<<<ACTION>>>\nComando: printf tentativa2\nFinalidade: verificar a URL indicada pelo usuario",
            "<<<FINAL_ANSWER>>>\nA verificacao solicitada foi concluida.",
            "<<<TASK_COMPLETE>>>",
        ]
        query = mock.Mock(side_effect=replies)
        completed = [
            subprocess.CompletedProcess("printf", 0, stdout="falha observada\n", stderr=""),
            subprocess.CompletedProcess("printf", 0, stdout="URL verificada\n", stderr=""),
        ]
        output = io.StringIO()

        def answer_confirmation(prompt):
            if prompt.startswith("Sua orientação"):
                self.assertEqual(query.call_count, 3, "Marvin deve aguardar a orientacao antes de consultar o modelo novamente")
                self.assertIn("Diga o que devo investigar", output.getvalue())
                return "verifique se a URL esta certa"
            return "s"

        with mock.patch.object(config, "MAX_ACTION_TURNS", 1):
            with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
                with mock.patch.object(sys, "argv", ["mvn", "-x", "clone de teste"]):
                    with mock.patch.object(sys, "stdin", io.StringIO("")):
                        with mock.patch.object(api, "query_messages_raw", query):
                            with mock.patch("builtins.input", side_effect=answer_confirmation):
                                with mock.patch.object(actions.subprocess, "run", side_effect=completed) as run_command:
                                    with contextlib.redirect_stdout(output):
                                        cli.main()

        self.assertEqual(run_command.call_count, 2)
        self.assertIn("Marvin precisa de orientação", output.getvalue())
        self.assertIn("Ciclo 2", output.getvalue())
        self.assertIn("A verificacao solicitada foi concluida.", output.getvalue())
        resumed_messages = query.call_args_list[3].args[0]
        self.assertTrue(any("verifique se a URL esta certa" in message["content"] for message in resumed_messages))

    def test_action_mode_stops_when_user_ends_after_cycle(self):
        replies = [
            "<<<ACTION>>>\nComando: printf tentativa\nFinalidade: executar uma tentativa",
            "<<<ACTION>>>\nComando: printf proxima\nFinalidade: ainda preciso investigar",
            "Ainda nao consegui concluir. O resultado nao confirma a causa do problema.",
        ]
        query = mock.Mock(side_effect=replies)
        completed = subprocess.CompletedProcess("printf tentativa", 0, stdout="resultado parcial\n", stderr="")
        output = io.StringIO()

        def answer_confirmation(prompt):
            if prompt.startswith("Sua orientação"):
                self.assertEqual(query.call_count, 3, "A execucao deve aguardar a resposta antes de continuar")
                self.assertIn("Diga o que devo investigar", output.getvalue())
                return "encerrar"
            return "s"

        with mock.patch.object(config, "MAX_ACTION_TURNS", 1):
            with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
                with mock.patch.object(sys, "argv", ["mvn", "-x", "investigue uma falha"]):
                    with mock.patch.object(sys, "stdin", io.StringIO("")):
                        with mock.patch.object(api, "query_messages_raw", query):
                            with mock.patch("builtins.input", side_effect=answer_confirmation):
                                with mock.patch.object(actions.subprocess, "run", return_value=completed) as run_command:
                                    with contextlib.redirect_stdout(output):
                                        cli.main()

        run_command.assert_called_once()
        self.assertEqual(query.call_count, 3, "Nao deve consultar o modelo depois que o usuario encerra")
        self.assertIn("Investigação encerrada pelo usuário.", output.getvalue())
        self.assertNotIn("Ciclo 2", output.getvalue())

    def test_action_mode_rejects_final_answer_when_request_is_still_incomplete(self):
        replies = [
            "<<<ACTION>>>\nComando: git clone https://example.org/ausente /tmp/projeto\nFinalidade: clonar o repositorio solicitado",
            "<<<ACTION>>>\nComando: ls -la /tmp/projeto\nFinalidade: verificar se o clone criou os arquivos",
            "<<<FINAL_ANSWER>>>\nO clone falhou; o diretorio esta vazio. Proximo passo: conferir a URL.",
            "<<<TASK_INCOMPLETE>>>",
            "<<<ACTION>>>\nComando: git ls-remote https://example.org/ausente\nFinalidade: verificar se a URL aponta para um repositorio acessivel",
            "<<<ACTION>>>\nComando: printf diagnostico\nFinalidade: ainda falta uma orientacao para concluir",
            "<<<TASK_INCOMPLETE>>>",
            "O clone nao foi concluido. Os resultados indicam que a URL pode estar incorreta, mas isso ainda nao foi confirmado.",
        ]
        query = mock.Mock(side_effect=replies)
        completed = [
            subprocess.CompletedProcess("git clone", 128, stdout="", stderr="Repository not found"),
            subprocess.CompletedProcess("ls", 0, stdout="total 0\n", stderr=""),
            subprocess.CompletedProcess("git ls-remote", 128, stdout="", stderr="Repository not found"),
            subprocess.CompletedProcess("printf", 0, stdout="diagnostico\n", stderr=""),
        ]
        output = io.StringIO()

        with mock.patch.object(config, "MAX_ACTION_TURNS", 4):
            with mock.patch.object(personality, "get_system_prompt", return_value="Prompt de teste"):
                with mock.patch.object(sys, "argv", ["mvn", "-x", "clone o repositorio"]):
                    with mock.patch.object(sys, "stdin", io.StringIO("")):
                        with mock.patch.object(api, "query_messages_raw", query):
                            with mock.patch("builtins.input", side_effect=["s", "s", "s", "s", "encerrar"]):
                                with mock.patch.object(actions.subprocess, "run", side_effect=completed) as run_command:
                                    with contextlib.redirect_stdout(output):
                                        cli.main()

        self.assertEqual(run_command.call_count, 4)
        self.assertIn("Ciclo 1 · Passo 3/4", output.getvalue())
        self.assertIn("Marvin precisa de orientação", output.getvalue())
        self.assertNotIn("Relatório final", output.getvalue())

    def test_web_question_searches_and_sends_results_to_model(self):
        search_response = io.BytesIO(json.dumps({
            "results": [{
                "title": "Cotação atual do dólar em reais",
                "url": "https://example.org/guia",
                "content": "Resumo de cotação encontrado.",
                "publishedDate": "2026-01-01",
            }],
            "unresponsive_engines": [],
        }).encode("utf-8"))
        urlopen = mock.Mock(side_effect=[search_response, stream_response("Resposta com fonte.")])

        with mock.patch.object(web, "enrich_web_results"):
            output = self.run_cli(["pesquise a cotação do dólar hoje"], urlopen=urlopen)

        self.assertEqual(urlopen.call_count, 2)
        search_request = urlopen.call_args_list[0].args[0]
        self.assertIn("format=json", search_request.full_url)
        model_payload = self.captured_payload(urlopen, 1)
        self.assertIn("Resumo de cotação encontrado.", model_payload["messages"][-1]["content"])
        self.assertIn("Fontes consultadas:", output)
        self.assertIn("https://example.org/guia", output)

    def test_weather_question_without_location_asks_before_searching(self):
        output = io.StringIO()
        with mock.patch.object(api.request, "urlopen") as urlopen:
            with contextlib.redirect_stdout(output):
                answer = chat.stream_chat(
                    [{"role": "user", "content": "Tá frio hoje né?"}],
                    "Prompt de teste",
                )

        urlopen.assert_not_called()
        self.assertIn("Para verificar o clima", answer)

    def test_weather_question_prompts_for_location_and_then_searches_in_terminal(self):
        class InteractiveInput(io.StringIO):
            def isatty(self):
                return True

        search_data = {
            "results": [{
                "title": "Previsão do tempo em Curitiba",
                "url": "https://www.climatempo.com.br/previsao-do-tempo/cidade/267/curitiba-pr",
                "content": "Temperatura mínima 12°C e máxima 20°C.",
                "published_date": "",
            }],
            "unresponsive_engines": [],
        }
        model_urlopen = mock.Mock(return_value=stream_response("A máxima será 20°C."))
        output = io.StringIO()
        with mock.patch.object(sys, "stdin", InteractiveInput("Curitiba\n")):
            with mock.patch.object(web, "search_web", return_value=search_data) as search:
                with mock.patch.object(web, "enrich_web_results"):
                    with mock.patch.object(api.request, "urlopen", side_effect=model_urlopen):
                        with contextlib.redirect_stdout(output):
                            chat.stream_chat(
                                [{"role": "user", "content": "Tá frio hoje né?"}],
                                "Prompt de teste",
                            )

        self.assertEqual(search.call_args.args[0], "temperatura atual e previsão do tempo hoje em Curitiba")
        self.assertIn("Sua resposta:", output.getvalue())

    def test_weather_followup_that_says_user_was_joking_does_not_trigger_search(self):
        class InteractiveInput(io.StringIO):
            def isatty(self):
                return True

        misleading_weather_results = {
            "results": [{
                "title": "Clima e Previsão do Tempo Hoje em Porto Alegre",
                "url": "https://www.climatempo.com.br/previsao-do-tempo/cidade/363/portoalegre-rs",
                "content": "Temperatura: 11°C a meia-noite, subindo para 18°C.",
                "published_date": "",
            }],
            "unresponsive_engines": [],
        }
        output = io.StringIO()
        user_input = "Não quero saber do tempo kk só tô quebrando o gelo\n"
        model_urlopen = mock.Mock()
        with mock.patch.object(sys, "stdin", InteractiveInput(user_input)):
            with mock.patch.object(web, "search_web", return_value=misleading_weather_results) as search:
                with mock.patch.object(api.request, "urlopen", side_effect=model_urlopen):
                    with contextlib.redirect_stdout(output):
                        answer = chat.stream_chat(
                            [{"role": "user", "content": "ta frio hoje né"}],
                            "Prompt de teste",
                        )

        search.assert_not_called()
        model_urlopen.assert_not_called()
        self.assertEqual(answer, "Ah, entendi, hehe. Levei a frase ao pé da letra.")
        self.assertNotIn("Porto Alegre", output.getvalue())
        self.assertNotIn("Pesquisando na web", output.getvalue())

    def test_generic_pending_web_search_can_be_cancelled_by_followup(self):
        class InteractiveInput(io.StringIO):
            def isatty(self):
                return True

        proposed_search = (
            web.WEB_SEARCH_MARKER
            + json.dumps({
                "query": "",
                "clarification": "Qual assunto você quer pesquisar?",
            })
        )
        model_urlopen = mock.Mock(side_effect=[
            stream_response(proposed_search),
            stream_response("Entendi, sem problema."),
        ])
        output = io.StringIO()
        with mock.patch.object(sys, "stdin", InteractiveInput("Não precisa pesquisar, deixa pra lá.\n")):
            with mock.patch.object(web, "search_web") as search:
                with mock.patch.object(api.request, "urlopen", side_effect=model_urlopen):
                    with contextlib.redirect_stdout(output):
                        answer = chat.stream_chat(
                            [{"role": "user", "content": "Pesquise algo para mim."}],
                            "Prompt de teste",
                        )

        search.assert_not_called()
        self.assertEqual(model_urlopen.call_count, 2, "deve haver resposta conversacional, sem executar a busca")
        self.assertIn("Entendi, sem problema", answer)
        self.assertNotIn("Pesquisando na web", output.getvalue())
        final_payload = json.loads(model_urlopen.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertIn("não use ferramentas nem pesquisa", final_payload["messages"][0]["content"])
        self.assertNotIn("Qual assunto você quer pesquisar?", str(final_payload["messages"]))
        self.assertIn("Não precisa pesquisar, deixa pra lá.", final_payload["messages"][-1]["content"])

    def test_explicit_no_search_alternative_is_answered_without_web_tools(self):
        class InteractiveInput(io.StringIO):
            def isatty(self):
                return True

        proposed_search = (
            web.WEB_SEARCH_MARKER
            + json.dumps({
                "query": "",
                "clarification": "Qual assunto você quer pesquisar?",
            })
        )
        model_urlopen = mock.Mock(side_effect=[
            stream_response(proposed_search),
            stream_response("A fotossíntese é o processo pelo qual plantas convertem luz em energia química."),
        ])
        with mock.patch.object(sys, "stdin", InteractiveInput("Não pesquise, mas me explique fotossíntese.\n")):
            with mock.patch.object(web, "search_web") as search:
                with mock.patch.object(api.request, "urlopen", side_effect=model_urlopen):
                    with contextlib.redirect_stdout(io.StringIO()):
                        answer = chat.stream_chat(
                            [{"role": "user", "content": "Pesquise sobre plantas."}],
                            "Prompt de teste",
                        )

        search.assert_not_called()
        self.assertEqual(model_urlopen.call_count, 2)
        self.assertIn("fotossíntese", answer)
        final_payload = json.loads(model_urlopen.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertIn("fotossíntese", final_payload["messages"][-1]["content"])
        self.assertNotIn("Ferramenta de pesquisa na web", final_payload["messages"][0]["content"])
        self.assertIn("não use ferramentas nem pesquisa", final_payload["messages"][0]["content"])

    def test_search_clarification_routes_cancel_continue_replan_and_unclear(self):
        self.assertEqual(
            web._classify_search_clarification(
                "não quero saber o clima, estou só quebrando o gelo hehe",
                expects_location=True,
            )[0],
            "cancel",
        )
        self.assertEqual(
            web._classify_search_clarification("Porto Alegre, RS", expects_location=True)[0],
            "continue",
        )
        self.assertEqual(
            web._classify_search_clarification(
                "Na verdade, pesquise a cotação do euro hoje.",
                expects_location=True,
            )[0],
            "replan",
        )
        self.assertEqual(
            web._classify_search_clarification("Não sei", expects_location=True)[0],
            "unclear",
        )
        self.assertEqual(
            web._classify_search_clarification("Na verdade, não quero pesquisar.")[0],
            "cancel",
        )
        self.assertEqual(
            web._classify_search_clarification(
                "Não quero pesquisar o clima; em vez disso, me diga a cotação do dólar.",
                expects_location=True,
            )[0],
            "replan",
        )
        self.assertEqual(
            web._classify_search_clarification(
                "Não pesquise, mas me explique fotossíntese.",
                expects_location=True,
            )[0],
            "respond",
        )

    def test_weather_search_rewrites_query_and_uses_only_weather_sources(self):
        search_data = {
            "results": [
                {
                    "title": "Meme: tá frio hoje né",
                    "url": "https://www.tiktok.com/@example/video/123",
                    "content": "Vídeo viral sobre frio.",
                },
                {
                    "title": "Previsão do tempo em Curitiba",
                    "url": "https://www.climatempo.com.br/previsao-do-tempo/cidade/267/curitiba-pr",
                    "content": "Temperatura mínima 12°C e máxima 20°C.",
                    "published_date": "",
                },
            ],
            "unresponsive_engines": [],
        }
        model_urlopen = mock.Mock(return_value=stream_response("A máxima será 20°C."))
        with mock.patch.object(web, "search_web", return_value=search_data) as search:
            with mock.patch.object(web, "enrich_web_results"):
                with mock.patch.object(api.request, "urlopen", side_effect=model_urlopen):
                    with contextlib.redirect_stdout(io.StringIO()):
                        chat.answer_with_web_search(
                            [{"role": "user", "content": "Tá frio em Curitiba hoje?"}],
                            "Prompt de teste",
                            "Tá frio em Curitiba hoje?",
                        )

        self.assertEqual(search.call_args.args[0], "temperatura atual e previsão do tempo hoje em Curitiba")
        model_payload = json.loads(model_urlopen.call_args.args[0].data.decode("utf-8"))
        research_context = model_payload["messages"][-1]["content"]
        self.assertIn("climatempo.com.br", research_context)
        self.assertNotIn("tiktok.com", research_context)

    def test_weather_search_with_only_irrelevant_results_does_not_answer_from_them(self):
        search_data = {
            "results": [{
                "title": "Tá frio hoje né - meme",
                "url": "https://www.tiktok.com/@example/video/123",
                "content": "Vídeo viral.",
            }],
            "unresponsive_engines": [],
        }
        output = io.StringIO()
        with mock.patch.object(web, "search_web", return_value=search_data):
            with mock.patch.object(api.request, "urlopen") as urlopen:
                with contextlib.redirect_stdout(output):
                    answer = chat.answer_with_web_search(
                        [{"role": "user", "content": "Tá frio em Curitiba hoje?"}],
                        "Prompt de teste",
                        "Tá frio em Curitiba hoje?",
                    )

        urlopen.assert_not_called()
        self.assertIn("Pesquisa inconclusiva", answer)
        self.assertNotIn("Fontes consultadas", output.getvalue())

    def test_generic_search_with_unrelated_results_does_not_answer_from_them(self):
        search_data = {
            "results": [{
                "title": "Vídeo engraçado do fim de semana",
                "url": "https://www.youtube.com/watch?v=example",
                "content": "Um meme popular nas redes sociais.",
                "published_date": "",
            }],
            "unresponsive_engines": [],
        }
        output = io.StringIO()
        with mock.patch.object(web, "search_web", return_value=search_data):
            with mock.patch.object(api.request, "urlopen") as urlopen:
                with contextlib.redirect_stdout(output):
                    answer = chat.answer_with_web_search(
                        [{"role": "user", "content": "qual a documentação atual do systemd"}],
                        "Prompt de teste",
                        "documentação atual do systemd",
                    )

        urlopen.assert_not_called()
        self.assertIn("Pesquisa inconclusiva", answer)
        self.assertNotIn("youtube.com", output.getvalue())

    def test_word_today_alone_does_not_force_a_web_search(self):
        urlopen = mock.Mock(return_value=stream_response("Que bom, espero que ajude."))

        self.run_cli(["lembre-me de revisar isso hoje"], urlopen=urlopen)

        self.assertEqual(urlopen.call_count, 1)
        payload = self.captured_payload(urlopen)
        self.assertEqual(payload["messages"][-1]["content"], "lembre-me de revisar isso hoje")

    def test_clear_default_memory_removes_only_default_session(self):
        sessions.save_session("default", [{"role": "user", "content": "padrão"}])
        sessions.save_session("outra", [{"role": "user", "content": "preservada"}])

        self.run_cli(["-c"])

        self.assertFalse(sessions.get_session_file("default").exists())
        self.assertTrue(sessions.get_session_file("outra").exists())

    def test_clear_named_session_removes_only_selected_session(self):
        sessions.save_session("memoriapersonalizada", [{"role": "user", "content": "apagar"}])
        sessions.save_session("default", [{"role": "user", "content": "preservar"}])

        self.run_cli(["-c", "memoriapersonalizada"])

        self.assertFalse(sessions.get_session_file("memoriapersonalizada").exists())
        self.assertTrue(sessions.get_session_file("default").exists())


if __name__ == "__main__":
    unittest.main()
