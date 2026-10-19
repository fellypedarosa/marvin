import contextlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("marvin_cli_under_test", str(ROOT / "mvn"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
marvin = importlib.util.module_from_spec(SPEC)
sys.modules[LOADER.name] = marvin
LOADER.exec_module(marvin)


def stream_response(content):
    event = {"choices": [{"delta": {"content": content}}]}
    body = f"data: {json.dumps(event)}\n\ndata: [DONE]\n".encode("utf-8")
    return io.BytesIO(body)


class MarvinCLITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sessions_dir = Path(self.temp_dir.name) / "sessions"
        self.sessions_patcher = mock.patch.object(marvin, "SESSIONS_DIR", self.sessions_dir)
        self.sessions_patcher.start()
        self.addCleanup(self.sessions_patcher.stop)
        self.addCleanup(self.temp_dir.cleanup)

    def run_cli(self, arguments, urlopen=None):
        stdout = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(marvin, "get_system_prompt", return_value="Prompt de teste"))
            stack.enter_context(mock.patch.object(sys, "argv", ["mvn", *arguments]))
            stack.enter_context(mock.patch.object(sys, "stdin", io.StringIO("")))
            if urlopen is not None:
                stack.enter_context(mock.patch.object(marvin.request, "urlopen", side_effect=urlopen))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            marvin.main()
        return stdout.getvalue()

    def captured_payload(self, urlopen_mock, call_index=0):
        request = urlopen_mock.call_args_list[call_index].args[0]
        return json.loads(request.data.decode("utf-8"))

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
        http_error = HTTPError(marvin.API_URL, 500, "Internal Server Error", {}, io.BytesIO(body))
        stderr = io.StringIO()

        with mock.patch.object(marvin.request, "urlopen", side_effect=http_error):
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    marvin.stream_chat([{"role": "user", "content": "oi"}], "Prompt de teste")

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("[Erro do modelo]", stderr.getvalue())
        self.assertIn("System message must be at the beginning", stderr.getvalue())
        self.assertNotIn("[Erro de conexao]", stderr.getvalue())

    def test_default_memory_question_loads_and_saves_history(self):
        marvin.save_session("default", [
            {"role": "user", "content": "Lembre-se do nome Ada."},
            {"role": "assistant", "content": "Vou lembrar."},
        ])
        urlopen = mock.Mock(return_value=stream_response("Ada."))

        self.run_cli(["-m", "qual nome mencionei?"], urlopen=urlopen)

        payload = self.captured_payload(urlopen)
        contents = [message["content"] for message in payload["messages"]]
        self.assertIn("Lembre-se do nome Ada.", contents)
        self.assertEqual(contents[-1], "qual nome mencionei?")
        saved = marvin.load_session("default")
        self.assertEqual(len(saved), 4)
        self.assertEqual(saved[-1]["content"], "Ada.")

    def test_named_memory_question_loads_and_saves_its_own_history(self):
        marvin.save_session("memoriapersonalizada", [
            {"role": "user", "content": "O projeto se chama Atlas."},
            {"role": "assistant", "content": "Entendido."},
        ])
        urlopen = mock.Mock(return_value=stream_response("Atlas."))

        self.run_cli(["-s", "memoriapersonalizada", "qual o nome do projeto?"], urlopen=urlopen)

        payload = self.captured_payload(urlopen)
        self.assertEqual(payload["messages"][-3]["content"], "O projeto se chama Atlas.")
        saved = marvin.load_session("memoriapersonalizada")
        self.assertEqual(len(saved), 4)
        self.assertEqual(saved[-1]["content"], "Atlas.")
        self.assertFalse(marvin.get_session_file("default").exists())

    def test_action_mode_confirms_and_executes_proposed_command(self):
        model_replies = iter([
            "<<<ACTION>>>\nComando: printf seguro\nFinalidade: verificar o fluxo de teste",
            "<<<FINAL_ANSWER>>>\nComando concluído.",
            "<<<TASK_COMPLETE>>>",
        ])
        completed = subprocess.CompletedProcess("printf seguro", 0, stdout="seguro\n", stderr="")
        output = io.StringIO()
        with mock.patch.object(marvin, "SESSIONS_DIR", self.sessions_dir):
            with mock.patch.object(marvin, "get_system_prompt", return_value="Prompt de teste"):
                with mock.patch.object(sys, "argv", ["mvn", "-x", "confirme uma operação segura"]):
                    with mock.patch.object(sys, "stdin", io.StringIO("")):
                        with mock.patch.object(marvin, "query_messages_raw", side_effect=lambda _messages: next(model_replies)):
                            with mock.patch("builtins.input", return_value="s"):
                                with mock.patch.object(marvin.subprocess, "run", return_value=completed) as run_command:
                                    with contextlib.redirect_stdout(output):
                                        marvin.main()

        run_command.assert_called_once()
        self.assertEqual(run_command.call_args.args[0], "printf seguro")
        self.assertIn("seguro", output.getvalue())
        self.assertIn("Comando concluído.", output.getvalue())

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

        with mock.patch.object(marvin, "MAX_ACTION_TURNS", 2):
            with mock.patch.object(marvin, "get_system_prompt", return_value="Prompt de teste"):
                with mock.patch.object(sys, "argv", ["mvn", "-x", "crie /tmp/mvn"]):
                    with mock.patch.object(sys, "stdin", io.StringIO("")):
                        with mock.patch.object(marvin, "query_messages_raw", query):
                            with mock.patch("builtins.input", side_effect=answer_confirmation):
                                with mock.patch.object(marvin.subprocess, "run", return_value=completed) as run_command:
                                    with contextlib.redirect_stdout(output):
                                        marvin.main()

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

        with mock.patch.object(marvin, "get_system_prompt", return_value="Prompt de teste"):
            with mock.patch.object(sys, "argv", ["mvn", "-x", "crie /tmp/mvn"]):
                with mock.patch.object(sys, "stdin", io.StringIO("")):
                    with mock.patch.object(marvin, "query_messages_raw", query):
                        with mock.patch("builtins.input", side_effect=interrupt_at_guidance):
                            with mock.patch.object(marvin.subprocess, "run") as run_command:
                                with contextlib.redirect_stdout(output):
                                    marvin.main()

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

        with mock.patch.object(marvin, "MAX_ACTION_TURNS", 1):
            with mock.patch.object(marvin, "get_system_prompt", return_value="Prompt de teste"):
                with mock.patch.object(sys, "argv", ["mvn", "-x", "clone de teste"]):
                    with mock.patch.object(sys, "stdin", io.StringIO("")):
                        with mock.patch.object(marvin, "query_messages_raw", query):
                            with mock.patch("builtins.input", side_effect=answer_confirmation):
                                with mock.patch.object(marvin.subprocess, "run", side_effect=completed) as run_command:
                                    with contextlib.redirect_stdout(output):
                                        marvin.main()

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

        with mock.patch.object(marvin, "MAX_ACTION_TURNS", 1):
            with mock.patch.object(marvin, "get_system_prompt", return_value="Prompt de teste"):
                with mock.patch.object(sys, "argv", ["mvn", "-x", "investigue uma falha"]):
                    with mock.patch.object(sys, "stdin", io.StringIO("")):
                        with mock.patch.object(marvin, "query_messages_raw", query):
                            with mock.patch("builtins.input", side_effect=answer_confirmation):
                                with mock.patch.object(marvin.subprocess, "run", return_value=completed) as run_command:
                                    with contextlib.redirect_stdout(output):
                                        marvin.main()

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

        with mock.patch.object(marvin, "MAX_ACTION_TURNS", 4):
            with mock.patch.object(marvin, "get_system_prompt", return_value="Prompt de teste"):
                with mock.patch.object(sys, "argv", ["mvn", "-x", "clone o repositorio"]):
                    with mock.patch.object(sys, "stdin", io.StringIO("")):
                        with mock.patch.object(marvin, "query_messages_raw", query):
                            with mock.patch("builtins.input", side_effect=["s", "s", "s", "s", "encerrar"]):
                                with mock.patch.object(marvin.subprocess, "run", side_effect=completed) as run_command:
                                    with contextlib.redirect_stdout(output):
                                        marvin.main()

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

        with mock.patch.object(marvin, "enrich_web_results"):
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
        with mock.patch.object(marvin.request, "urlopen") as urlopen:
            with contextlib.redirect_stdout(output):
                answer = marvin.stream_chat(
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
            with mock.patch.object(marvin, "search_web", return_value=search_data) as search:
                with mock.patch.object(marvin, "enrich_web_results"):
                    with mock.patch.object(marvin.request, "urlopen", side_effect=model_urlopen):
                        with contextlib.redirect_stdout(output):
                            marvin.stream_chat(
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
            with mock.patch.object(marvin, "search_web", return_value=misleading_weather_results) as search:
                with mock.patch.object(marvin.request, "urlopen", side_effect=model_urlopen):
                    with contextlib.redirect_stdout(output):
                        answer = marvin.stream_chat(
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
            marvin.WEB_SEARCH_MARKER
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
            with mock.patch.object(marvin, "search_web") as search:
                with mock.patch.object(marvin.request, "urlopen", side_effect=model_urlopen):
                    with contextlib.redirect_stdout(output):
                        answer = marvin.stream_chat(
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
            marvin.WEB_SEARCH_MARKER
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
            with mock.patch.object(marvin, "search_web") as search:
                with mock.patch.object(marvin.request, "urlopen", side_effect=model_urlopen):
                    with contextlib.redirect_stdout(io.StringIO()):
                        answer = marvin.stream_chat(
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
            marvin._classify_search_clarification(
                "não quero saber o clima, estou só quebrando o gelo hehe",
                expects_location=True,
            )[0],
            "cancel",
        )
        self.assertEqual(
            marvin._classify_search_clarification("Porto Alegre, RS", expects_location=True)[0],
            "continue",
        )
        self.assertEqual(
            marvin._classify_search_clarification(
                "Na verdade, pesquise a cotação do euro hoje.",
                expects_location=True,
            )[0],
            "replan",
        )
        self.assertEqual(
            marvin._classify_search_clarification("Não sei", expects_location=True)[0],
            "unclear",
        )
        self.assertEqual(
            marvin._classify_search_clarification("Na verdade, não quero pesquisar.")[0],
            "cancel",
        )
        self.assertEqual(
            marvin._classify_search_clarification(
                "Não quero pesquisar o clima; em vez disso, me diga a cotação do dólar.",
                expects_location=True,
            )[0],
            "replan",
        )
        self.assertEqual(
            marvin._classify_search_clarification(
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
        with mock.patch.object(marvin, "search_web", return_value=search_data) as search:
            with mock.patch.object(marvin, "enrich_web_results"):
                with mock.patch.object(marvin.request, "urlopen", side_effect=model_urlopen):
                    with contextlib.redirect_stdout(io.StringIO()):
                        marvin.answer_with_web_search(
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
        with mock.patch.object(marvin, "search_web", return_value=search_data):
            with mock.patch.object(marvin.request, "urlopen") as urlopen:
                with contextlib.redirect_stdout(output):
                    answer = marvin.answer_with_web_search(
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
        with mock.patch.object(marvin, "search_web", return_value=search_data):
            with mock.patch.object(marvin.request, "urlopen") as urlopen:
                with contextlib.redirect_stdout(output):
                    answer = marvin.answer_with_web_search(
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
        marvin.save_session("default", [{"role": "user", "content": "padrão"}])
        marvin.save_session("outra", [{"role": "user", "content": "preservada"}])

        self.run_cli(["-c"])

        self.assertFalse(marvin.get_session_file("default").exists())
        self.assertTrue(marvin.get_session_file("outra").exists())

    def test_clear_named_session_removes_only_selected_session(self):
        marvin.save_session("memoriapersonalizada", [{"role": "user", "content": "apagar"}])
        marvin.save_session("default", [{"role": "user", "content": "preservar"}])

        self.run_cli(["-c", "memoriapersonalizada"])

        self.assertFalse(marvin.get_session_file("memoriapersonalizada").exists())
        self.assertTrue(marvin.get_session_file("default").exists())


if __name__ == "__main__":
    unittest.main()
