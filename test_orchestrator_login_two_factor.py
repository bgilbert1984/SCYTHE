"""A FusionAuth login that is not a login must not become a session.

FusionAuth answers 242 when the user has two-factor enrolled: a 2xx status (so the client's was_successful() is true) whose body has a twoFactorId and no token.
The orchestrator's login route used to read the missing token and answer status ok with a null session token. It now refuses, fails closed, and never falls
through to the legacy operator store on that response. The route runs through its real handler with FusionAuth and the operator manager replaced by fakes.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

BODY = {"callsign": "T-1", "password": "not-a-real-password"}


def fa_response(status, body):
    return SimpleNamespace(status=status, was_successful=lambda: 200 <= status <= 299, success_response=body)


class FakeFusionAuth:
    def __init__(self, response):
        self.response = response

    def login(self, request):
        return self.response


class RecordingOperators:
    def __init__(self):
        self.authenticated = []

    def authenticate(self, callsign, password):
        self.authenticated.append(callsign)
        return None


class LoginRoute(unittest.TestCase):
    url = "/api/operator/login"

    def run_login(self, response):
        import scythe_orchestrator as orch
        self.operators = RecordingOperators()
        for patch in (mock.patch.object(orch, "fa_client", FakeFusionAuth(response)), mock.patch.object(orch, "operator_manager", self.operators)):
            patch.start()
            self.addCleanup(patch.stop)
        return orch.app.test_client().post(self.url, json=BODY)

    def test_a_normal_login_still_returns_the_token(self):
        r = self.run_login(fa_response(200, {"token": "jwt-1", "user": {"id": "u-1", "username": "T-1", "data": {}}}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["session"]["session_token"], "jwt-1")

    def test_a_242_two_factor_response_is_refused_with_no_session(self):
        r = self.run_login(fa_response(242, {"twoFactorId": "tf-1", "methods": [{"method": "authenticator"}]}))
        self.assertEqual(r.status_code, 401)
        body = r.get_json()
        self.assertEqual(body["status"], "error")
        self.assertTrue(body["two_factor_required"])
        self.assertNotIn("session", body)
        self.assertNotIn("tf-1", r.get_data(as_text=True), "the two-factor id is not handed to the page")

    def test_a_242_does_not_fall_through_to_the_legacy_operator_store(self):
        self.run_login(fa_response(242, {"twoFactorId": "tf-1"}))
        self.assertEqual(self.operators.authenticated, [])

    def test_a_success_with_no_token_is_refused_even_without_a_two_factor_id(self):
        r = self.run_login(fa_response(200, {"user": {"id": "u-1"}}))
        self.assertEqual(r.status_code, 401)
        body = r.get_json()
        self.assertFalse(body["two_factor_required"])
        self.assertNotIn("session", body)

    def test_the_other_login_url_behaves_the_same(self):
        self.url = "/api/user/login"
        r = self.run_login(fa_response(242, {"twoFactorId": "tf-1"}))
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
