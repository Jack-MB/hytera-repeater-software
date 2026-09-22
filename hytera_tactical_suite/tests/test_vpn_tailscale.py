"""
Tests für Tailscale VPN-Integration & Fernzugriffs-Endpunkte
"""

import os
import sys
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
SUITE_DIR = os.path.abspath(os.path.join(TEST_DIR, ".."))
if SUITE_DIR not in sys.path:
    sys.path.insert(0, SUITE_DIR)

from tailscale_helper import get_tailscale_ip, get_tailscale_status
from main import app


class TestTailscaleVPN(unittest.TestCase):

    def test_tailscale_status_structure(self):
        status = get_tailscale_status(8000)
        self.assertIn("active", status)
        self.assertIn("ip", status)
        self.assertIn("url", status)
        self.assertIn("port", status)
        self.assertEqual(status["port"], 8000)
        self.assertIn("subnet_route_cmd", status)
        self.assertIn("192.168.0.0/24", status["subnet_route_cmd"])

    @patch("tailscale_helper._run_tailscale_cli")
    def test_tailscale_detected(self, mock_cli):
        mock_cli.return_value = "100.85.24.12"
        ip = get_tailscale_ip()
        self.assertEqual(ip, "100.85.24.12")
        status = get_tailscale_status(8000)
        self.assertTrue(status["active"])
        self.assertEqual(status["ip"], "100.85.24.12")
        self.assertEqual(status["url"], "http://100.85.24.12:8000")

    @patch("tailscale_helper._run_tailscale_cli")
    @patch("tailscale_helper._scan_ipconfig")
    @patch("tailscale_helper._scan_socket")
    def test_tailscale_offline(self, mock_sock, mock_ipcfg, mock_cli):
        mock_cli.return_value = None
        mock_ipcfg.return_value = None
        mock_sock.return_value = None
        ip = get_tailscale_ip()
        self.assertIsNone(ip)
        status = get_tailscale_status(8000)
        self.assertFalse(status["active"])
        self.assertEqual(status["ip"], "--")
        self.assertEqual(status["url"], "--")

    def test_api_vpn_status_endpoint(self):
        with TestClient(app) as client:
            resp = client.get("/api/vpn/status")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("active", data)
            self.assertIn("ip", data)
            self.assertIn("url", data)
            self.assertIn("subnet_route_cmd", data)

    def test_api_initial_state_contains_vpn(self):
        with TestClient(app) as client:
            resp = client.get("/api/initial-state")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("vpn", data)
            self.assertIn("active", data["vpn"])
            self.assertIn("ip", data["vpn"])


if __name__ == "__main__":
    unittest.main()
