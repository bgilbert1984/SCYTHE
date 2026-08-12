import os
import tempfile
import unittest
from unittest.mock import patch

from graphops_copilot import GraphOpsAgent


class OllamaCloudTransportTests(unittest.TestCase):
    def test_key_file_accepts_existing_api_assignment_without_logging_value(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            handle.write("API=test-secret\n")
            handle.flush()
            with patch.dict(os.environ, {"OLLAMA_API_KEY_FILE": handle.name}, clear=True):
                self.assertEqual(GraphOpsAgent._load_ollama_api_key(), "test-secret")

    def test_authorization_is_confined_to_exact_https_cloud_host(self):
        agent = GraphOpsAgent.__new__(GraphOpsAgent)
        agent._ollama_api_key = "test-secret"
        self.assertEqual(agent._ollama_headers("https://ollama.com")["Authorization"],
                         "Bearer test-secret")
        for endpoint in ("http://ollama.com", "https://evil.ollama.com",
                         "https://ollama.com.example", "http://127.0.0.1:11434",
                         "http://192.168.1.185:11434"):
            self.assertNotIn("Authorization", agent._ollama_headers(endpoint))

    def test_cloud_route_is_explicit(self):
        agent = GraphOpsAgent.__new__(GraphOpsAgent)
        agent._ollama = "https://ollama.com"
        agent._configured_ollama = "https://ollama.com"
        self.assertEqual(agent._ollama_route(), "OLLAMA_CLOUD_DIRECT")


if __name__ == "__main__":
    unittest.main()
