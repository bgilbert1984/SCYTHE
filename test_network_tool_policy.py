import ipaddress
import unittest
from unittest.mock import patch

from network_tool_policy import (
    NetworkToolPolicyError, parse_nmap_report_identity, validate_ndpi_request, validate_nmap_options,
    validate_nmap_target,
)


class NetworkToolPolicyTests(unittest.TestCase):
    def test_nmap_accepts_private_targets_and_bounded_options(self):
        self.assertEqual(validate_nmap_target("192.168.1.0/24"), "192.168.1.0/24")
        self.assertEqual(validate_nmap_options("-sT -sV -T3 --top-ports 100"),
                         ["-sT", "-sV", "-T3", "--top-ports", "100"])

    def test_nmap_rejects_public_targets_hostnames_scripts_and_output(self):
        for target in ("scanme.nmap.org", "8.8.8.8", "0.0.0.0/0", "--help"):
            with self.subTest(target=target), self.assertRaises(NetworkToolPolicyError):
                validate_nmap_target(target)
        for options in ("--script vuln", "-oX /tmp/result", "-sS", "-T5"):
            with self.subTest(options=options), self.assertRaises(NetworkToolPolicyError):
                validate_nmap_options(options)

    def test_nmap_can_use_an_explicit_operator_network_boundary(self):
        allowed = (ipaddress.ip_network("203.0.113.0/24"),)
        self.assertEqual(validate_nmap_target("203.0.113.10", allowed), "203.0.113.10")

    def test_ndpi_interface_and_duration_are_bounded(self):
        self.assertEqual(validate_ndpi_request("eth0", 10), ("eth0", 10))
        for interface, duration in (("../eth0", 10), ("lo", 10), ("eth0", 0), ("eth0", 61)):
            with self.subTest(interface=interface, duration=duration), self.assertRaises(NetworkToolPolicyError):
                validate_ndpi_request(interface, duration)
        with patch.dict("os.environ", {"SCYTHE_NDPI_ALLOWED_INTERFACES": "eth0,lo"}):
            self.assertEqual(validate_ndpi_request("lo", 2), ("lo", 2))

    def test_nmap_report_identity_handles_address_only_and_named_hosts(self):
        self.assertEqual(parse_nmap_report_identity("Nmap scan report for 127.0.0.1"),
                         ("127.0.0.1", "127.0.0.1"))
        self.assertEqual(parse_nmap_report_identity("Nmap scan report for router.local (192.168.1.1)"),
                         ("192.168.1.1", "router.local"))


if __name__ == "__main__":
    unittest.main()
