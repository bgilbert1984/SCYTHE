import unittest
from unittest.mock import patch

from graphops_rf_tune import (MODES, TuneProposalRefused, tune_receipt, validate_tune_request)
from scythe_orchestrator import app


def _request(**overrides):
    return {"frequency_hz": 101_700_000, "mode": "WFM", **overrides}


class TuneValidationTests(unittest.TestCase):
    def test_a_valid_request_becomes_bounded_rf_tune_parameters(self):
        frame = validate_tune_request(_request())
        self.assertEqual(frame["tool_name"], "rf_tune")
        self.assertEqual(frame["params"], {"frequency_hz": 101_700_000.0, "mode": "WFM"})
        self.assertEqual(frame["regime"], "TUNER")

    def test_every_proposal_states_that_it_was_not_executed(self):
        boundaries = " ".join(validate_tune_request(_request())["boundaries"])
        self.assertIn("TUNING IS PROPOSED, NOT EXECUTED", boundaries)
        self.assertIn("NO RIGCTL CONNECTION WAS OPENED", boundaries)

    def test_tunability_is_not_presented_as_transmission_authorization(self):
        boundaries = " ".join(validate_tune_request(_request())["boundaries"])
        self.assertIn("TUNABILITY IS NOT TRANSMISSION AUTHORIZATION", boundaries)

    def test_unknown_fields_are_refused_rather_than_ignored(self):
        with self.assertRaisesRegex(TuneProposalRefused, "unknown tune fields"):
            validate_tune_request(_request(gain_db=29.7))

    def test_every_declared_mode_is_accepted_and_others_refused(self):
        for mode in MODES:
            self.assertEqual(validate_tune_request(_request(mode=mode))["params"]["mode"], mode)
        with self.assertRaisesRegex(TuneProposalRefused, "mode must be one of"):
            validate_tune_request(_request(mode="FM-STEREO"))

    def test_direct_sampling_is_never_entered_implicitly(self):
        with self.assertRaisesRegex(TuneProposalRefused, "acknowledge_direct_sampling"):
            validate_tune_request(_request(frequency_hz=5_000_000))
        frame = validate_tune_request(
            _request(frequency_hz=5_000_000, acknowledge_direct_sampling=True))
        self.assertEqual(frame["regime"], "DIRECT_SAMPLING")
        self.assertIn("DIRECT SAMPLING PERFORMANCE DIFFERS FROM ORDINARY TUNER MODE",
                      frame["boundaries"])

    def test_a_frequency_outside_declared_coverage_is_refused_not_clamped(self):
        # Clamping would produce a receipt for a tuning the operator never asked for.
        with self.assertRaisesRegex(TuneProposalRefused, "outside the declared coverage"):
            validate_tune_request(_request(frequency_hz=2_400_000_000))

    def test_non_numeric_and_non_finite_frequencies_are_refused(self):
        for bad in ("ninety", None, float("inf"), float("nan")):
            with self.assertRaises(TuneProposalRefused):
                validate_tune_request({"frequency_hz": bad})

    def test_the_receipt_binds_the_exact_proposed_parameters(self):
        frame = validate_tune_request(_request())
        receipt = tune_receipt(frame, {"proposal_id": "p-1", "status": "proposed",
                                       "approval_reason": "awaiting executor"})
        self.assertEqual(receipt["proposalId"], "p-1")
        self.assertFalse(receipt["executed"])
        self.assertEqual(len(receipt["requestHash"]), 64)
        moved = validate_tune_request(_request(frequency_hz=101_800_000))
        self.assertNotEqual(receipt["requestHash"], tune_receipt(moved, {})["requestHash"])


class TuneEndpointTests(unittest.TestCase):
    """The browser may propose. It may never execute, and never reaches Rigctl."""

    def _post(self, body, proposal=None, port=42095):
        envelope = {'jsonrpc': '2.0', 'result': proposal} if proposal is not None else None
        with patch('scythe_orchestrator._graphops_directive_authorized', return_value=True), \
             patch('scythe_orchestrator._get_primary_instance_port', return_value=port), \
             patch('scythe_orchestrator._proxy_post', return_value=envelope) as proxy:
            response = app.test_client().post('/api/graphops/rf-tune/propose', json=body)
        return response, proxy

    def test_a_click_records_a_proposal_and_reports_no_execution(self):
        response, proxy = self._post(_request(), proposal={
            'proposal_id': 'abc-123', 'status': 'proposed', 'approval_reason': 'awaiting executor'})
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertTrue(payload['proposed'])
        self.assertFalse(payload['executed'])
        self.assertFalse(payload['rigctlContacted'])
        self.assertEqual(payload['receipt']['proposalId'], 'abc-123')
        # It must reach the real safety gate, as orchestrate/propose — never execute.
        path, body = proxy.call_args.args[1], proxy.call_args.args[2]
        self.assertEqual(path, '/mcp')
        self.assertEqual(body['method'], 'orchestrate/propose')
        self.assertEqual(body['params']['tool_name'], 'rf_tune')

    def test_the_endpoint_never_calls_execute_even_when_approved(self):
        response, proxy = self._post(_request(), proposal={
            'proposal_id': 'abc-123', 'status': 'approved', 'approval_reason': 'within budget'})
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.get_json()['executed'])
        methods = [call.args[2]['method'] for call in proxy.call_args_list]
        self.assertEqual(methods, ['orchestrate/propose'],
                         'an approved proposal must not be executed by the web path')

    def test_a_refused_request_is_never_transmitted_to_the_safety_gate(self):
        response, proxy = self._post(_request(frequency_hz=2_400_000_000))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['proposed'])
        proxy.assert_not_called()

    def test_without_an_instance_the_request_is_not_silently_dropped(self):
        response, proxy = self._post(_request(), port=None)
        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload['proposed'])
        self.assertIn('not recorded as a proposal', payload['error'])
        proxy.assert_not_called()

    def test_a_silent_safety_gate_is_reported_rather_than_assumed_approved(self):
        response, _ = self._post(_request(), proposal=None)
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()['proposed'])

    def test_unauthenticated_callers_cannot_propose(self):
        with patch('scythe_orchestrator._graphops_directive_authorized', return_value=False):
            response = app.test_client().post('/api/graphops/rf-tune/propose', json=_request())
        self.assertEqual(response.status_code, 401)


if __name__ == '__main__':
    unittest.main()
