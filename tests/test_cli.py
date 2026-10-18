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

    def test_web_question_searches_and_sends_results_to_model(self):
        search_response = io.BytesIO(json.dumps({
            "results": [{
                "title": "Fonte de teste",
                "url": "https://example.org/guia",
                "content": "Resumo encontrado.",
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
        self.assertIn("Resumo encontrado.", model_payload["messages"][-1]["content"])
        self.assertIn("Fontes consultadas:", output)
        self.assertIn("https://example.org/guia", output)

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
