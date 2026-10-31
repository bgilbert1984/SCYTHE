import pathlib
import unittest

from flask import Flask

from totp_gate import install_totp_gate

ROOT = pathlib.Path(__file__).resolve().parent
TOTP_ROUTES = [("POST", "setup"), ("POST", "enable"), ("POST", "verify"), ("GET", "status")]


def _app():
    app = Flask(__name__)

    @app.route("/api/operator/totp/verify", methods=["POST"])
    def would_mint_a_session():
        raise AssertionError("the TOTP handler must not run")

    @app.route("/api/operator/login", methods=["POST"])
    def login():
        return "login", 200

    install_totp_gate(app)
    return app


class TotpGateTests(unittest.TestCase):
    def test_every_totp_route_answers_501(self):
        client = _app().test_client()
        for method, name in TOTP_ROUTES:
            response = client.open(f"/api/operator/totp/{name}", method=method, json={"callsign": "x", "totp_code": "123456"})
            self.assertEqual(response.status_code, 501, name)
            self.assertEqual(response.get_json()["status"], "error")

    def test_the_gate_runs_before_a_registered_handler(self):
        self.assertEqual(_app().test_client().post("/api/operator/totp/verify").status_code, 501)

    def test_other_routes_are_unaffected(self):
        response = _app().test_client().post("/api/operator/login")
        self.assertEqual((response.status_code, response.data), (200, b"login"))

    def test_a_similar_prefix_is_not_gated(self):
        app = _app()
        app.add_url_rule("/api/operator/totpx/y", "other", lambda: "ok")
        self.assertEqual(app.test_client().get("/api/operator/totpx/y").status_code, 200)

    def test_both_servers_install_the_gate(self):
        for name in ("scythe_orchestrator.py", "rf_scythe_api_server.py"):
            source = (ROOT / name).read_text(encoding="utf8")
            self.assertIn("install_totp_gate(app)", source, name)
            self.assertLess(source.index("install_totp_gate(app)"), source.index("/api/operator/totp/setup'"), name)


if __name__ == "__main__":
    unittest.main()
