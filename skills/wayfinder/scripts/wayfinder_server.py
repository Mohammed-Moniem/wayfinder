#!/usr/bin/env python3
"""Loopback-only HTTP server for the local Wayfinder dashboard."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import math
import mimetypes
from pathlib import Path
import re
import secrets
import sys
from typing import Any, Callable
import unicodedata
from urllib.parse import unquote, urlsplit


def _load_state_module() -> Any:
    name = "_wayfinder_state_v3"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).resolve(strict=True).with_name("wayfinder_state.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the Wayfinder state engine.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _load_intake_module() -> Any:
    name = "_wayfinder_intake_v3"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).resolve(strict=True).with_name("wayfinder_intake.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the Wayfinder intake engine.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


_STATE = _load_state_module()
WayfinderError = _STATE.WayfinderError
build_state = _STATE.build_state
resolve_effort = _STATE.resolve_effort
state_json = _STATE.state_json


LOOPBACK = "127.0.0.1"
MAX_REQUEST_BYTES = 8_192
DECISION_ID = re.compile(r"^D-\d{3,}$")
QUESTION_ID = re.compile(r"^Q-(?:\d{3}|[A-Z]{2,4}-\d{3})$")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def terminal_safe_text(value: object, *, allow_newlines: bool = False) -> str:
    """Render untrusted text without terminal controls or bidi formatting.

    Newlines are allowed only when the caller owns the surrounding multiline
    layout. Every other Unicode control/format character is made visible.
    """
    rendered: list[str] = []
    for character in str(value):
        if character == "\n" and allow_newlines:
            rendered.append(character)
            continue
        codepoint = ord(character)
        if (
            codepoint < 0x20
            or 0x7F <= codepoint <= 0x9F
            or unicodedata.category(character) in {"Cf", "Zl", "Zp", "Cs"}
        ):
            width = 4 if codepoint <= 0xFFFF else 8
            rendered.append(f"\\u{codepoint:0{width}x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def dashboard_assets() -> Path:
    return Path(__file__).resolve(strict=True).parent.parent / "assets" / "dashboard"


def _within(parent: Path, child: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _handler(
    state_factory: Callable[[], dict[str, Any]],
    assets: Path,
    quiet: bool,
    capability: str,
    *,
    decision_recording: bool = False,
    csrf_token: str = "",
    choice_recorder: Callable[..., dict[str, Any]] | None = None,
    answer_recorder: Callable[..., dict[str, Any]] | None = None,
    intake_error: type[Exception] = RuntimeError,
) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "WayfinderDashboard/3"
        sys_version = ""

        def _emit_log(self, message: str) -> None:
            if not quiet:
                BaseHTTPRequestHandler.log_message(self, "%s", terminal_safe_text(message))

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            """Log only a bounded method/status summary, never a request target."""
            del size
            raw_method = getattr(self, "command", "")
            method = raw_method if isinstance(raw_method, str) and raw_method.isascii() and raw_method.isalpha() else "UNKNOWN"
            method = method.upper()[:16] or "UNKNOWN"
            status = str(code)
            if len(status) > 3 or not status.isascii() or not status.isdigit():
                status = "-"
            self._emit_log(f"{method} {status}")

        def log_error(self, format: str, *args: Any) -> None:
            """Keep parser/transport notes generic and request-target free."""
            del format, args
            self._emit_log("request handling error")

        def log_message(self, format: str, *args: Any) -> None:
            """Safe fallback for stdlib logging paths not covered above."""
            del format, args
            self._emit_log("request handled")

        def send_error(
            self,
            code: int,
            message: str | None = None,
            explain: str | None = None,
        ) -> None:
            """Keep BaseHTTPRequestHandler fallbacks inside our security boundary."""
            del message, explain
            if int(code) == 501:
                # Unknown verbs otherwise receive BaseHTTPRequestHandler's
                # headerless 501 page before Host validation can run.
                self._method_not_allowed()
                return
            self._respond(
                int(code),
                b"Request rejected\n",
                "text/plain; charset=utf-8",
            )

        def _headers(self, status: int, content_type: str, length: int, *, allow: str | None = None) -> None:
            self.send_response(status)
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            if allow:
                self.send_header("Allow", allow)
            self.end_headers()

        def _respond(self, status: int, body: bytes, content_type: str, *, head: bool = False, allow: str | None = None) -> None:
            self._headers(status, content_type, len(body), allow=allow)
            if not head:
                self.wfile.write(body)

        def _not_found(self, *, head: bool = False) -> None:
            body = b"Not found\n"
            self._respond(404, body, "text/plain; charset=utf-8", head=head)

        def _asset_unavailable(self, *, head: bool = False) -> None:
            body = b"Wayfinder dashboard asset is unavailable\n"
            self._respond(500, body, "text/plain; charset=utf-8", head=head)

        def _json_response(self, status: int, payload: dict[str, Any], *, head: bool = False) -> None:
            try:
                body = json.dumps(
                    payload,
                    sort_keys=True,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8") + b"\n"
            except (TypeError, ValueError):
                body = b'{"error":"Wayfinder response is unavailable"}\n'
                status = 500
            self._respond(status, body, "application/json; charset=utf-8", head=head)

        def _host_allowed(self) -> bool:
            port = int(self.server.server_address[1])
            expected = LOOPBACK if port == 80 else f"{LOOPBACK}:{port}"
            return self.headers.get_all("Host", []) == [expected]

        def _reject_host(self, *, head: bool = False) -> None:
            self._respond(
                421,
                b"Misdirected request\n",
                "text/plain; charset=utf-8",
                head=head,
            )

        def _expected_origin(self) -> str:
            port = int(self.server.server_address[1])
            return f"http://{LOOPBACK}" if port == 80 else f"http://{LOOPBACK}:{port}"

        def _write_proof_allowed(self) -> bool:
            origins = self.headers.get_all("Origin", [])
            csrf_values = self.headers.get_all("X-Wayfinder-CSRF", [])
            return (
                origins == [self._expected_origin()]
                and csrf_values == [csrf_token]
                and bool(csrf_token)
                and secrets.compare_digest(csrf_values[0], csrf_token)
            )

        def _authorized_path(self) -> str | None:
            """Return the capability-stripped route or hide the server."""
            parsed = urlsplit(self.path)
            try:
                decoded = unquote(parsed.path, errors="strict")
            except (UnicodeError, ValueError):
                return None
            if "\\" in decoded or any(
                ord(character) < 0x20
                or 0x7F <= ord(character) <= 0x9F
                or unicodedata.category(character) in {"Cf", "Zl", "Zp", "Cs"}
                for character in decoded
            ):
                return None
            prefix = f"/{capability}"
            if not decoded.startswith(prefix + "/"):
                return None
            return decoded[len(prefix):]

        def _method_not_allowed(self) -> None:
            if not self._host_allowed():
                self._reject_host()
                return
            decoded = self._authorized_path()
            if decoded is None:
                self._not_found()
                return
            allow = "POST" if decoded in {"/api/intake/choice", "/api/intake/answer"} else "GET, HEAD"
            self._respond(
                405,
                b"Method not allowed\n",
                "text/plain; charset=utf-8",
                allow=allow,
            )

        def _serve(self, *, head: bool) -> None:
            if not self._host_allowed():
                self._reject_host(head=head)
                return
            decoded = self._authorized_path()
            if decoded is None:
                self._not_found(head=head)
                return
            parts = Path(decoded).parts
            if ".." in parts or "." in parts:
                self._not_found(head=head)
                return

            if decoded == "/api/state":
                try:
                    body = state_json(state_factory()).encode("utf-8")
                except Exception:
                    payload = json.dumps(
                        {"error": "Wayfinder state is unavailable"},
                        sort_keys=True,
                    ).encode("utf-8") + b"\n"
                    self._respond(500, payload, "application/json; charset=utf-8", head=head)
                    return
                self._respond(200, body, "application/json; charset=utf-8", head=head)
                return
            if decoded == "/api/session":
                payload: dict[str, Any] = {
                    "mode": "decision-recording" if decision_recording else "read-only",
                    "recordable_current_question": False,
                }
                if decision_recording:
                    payload["csrf_token"] = csrf_token
                    try:
                        current_state = state_factory()
                        intake_state = current_state.get("intake", {})
                        current_question = intake_state.get("current_question", {}) if isinstance(intake_state, dict) else {}
                        revision = intake_state.get("revision") if isinstance(intake_state, dict) else None
                        answer_type = current_question.get("answer_type") if isinstance(current_question, dict) else None
                        payload["recordable_current_question"] = (
                            isinstance(revision, int)
                            and not isinstance(revision, bool)
                            and isinstance(current_question, dict)
                            and isinstance(current_question.get("id"), str)
                            and answer_type in {"text", "fact", "choice", "single_choice", "single-choice"}
                        )
                    except Exception:
                        payload["recordable_current_question"] = False
                self._json_response(200, payload, head=head)
                return
            if decoded.startswith("/api/"):
                self._not_found(head=head)
                return

            relative = "index.html" if decoded in {"", "/"} else decoded.lstrip("/")
            try:
                candidate = assets / relative
                if not _within(assets, candidate):
                    self._not_found(head=head)
                    return
                resolved = candidate.resolve(strict=True)
                if not resolved.is_file() or not _within(assets, resolved):
                    self._not_found(head=head)
                    return
                body = resolved.read_bytes()
                content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            except OSError:
                self._not_found(head=head)
                return
            except Exception:
                self._asset_unavailable(head=head)
                return
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json", "image/svg+xml"}:
                content_type += "; charset=utf-8"
            self._respond(200, body, content_type, head=head)

        def _read_json_object(self) -> tuple[dict[str, Any] | None, int]:
            if self.headers.get_all("Transfer-Encoding", []):
                return None, 400
            content_types = self.headers.get_all("Content-Type", [])
            if content_types != ["application/json"]:
                return None, 415
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                return None, 411
            length = int(lengths[0])
            if length <= 0 or length > MAX_REQUEST_BYTES:
                return None, 413
            def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate key")
                    result[key] = value
                return result

            def reject_constant(value: str) -> Any:
                del value
                raise ValueError("non-finite number")

            def finite_float(value: str) -> float:
                parsed_float = float(value)
                if not math.isfinite(parsed_float):
                    raise ValueError("non-finite number")
                return parsed_float

            try:
                raw = self.rfile.read(length)
                parsed = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=unique_object,
                    parse_constant=reject_constant,
                    parse_float=finite_float,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, OSError, RecursionError, ValueError):
                return None, 400
            if not isinstance(parsed, dict):
                return None, 422
            return parsed, 200

        @staticmethod
        def _valid_revision(value: object) -> bool:
            return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2_147_483_647

        def _record(self) -> None:
            if not self._host_allowed():
                self._reject_host()
                return
            decoded = self._authorized_path()
            if urlsplit(self.path).query:
                self._not_found()
                return
            if decoded not in {"/api/intake/choice", "/api/intake/answer"}:
                if decoded is None:
                    self._not_found()
                else:
                    self._method_not_allowed()
                return
            if not decision_recording:
                self._json_response(403, {"error": "This dashboard launch is read-only", "code": "READ_ONLY"})
                return
            if not self._write_proof_allowed():
                self._json_response(403, {"error": "Write proof was rejected", "code": "WRITE_PROOF_REJECTED"})
                return
            body, parse_status = self._read_json_object()
            if body is None:
                messages = {
                    400: "Request body is invalid",
                    411: "A single content length is required",
                    413: "Request body is too large",
                    415: "Content type must be application/json",
                    422: "Request fields are invalid",
                }
                self._json_response(parse_status, {"error": messages[parse_status], "code": "REQUEST_INVALID"})
                return

            if_match = self.headers.get_all("If-Match", [])
            expected_revision = body.get("expected_revision")
            if (
                not self._valid_revision(expected_revision)
                or if_match != [f'"{expected_revision}"']
            ):
                self._json_response(422, {"error": "Revision proof is invalid", "code": "REVISION_INVALID"})
                return

            try:
                if decoded == "/api/intake/choice":
                    if set(body) != {"decision_id", "choice", "expected_revision"}:
                        raise ValueError
                    decision_id = body.get("decision_id")
                    choice = body.get("choice")
                    if (
                        not isinstance(decision_id, str)
                        or DECISION_ID.fullmatch(decision_id) is None
                        or not isinstance(choice, str)
                        or not 1 <= len(choice) <= 120
                    ):
                        raise ValueError
                    if choice_recorder is None:
                        raise RuntimeError
                    refreshed = choice_recorder(
                        decision_id=decision_id,
                        expected_revision=expected_revision,
                        actor="User",
                        source="DASHBOARD",
                        choice=choice,
                    )
                else:
                    if set(body) != {"question_id", "answer", "expected_revision"}:
                        raise ValueError
                    question_id = body.get("question_id")
                    answer = body.get("answer")
                    if (
                        not isinstance(question_id, str)
                        or QUESTION_ID.fullmatch(question_id) is None
                        or not isinstance(answer, str)
                        or not 1 <= len(answer) <= 4_000
                    ):
                        raise ValueError
                    if answer_recorder is None:
                        raise RuntimeError
                    refreshed = answer_recorder(
                        question_id=question_id,
                        expected_revision=expected_revision,
                        actor="User",
                        source="DASHBOARD",
                        answer=answer,
                    )
            except ValueError:
                self._json_response(422, {"error": "Request fields are invalid", "code": "REQUEST_INVALID"})
                return
            except intake_error as exc:
                status = int(getattr(exc, "http_status", 409))
                if status not in {409, 422}:
                    status = 409
                code = getattr(exc, "code", "INTAKE_NOT_READY")
                if not isinstance(code, str) or not code.isascii() or len(code) > 64:
                    code = "INTAKE_NOT_READY"
                message = "The intake state changed; refresh and review the current question" if status == 409 else "The current answer was rejected"
                self._json_response(status, {"error": message, "code": code})
                return
            except Exception:
                self._json_response(500, {"error": "Wayfinder could not record the answer", "code": "WRITE_FAILED"})
                return
            self._json_response(200, {"state": refreshed})

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._serve(head=False)

        def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._serve(head=True)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._record()

        def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._method_not_allowed()

        do_PATCH = do_PUT
        do_DELETE = do_PUT
        do_OPTIONS = do_PUT
        do_CONNECT = do_PUT
        do_TRACE = do_PUT

    return DashboardHandler


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    wayfinder_project_root: Path
    wayfinder_effort_dir: Path
    wayfinder_capability: str
    wayfinder_decision_recording: bool

    def handle_error(self, request: object, client_address: object) -> None:
        """Never let stdlib handler tracebacks disclose local dashboard state."""
        del request, client_address


def dashboard_url(server: DashboardServer) -> str:
    """Return the exact per-launch capability URL users may open locally."""
    return f"http://{LOOPBACK}:{int(server.server_address[1])}/{server.wayfinder_capability}/"


def make_server(
    root: Path,
    effort: str | Path | None = None,
    *,
    port: int = 0,
    quiet: bool = False,
    assets: Path | None = None,
    decision_recording: bool = False,
) -> DashboardServer:
    """Create, but do not start, a loopback-only dashboard server."""
    if not 0 <= port <= 65535:
        raise WayfinderError("Port must be between 0 and 65535.")
    static_root = (assets or dashboard_assets()).resolve(strict=True)
    if not static_root.is_dir() or not (static_root / "index.html").is_file():
        raise WayfinderError("Dashboard assets are missing; expected assets/dashboard/index.html.")
    # Resolve and compute before binding so startup fails clearly and the
    # terminal can identify the exact project/effort being exposed locally.
    project_root, effort_dir = resolve_effort(root, effort)
    build_state(project_root, effort_dir)
    factory = lambda: build_state(project_root, effort_dir)
    capability = secrets.token_urlsafe(24)
    csrf_token = secrets.token_urlsafe(32) if decision_recording else ""
    choice_recorder: Callable[..., dict[str, Any]] | None = None
    answer_recorder: Callable[..., dict[str, Any]] | None = None
    intake_error: type[Exception] = RuntimeError
    if decision_recording:
        intake = _load_intake_module()
        choice_recorder = lambda **kwargs: intake.record_intake_choice(project_root, effort_dir, **kwargs)
        answer_recorder = lambda **kwargs: intake.record_intake_answer(project_root, effort_dir, **kwargs)
        intake_error = intake.IntakeError
    handler = _handler(
        factory,
        static_root,
        quiet,
        capability,
        decision_recording=decision_recording,
        csrf_token=csrf_token,
        choice_recorder=choice_recorder,
        answer_recorder=answer_recorder,
        intake_error=intake_error,
    )
    server = DashboardServer((LOOPBACK, port), handler)
    server.wayfinder_project_root = project_root
    server.wayfinder_effort_dir = effort_dir
    server.wayfinder_capability = capability
    server.wayfinder_decision_recording = decision_recording
    return server


def serve(
    root: Path,
    effort: str | Path | None = None,
    *,
    port: int = 0,
    quiet: bool = False,
    decision_recording: bool = False,
) -> None:
    server = make_server(root, effort, port=port, quiet=quiet, decision_recording=decision_recording)
    relative_effort = server.wayfinder_effort_dir.relative_to(server.wayfinder_project_root).as_posix()
    print(terminal_safe_text(f"Project root: {server.wayfinder_project_root}"), flush=True)
    print(terminal_safe_text(f"Wayfinder effort: {relative_effort}"), flush=True)
    print(f"Wayfinder dashboard: {dashboard_url(server)}", flush=True)
    mode = "Interactive decision recording" if decision_recording else "Read-only"
    print(f"{mode} and bound to this machine. Press Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
