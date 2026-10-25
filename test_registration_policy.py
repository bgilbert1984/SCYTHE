"""Self-registration never lets the caller choose their own authority, in the policy and in every route that creates an operator from a request body.

Both servers expose /api/operator/register: the orchestrator (also as /api/user/register, with a FusionAuth branch and a legacy branch) and the instance server.
Each route is exercised through its real handler with the operator manager (and FusionAuth) replaced by recording fakes, so no database is touched and no password
used here is real.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from registration_policy import DEFAULT_REGISTRATION_ROLE, PUBLIC_REGISTRATION_ROLES, RegistrationRoleError, public_registration_role

BODY = {"callsign": "T-1", "email": "t1@example.invalid", "password": "not-a-real-password"}


class Policy(unittest.TestCase):
    def test_no_role_gets_the_default(self):
        for requested in (None, "", "   "):
            self.assertEqual(public_registration_role(requested), DEFAULT_REGISTRATION_ROLE)
        self.assertEqual(DEFAULT_REGISTRATION_ROLE, "operator")

    def test_the_two_public_roles_are_granted_in_any_case(self):
        self.assertEqual(PUBLIC_REGISTRATION_ROLES, ("observer", "operator"))
        for requested, granted in (("observer", "observer"), ("operator", "operator"), (" Operator ", "operator"), ("OBSERVER", "observer")):
            self.assertEqual(public_registration_role(requested), granted)

    def test_every_other_role_is_refused(self):
        for requested in ("admin", "ADMIN", " Admin ", "supervisor", "root", "operator,admin", "operator\u0000admin", 0, 1, True, ["admin"], {"role": "admin"}, b"admin"):
            with self.assertRaises(RegistrationRoleError, msg=repr(requested)):
                public_registration_role(requested)


class FakeOperators:
    def __init__(self):
        self.registered = []

    def register_operator(self, callsign, email, password, role, team_id=None):
        self.registered.append((callsign, role.value))
        return SimpleNamespace(to_dict=lambda: {"callsign": callsign, "role": role.value})


class FakeFusionAuth:
    def __init__(self):
        self.requests = []

    def create_user(self, request):
        self.requests.append(request)
        return SimpleNamespace(was_successful=lambda: True, success_response={"user": {"id": "u-1"}})


class RouteContract:
    """What every registration route must do; each subclass supplies how to call its route and what it created."""

    def post(self, role):
        raise NotImplementedError

    def created_roles(self):
        raise NotImplementedError

    def test_a_request_with_no_role_creates_an_operator(self):
        response = self.post(None)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.created_roles(), ["operator"])

    def test_the_public_roles_are_created_as_asked(self):
        self.post("observer")
        self.post("operator")
        self.assertEqual(self.created_roles(), ["observer", "operator"])

    def test_what_is_created_is_the_validated_role_not_the_text_that_was_sent(self):
        self.post(" OBSERVER ")
        self.assertEqual(self.created_roles(), ["observer"])

    def test_admin_supervisor_and_unknown_roles_are_refused_and_nothing_is_created(self):
        for role in ("admin", "ADMIN", "supervisor", "root", "operator,admin", 1, ["admin"]):
            response = self.post(role)
            self.assertEqual(response.status_code, 400, role)
            self.assertEqual(response.get_json()["status"], "error")
        self.assertEqual(self.created_roles(), [], "a refused request created nothing")

    def test_a_missing_field_is_still_a_missing_field(self):
        response = self.client.post(self.url, json={"callsign": "T-1"})
        self.assertEqual(response.status_code, 400)


def body_with(role):
    body = dict(BODY)
    if role is not None:
        body["role"] = role
    return body


class OrchestratorLegacyBranch(RouteContract, unittest.TestCase):
    url = "/api/operator/register"

    def setUp(self):
        import scythe_orchestrator as orch
        self.operators = FakeOperators()
        for patch in (mock.patch.object(orch, "fa_client", None), mock.patch.object(orch, "operator_manager", self.operators)):
            patch.start()
            self.addCleanup(patch.stop)
        self.client = orch.app.test_client()

    def post(self, role):
        return self.client.post(self.url, json=body_with(role))

    def created_roles(self):
        return [role for _, role in self.operators.registered]


class OrchestratorSecondRoute(OrchestratorLegacyBranch):
    url = "/api/user/register"


class OrchestratorFusionAuthBranch(RouteContract, unittest.TestCase):
    url = "/api/operator/register"

    def setUp(self):
        import scythe_orchestrator as orch
        self.fusionauth = FakeFusionAuth()
        patch = mock.patch.object(orch, "fa_client", self.fusionauth)
        patch.start()
        self.addCleanup(patch.stop)
        self.client = orch.app.test_client()

    def post(self, role):
        return self.client.post(self.url, json=body_with(role))

    def created_roles(self):
        return [request["user"]["data"]["role"] for request in self.fusionauth.requests]


class InstanceServer(RouteContract, unittest.TestCase):
    url = "/api/operator/register"

    def setUp(self):
        import rf_scythe_api_server as rf
        self.operators = FakeOperators()
        patch = mock.patch.object(rf, "operator_manager", self.operators)
        patch.start()
        self.addCleanup(patch.stop)
        self.client = rf.app.test_client()

    def post(self, role):
        return self.client.post(self.url, json=body_with(role))

    def created_roles(self):
        return [role for _, role in self.operators.registered]


if __name__ == "__main__":
    unittest.main()
