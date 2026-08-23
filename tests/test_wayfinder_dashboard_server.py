from __future__ import annotations

from http.client import HTTPConnection
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = ROOT / "skills" / "wayfinder" / "scripts" / "wayfinder_server.py"
CLI_PATH = ROOT / "skills" / "wayfinder" / "scripts" / "wayfinder.py"


def load_server_module():
    name = "_wayfinder_dashboard_server_tests"
    spec = importlib.util.spec_from_file_location(name, SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load dashboard server")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SERVER = load_server_module()


class FakeIntakeError(RuntimeError):
    def __init__(self, code: str, http_status: int) -> None:
        super().__init__("safe fake intake failure")
        self.code = code
        self.http_status = http_status


class WayfinderDashboardServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capability = "test-capability"
        self.csrf = "test-csrf-proof"
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.state = {
            "project": {"title": "Test route"},
            "intake": {
                "revision": 7,
                "current_question": {"id": "Q-SW-001", "answer_type": "choice"},
            },
        }

    def start_server(self, *, interactive: bool = True, choice=None, answer=None) -> None:
        def record_choice(**kwargs):
            self.calls.append(("choice", kwargs))
            self.state = {
                **self.state,
                "intake": {"revision": 8, "current_question": None},
                "selected": kwargs["choice"],
            }
            return self.state

        def record_answer(**kwargs):
            self.calls.append(("answer", kwargs))
            self.state = {
                **self.state,
                "intake": {"revision": 8, "current_question": None},
                "answer": kwargs["answer"],
            }
            return self.state

        handler = SERVER._handler(
            lambda: self.state,
            SERVER.dashboard_assets(),
            True,
            self.capability,
            decision_recording=interactive,
            csrf_token=self.csrf if interactive else "",
            choice_recorder=choice or record_choice,
            answer_recorder=answer or record_answer,
            intake_error=FakeIntakeError,
        )
        self.server = SERVER.DashboardServer((SERVER.LOOPBACK, 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = int(self.server.server_address[1])
        self.origin = f"http://{SERVER.LOOPBACK}:{self.port}"
        self.base = f"/{self.capability}"

    def tearDown(self) -> None:
        server = getattr(self, "server", None)
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = getattr(self, "thread", None)
        if thread is not None:
            thread.join(timeout=2)

    def request(self, method: str, route: str, *, body: bytes | None = None, headers=None):
        connection = HTTPConnection(SERVER.LOOPBACK, self.port, timeout=3)
        request_headers = dict(headers or {})
        connection.request(method, route, body=body, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        result = (response.status, dict(response.getheaders()), payload)
        connection.close()
        return result

    def write_headers(self, revision: int = 7) -> dict[str, str]:
        return {
            "Origin": self.origin,
            "X-Wayfinder-CSRF": self.csrf,
            "Content-Type": "application/json",
            "If-Match": f'"{revision}"',
        }

    def test_session_is_explicit_and_csrf_exists_only_in_interactive_mode(self) -> None:
        self.start_server(interactive=True)
        status, headers, body = self.request("GET", f"{self.base}/api/session")
        self.assertEqual(200, status)
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual("DENY", headers["X-Frame-Options"])
        self.assertEqual(
            {
                "csrf_token": self.csrf,
                "mode": "decision-recording",
                "recordable_current_question": True,
            },
            json.loads(body),
        )
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        del self.server
        del self.thread

        self.start_server(interactive=False)
        status, _, body = self.request("GET", f"{self.base}/api/session")
        self.assertEqual(200, status)
        self.assertEqual(
            {"mode": "read-only", "recordable_current_question": False},
            json.loads(body),
        )

    def test_browser_auto_open_is_explicit_testable_and_fail_soft(self) -> None:
        url = "http://127.0.0.1:43210/test-capability/"
        opener = mock.Mock(return_value=True)
        self.assertTrue(SERVER.open_dashboard_browser(url, opener=opener))
        opener.assert_called_once_with(url)

        self.assertFalse(SERVER.open_dashboard_browser(url, opener=mock.Mock(return_value=False)))
        self.assertFalse(
            SERVER.open_dashboard_browser(
                url,
                opener=mock.Mock(side_effect=RuntimeError("browser unavailable")),
            )
        )

    def test_failed_auto_open_keeps_serving_and_prints_a_url_fallback(self) -> None:
        project = Path("/tmp/wayfinder-browser-test")
        fake_server = mock.Mock()
        fake_server.server_address = (SERVER.LOOPBACK, 43210)
        fake_server.wayfinder_capability = "test-capability"
        fake_server.wayfinder_project_root = project
        fake_server.wayfinder_effort_dir = project / ".codex" / "wayfinder" / "efforts" / "demo"
        opener = mock.Mock()
        with (
            mock.patch.object(SERVER, "make_server", return_value=fake_server),
            mock.patch.object(SERVER, "open_dashboard_browser", return_value=False) as open_browser,
            mock.patch("builtins.print") as print_output,
        ):
            SERVER.serve(project, decision_recording=True, open_browser=True, browser_opener=opener)

        url = "http://127.0.0.1:43210/test-capability/"
        open_browser.assert_called_once_with(url, opener=opener)
        fake_server.serve_forever.assert_called_once_with(poll_interval=0.25)
        fake_server.server_close.assert_called_once_with()
        self.assertTrue(any(url in str(call) for call in print_output.call_args_list))
        self.assertTrue(any("did not open automatically" in str(call) for call in print_output.call_args_list))

    def test_valid_choice_uses_fixed_identity_and_persists_in_refreshed_state(self) -> None:
        self.start_server()
        body = json.dumps({"decision_id": "D-001", "choice": "opt-a", "expected_revision": 7}).encode()
        status, _, response = self.request(
            "POST", f"{self.base}/api/intake/choice", body=body, headers=self.write_headers()
        )
        self.assertEqual(200, status)
        self.assertEqual("opt-a", json.loads(response)["state"]["selected"])
        self.assertEqual(
            [("choice", {"decision_id": "D-001", "choice": "opt-a", "expected_revision": 7, "actor": "User", "source": "DASHBOARD"})],
            self.calls,
        )
        status, _, refreshed = self.request("GET", f"{self.base}/api/state")
        self.assertEqual(200, status)
        self.assertEqual("opt-a", json.loads(refreshed)["selected"])

    def test_real_intake_choice_persists_through_the_http_adapter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wayfinder-dashboard-http-") as directory:
            project = Path(directory)
            subprocess.run(
                [
                    sys.executable,
                    str(CLI_PATH),
                    "init",
                    "--root",
                    str(project),
                    "--slug",
                    "demo",
                    "--destination",
                    "Plan demo",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(CLI_PATH),
                    "intake",
                    "start",
                    "--root",
                    str(project),
                    "--intent",
                    "Build a small web app",
                    "--json",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            self.server = SERVER.make_server(project, port=0, quiet=True, decision_recording=True)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.port = int(self.server.server_address[1])
            self.origin = f"http://{SERVER.LOOPBACK}:{self.port}"
            self.base = f"/{self.server.wayfinder_capability}"

            state_status, _, state_body = self.request("GET", f"{self.base}/api/state")
            session_status, _, session_body = self.request("GET", f"{self.base}/api/session")
            self.assertEqual(200, state_status)
            self.assertEqual(200, session_status)
            before = json.loads(state_body)
            session = json.loads(session_body)
            question = before["intake"]["current_question"]
            self.assertEqual("Q-001", question["id"])
            self.assertEqual("D-001", question["decision_id"])
            self.assertEqual(1, question["expected_revision"])
            self.assertTrue(session["recordable_current_question"])

            body = json.dumps(
                {
                    "decision_id": question["decision_id"],
                    "choice": "SOFTWARE",
                    "expected_revision": question["expected_revision"],
                }
            ).encode()
            status, _, response = self.request(
                "POST",
                f"{self.base}/api/intake/choice",
                body=body,
                headers={
                    "Origin": self.origin,
                    "X-Wayfinder-CSRF": session["csrf_token"],
                    "Content-Type": "application/json",
                    "If-Match": '"1"',
                },
            )
            self.assertEqual(200, status)
            recorded = json.loads(response)["state"]
            self.assertEqual(2, recorded["intake"]["revision"])
            self.assertEqual("SOFTWARE", recorded["intake"]["domain"]["selected"])
            self.assertEqual("RESOLVED", recorded["intake"]["decision_bindings"][0]["status"])
            self.assertEqual("E-001", recorded["intake"]["decision_bindings"][0]["evidence_id"])
            self.assertEqual("RESOLVED", next(node for node in recorded["nodes"] if node["id"] == "D-001")["status"])
            self.assertEqual("E-001", next(item for item in recorded["evidence"] if item["id"] == "E-001")["id"])

            effort = project / ".codex" / "wayfinder" / "efforts" / "demo"
            persisted_intake = json.loads((effort / "INTAKE.json").read_text(encoding="utf-8"))
            persisted_manifest = json.loads((effort / "EFFORT.json").read_text(encoding="utf-8"))
            persisted_decision = (effort / "decisions" / "D-001.md").read_text(encoding="utf-8")
            persisted_evidence = (effort / "evidence" / "E-001.md").read_text(encoding="utf-8")
            self.assertEqual(2, persisted_intake["revision"])
            self.assertEqual("DASHBOARD", persisted_intake["receipts"][-1]["source"])
            self.assertEqual("SOFTWARE", persisted_intake["domain"]["selected"])
            self.assertEqual("RESOLVED", persisted_manifest["decisions"][0]["status"])
            self.assertEqual("E-001", persisted_manifest["evidence"][0]["id"])
            self.assertIn("- **Revision:** 1", persisted_decision)
            self.assertIn("explicitly selected Software", persisted_decision)
            self.assertIn("explicitly selected Software", persisted_evidence)
            self.assertEqual(2, recorded["implementation_baseline"]["intake_revision"])
            self.assertEqual(
                [{"id": "D-001", "revision": 1, "status": "RESOLVED"}],
                recorded["implementation_baseline"]["applicable_decisions"],
            )

            refresh_status, _, refresh_body = self.request("GET", f"{self.base}/api/state")
            self.assertEqual(200, refresh_status)
            refreshed = json.loads(refresh_body)
            self.assertEqual(2, refreshed["intake"]["revision"])
            self.assertEqual("SOFTWARE", refreshed["intake"]["domain"]["selected"])

    def test_valid_text_answer_uses_only_current_question_contract(self) -> None:
        self.state["intake"]["current_question"] = {"id": "Q-002", "answer_type": "fact"}
        self.start_server()
        session_status, _, session_body = self.request("GET", f"{self.base}/api/session")
        self.assertEqual(200, session_status)
        self.assertTrue(json.loads(session_body)["recordable_current_question"])
        body = json.dumps({"question_id": "Q-002", "answer": "A bounded answer", "expected_revision": 7}).encode()
        status, _, response = self.request(
            "POST", f"{self.base}/api/intake/answer", body=body, headers=self.write_headers()
        )
        self.assertEqual(200, status)
        self.assertEqual("A bounded answer", json.loads(response)["state"]["answer"])
        self.assertEqual("User", self.calls[0][1]["actor"])
        self.assertEqual("DASHBOARD", self.calls[0][1]["source"])

    def test_read_only_mode_rejects_the_narrow_write_routes(self) -> None:
        self.start_server(interactive=False)
        body = json.dumps({"decision_id": "D-001", "choice": "opt-a", "expected_revision": 7}).encode()
        status, _, response = self.request(
            "POST", f"{self.base}/api/intake/choice", body=body, headers={
                "Origin": self.origin,
                "Content-Type": "application/json",
                "If-Match": '"7"',
            }
        )
        self.assertEqual(403, status)
        self.assertEqual("READ_ONLY", json.loads(response)["code"])
        self.assertEqual([], self.calls)

    def test_every_non_post_verb_is_non_mutating_even_with_complete_write_proof(self) -> None:
        self.start_server()
        body = json.dumps({"decision_id": "D-001", "choice": "opt-a", "expected_revision": 7}).encode()
        for method in ("PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"):
            with self.subTest(method=method):
                status, headers, _ = self.request(
                    method, f"{self.base}/api/intake/choice", body=body, headers=self.write_headers()
                )
                self.assertEqual(405, status)
                self.assertEqual("POST", headers["Allow"])
        self.assertEqual([], self.calls)

    def test_origin_csrf_host_and_capability_are_all_required(self) -> None:
        self.start_server()
        body = json.dumps({"decision_id": "D-001", "choice": "opt-a", "expected_revision": 7}).encode()
        cases = [
            ({key: value for key, value in self.write_headers().items() if key != "Origin"}, f"{self.base}/api/intake/choice", 403),
            ({**self.write_headers(), "Origin": "null"}, f"{self.base}/api/intake/choice", 403),
            ({**self.write_headers(), "X-Wayfinder-CSRF": "wrong"}, f"{self.base}/api/intake/choice", 403),
            ({**self.write_headers(), "Host": "attacker.invalid"}, f"{self.base}/api/intake/choice", 421),
            (self.write_headers(), "/wrong-capability/api/intake/choice", 404),
        ]
        for headers, route, expected in cases:
            with self.subTest(expected=expected, route=route):
                status, _, _ = self.request("POST", route, body=body, headers=headers)
                self.assertEqual(expected, status)
        self.assertEqual([], self.calls)

    def test_exact_content_type_body_cap_revision_and_field_set_are_enforced(self) -> None:
        self.start_server()
        valid = {"decision_id": "D-001", "choice": "opt-a", "expected_revision": 7}
        cases = [
            (json.dumps(valid).encode(), {**self.write_headers(), "Content-Type": "application/json; charset=utf-8"}, 415),
            (b"x" * (SERVER.MAX_REQUEST_BYTES + 1), self.write_headers(), 413),
            (json.dumps({**valid, "expected_revision": True}).encode(), {**self.write_headers(), "If-Match": '"True"'}, 422),
            (json.dumps({**valid, "path": "arbitrary"}).encode(), self.write_headers(), 422),
            (json.dumps(valid).encode(), {**self.write_headers(), "If-Match": '"6"'}, 422),
        ]
        for body, headers, expected in cases:
            with self.subTest(expected=expected, body_length=len(body)):
                status, _, _ = self.request("POST", f"{self.base}/api/intake/choice", body=body, headers=headers)
                self.assertEqual(expected, status)
        self.assertEqual([], self.calls)

    def test_duplicate_keys_nonfinite_values_and_excessive_nesting_are_rejected(self) -> None:
        self.start_server()
        bodies = [
            b'{"decision_id":"D-001","choice":"a","choice":"b","expected_revision":7}',
            b'{"decision_id":"D-001","choice":NaN,"expected_revision":7}',
            b'{"decision_id":"D-001","choice":"a","expected_revision":1e999}',
            (b"[" * 1_500) + b"0" + (b"]" * 1_500),
        ]
        for body in bodies:
            with self.subTest(prefix=body[:24]):
                status, _, response = self.request(
                    "POST", f"{self.base}/api/intake/choice", body=body, headers=self.write_headers()
                )
                self.assertEqual(400, status)
                self.assertEqual("REQUEST_INVALID", json.loads(response)["code"])
        self.assertEqual([], self.calls)

    def test_intake_conflict_and_validation_errors_are_non_reflective(self) -> None:
        def conflict(**_kwargs):
            raise FakeIntakeError("INTAKE_REVISION_CONFLICT", 409)

        self.start_server(choice=conflict)
        body = json.dumps({"decision_id": "D-001", "choice": "secret-option", "expected_revision": 7}).encode()
        status, _, response = self.request(
            "POST", f"{self.base}/api/intake/choice", body=body, headers=self.write_headers()
        )
        self.assertEqual(409, status)
        decoded = response.decode()
        self.assertIn("INTAKE_REVISION_CONFLICT", decoded)
        self.assertNotIn("secret-option", decoded)

    def test_unknown_post_route_and_query_variant_never_reach_recorders(self) -> None:
        self.start_server()
        body = json.dumps({"decision_id": "D-001", "choice": "opt-a", "expected_revision": 7}).encode()
        status, _, _ = self.request("POST", f"{self.base}/api/state", body=body, headers=self.write_headers())
        self.assertEqual(405, status)
        status, _, _ = self.request("POST", f"{self.base}/api/intake/choice?alternate=true", body=body, headers=self.write_headers())
        self.assertEqual(404, status)
        self.assertEqual([], self.calls)


if __name__ == "__main__":
    unittest.main()
