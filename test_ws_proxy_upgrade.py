"""The orchestrator's /proxy/<port>/ws tunnel: it accepts a real WebSocket upgrade, and only for a valid session.

No sockets: the Flask test client sends the upgrade headers, and the WebSocket server/client classes are stubs.
"""
import unittest
from unittest import mock

import scythe_orchestrator as orch

UPGRADE = {"Connection": "Upgrade", "Upgrade": "websocket", "Sec-WebSocket-Version": "13",
           "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}


REACHED = []   # a request that got past authentication and tried to complete the handshake


class _Handshake:
    def __init__(self, environ):
        REACHED.append(True)
        raise RuntimeError("stop at the handshake")


def _good_only(token):
    return {"valid": True, "userId": "u-1"} if token == "good" else None


class Tunnel(unittest.TestCase):
    def setUp(self):
        REACHED.clear()
        self.client = orch.app.test_client()
        # The backend connection is made before the handshake, so it is stubbed too: these tests must never open a real socket.
        no_backend_needed = mock.Mock(create_connection=lambda url, timeout=None: _Remote([]))
        for target, value in (("HAS_SIMPLE_WS", True), ("HAS_WS_CLIENT", True), ("ws_client", no_backend_needed),
                              ("WSServer", _Handshake), ("validate_jwt", _good_only)):
            patcher = mock.patch.object(orch, target, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def get(self, path, headers=UPGRADE):
        return self.client.get(path, headers=headers)

    def test_an_upgrade_is_routed_to_the_proxy_not_rejected_by_the_router(self):
        # Without websocket=True on the rules, Werkzeug answers every upgrade with 400 before the view runs.
        self.assertNotEqual(self.get("/proxy/8765/ws").status_code, 400)
        self.assertNotEqual(self.get("/proxy/8765/ws/extra").status_code, 400)

    def test_both_routes_have_a_websocket_rule(self):
        paths = {rule.rule for rule in orch.app.url_map.iter_rules() if rule.websocket}
        self.assertTrue({"/proxy/<int:port>/ws", "/proxy/<int:port>/ws/<path:subpath>"} <= paths, paths)

    def test_no_token_is_refused(self):
        self.assertEqual(self.get("/proxy/8765/ws").status_code, 401)

    def test_a_bad_token_is_refused_without_reaching_the_handshake(self):
        for path in ("/proxy/8765/ws?token=nope", "/proxy/8766/ws?token=", "/proxy/8765/ws?token=good%20"):
            self.assertEqual(self.get(path).status_code, 401, path)
        self.assertEqual(REACHED, [])

    def test_a_valid_session_reaches_the_handshake_from_the_query_or_a_header(self):
        for path, headers in (("/proxy/8765/ws?token=good", UPGRADE),
                              ("/proxy/8765/ws", {**UPGRADE, "X-Session-Token": "good"}),
                              ("/proxy/8765/ws", {**UPGRADE, "Authorization": "Bearer good"})):
            REACHED.clear()
            self.get(path, headers)
            self.assertEqual(REACHED, [True], path)

    def test_a_port_that_is_not_allowed_is_refused_before_anything_else(self):
        self.assertEqual(self.get("/proxy/22/ws?token=good").status_code, 403)

    def test_the_session_token_is_not_passed_to_the_backend(self):
        seen = []

        class Server:
            def __init__(self, environ): pass
            def close(self): pass

        def connect(url, timeout=None):
            seen.append(url)
            raise OSError("stop here")

        with mock.patch.object(orch, "WSServer", Server), \
             mock.patch.object(orch, "ws_client", mock.Mock(create_connection=connect), create=True):
            resp = self.get("/proxy/8765/ws?token=good&room=a&room=b")
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(seen, ["ws://127.0.0.1:8765/ws?room=a&room=b"])

    def test_a_plain_request_still_takes_the_http_fallback(self):
        with mock.patch.object(orch, "generic_port_proxy", lambda port, subpath: ("fallback", 200)):
            resp = self.client.get("/proxy/8765/ws")
        self.assertEqual((resp.status_code, resp.get_data()), (200, b"fallback"))


class _Remote:
    """A stand-in for the backend connection: replays frames, records what is sent to it."""
    def __init__(self, frames):
        self.frames, self.sent, self.timeout, self.closed = list(frames), [], "unset", False

    def settimeout(self, value): self.timeout = value
    def recv_data(self):
        if self.frames:
            return self.frames.pop(0)
        return 8, b""
    def send(self, data): self.sent.append(("text", data))
    def send_binary(self, data): self.sent.append(("binary", data))
    def close(self): self.closed = True


class _Browser:
    """A stand-in for the browser side: replays messages, records what is sent to it."""
    def __init__(self, messages=()):
        self.messages, self.sent, self.closed = list(messages), [], False
    def send(self, data): self.sent.append(data)
    def receive(self):
        if self.messages:
            return self.messages.pop(0)
        raise ConnectionError("closed")
    def close(self): self.closed = True


class Relay(unittest.TestCase):
    def setUp(self):
        self.client = orch.app.test_client()
        self.remote = _Remote([])
        self.browser = _Browser()
        fake_client = mock.Mock(ABNF=mock.Mock(OPCODE_TEXT=1, OPCODE_BINARY=2, OPCODE_CLOSE=8),
                                create_connection=lambda url, timeout=None: self.remote)
        for target, value in (("HAS_SIMPLE_WS", True), ("HAS_WS_CLIENT", True), ("validate_jwt", _good_only),
                              ("WSServer", lambda environ: self.browser), ("ws_client", fake_client)):
            patcher = mock.patch.object(orch, target, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_tunnel(self):
        # The finished tunnel returns a response that refuses to write anything: the test client sees that as an error.
        with self.assertRaises(ConnectionAbortedError):
            self.client.get("/proxy/8765/ws?token=good", headers=UPGRADE)

    def test_text_and_binary_frames_keep_their_type_and_a_close_ends_the_stream(self):
        self.remote.frames = [(1, b"hi"), (2, b"\x00\xff"), (8, b"")]
        self.run_tunnel()
        self.assertEqual(self.browser.sent, ["hi", b"\x00\xff"])   # a close is not forwarded as an empty message
        self.assertTrue(self.browser.closed)

    def test_browser_messages_keep_their_type(self):
        self.remote.frames = []
        self.browser.messages = ["t", b"b"]
        self.run_tunnel()
        self.assertEqual(self.remote.sent, [("text", "t"), ("binary", b"b")])
        self.assertTrue(self.remote.closed)

    def test_the_connect_timeout_is_not_left_on_an_idle_stream(self):
        self.run_tunnel()
        self.assertIsNone(self.remote.timeout)

    def test_a_backend_that_is_down_gets_a_plain_502_before_any_handshake(self):
        handshakes = []

        def down(url, timeout=None):
            raise OSError("refused")

        with mock.patch.object(orch, "ws_client", mock.Mock(create_connection=down), create=True), \
             mock.patch.object(orch, "WSServer", lambda environ: handshakes.append(environ), create=True):
            resp = self.client.get("/proxy/8765/ws?token=good", headers=UPGRADE)
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(handshakes, [])


class Finished(unittest.TestCase):
    def test_nothing_is_written_after_a_websocket_ends_and_the_socket_is_closed(self):
        sock = mock.Mock()
        start_response = mock.Mock()
        with self.assertRaises(ConnectionAbortedError):
            orch._WebSocketDone()({"werkzeug.socket": sock}, start_response)
        start_response.assert_not_called()
        sock.shutdown.assert_called_once()
        sock.close.assert_called_once()

    def test_it_also_works_without_a_raw_socket(self):
        with self.assertRaises(ConnectionAbortedError):
            orch._WebSocketDone()({}, mock.Mock())


if __name__ == "__main__":
    unittest.main()
