import unittest

from ip_enrichment import IpEnrichmentResolver, enrich_graph_node


class _AsnReader:
    def get_with_prefix_len(self, ip):
        return ({"autonomous_system_number": 15169,
                 "autonomous_system_organization": "Google LLC"}, 24)


class _CityReader:
    def get(self, ip):
        return {
            "country": {"iso_code": "US", "names": {"en": "United States"}},
            "subdivisions": [{"names": {"en": "California"}}],
            "city": {"names": {"en": "Mountain View"}},
            "location": {"latitude": 37.4, "longitude": -122.1, "accuracy_radius": 25},
        }


def _resolver():
    resolver = IpEnrichmentResolver(cache_limit=2)
    resolver._readers = {"asn": _AsnReader(), "city": _CityReader()}
    resolver._sources = {
        "asn": {"database": "fixture-asn", "buildEpoch": 1, "sha256": "a", "localOnly": True},
        "city": {"database": "fixture-city", "buildEpoch": 2, "sha256": "b", "localOnly": True},
    }
    return resolver


class IpEnrichmentTests(unittest.TestCase):
    def test_public_ip_has_claim_level_network_and_geo_authority(self):
        result = _resolver().resolve("8.8.8.8")
        self.assertEqual(result["scope"], "PUBLIC")
        self.assertEqual(result["network"]["asn"], 15169)
        self.assertEqual(result["network"]["prefix"], "8.8.8.0/24")
        self.assertEqual(result["network"]["evidenceClass"], "INFERRED")
        self.assertEqual(result["geo"]["city"], "Mountain View")
        self.assertEqual(result["geo"]["uncertaintyRadiusKm"], 25.0)
        self.assertEqual(result["geo"]["authority"], "GEOIP_ESTIMATE")

    def test_private_and_reserved_addresses_never_receive_geoip_claims(self):
        for address, scope in (("192.168.1.4", "PRIVATE"), ("127.0.0.1", "LOOPBACK"),
                               ("224.0.0.1", "MULTICAST"), ("169.254.1.2", "LINK_LOCAL"),
                               ("100.64.0.1", "RESERVED")):
            result = _resolver().resolve(address)
            self.assertEqual(result["scope"], scope)
            self.assertNotIn("network", result)
            self.assertNotIn("geo", result)

    def test_enrichment_never_becomes_topology_position_or_observed_evidence(self):
        node = {"id": "host:8.8.8.8", "kind": "network_host", "labels": {"ip": "8.8.8.8"},
                "evidenceClass": "OBSERVED", "position": None}
        enriched = enrich_graph_node(node, _resolver())
        self.assertEqual(enriched["evidenceClass"], "OBSERVED")
        self.assertIsNone(enriched["position"])
        self.assertEqual(enriched["enrichment"]["geo"]["evidenceClass"], "INFERRED")
        self.assertNotIn("enrichment", node)


if __name__ == "__main__":
    unittest.main()
