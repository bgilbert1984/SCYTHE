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
        for target, value in (("HAS_SIMPLE_WS", True), ("HAS_WS_CLIENT", True),
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


if __name__ == "__main__":
    unittest.main()
