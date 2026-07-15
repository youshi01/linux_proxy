import importlib.machinery
import importlib.util
import base64
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_proxy_module():
    loader = importlib.machinery.SourceFileLoader("linux_proxy_under_test", str(ROOT / "proxy"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class LocalInbound:
    def __init__(self, socks=False):
        self.socks = socks
        self.stop_event = threading.Event()
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.server.settimeout(0.1)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop_event.set()
        self.server.close()
        self.thread.join(timeout=1)

    def serve(self):
        while not self.stop_event.is_set():
            try:
                connection, _ = self.server.accept()
            except (OSError, socket.timeout):
                continue
            with connection:
                if self.socks:
                    connection.settimeout(1)
                    try:
                        if connection.recv(3) == b"\x05\x01\x00":
                            connection.sendall(b"\x05\x00")
                    except (OSError, socket.timeout):
                        pass


class ProxyModeTests(unittest.TestCase):
    def setUp(self):
        self.proxy = load_proxy_module()
        self.runtime = {"service": "xray", "config_file": "/tmp/config.json"}

    def test_mode_starts_and_verifies_service_before_writing_environment(self):
        events = []

        def ensure(runtime):
            events.append(("ensure", runtime["service"]))
            return True

        def apply(mode):
            events.append(("apply", mode))

        with mock.patch.object(self.proxy, "require_service", return_value=self.runtime), \
             mock.patch.object(self.proxy, "ensure_proxy_running", side_effect=ensure), \
             mock.patch.object(self.proxy, "apply_mode", side_effect=apply), \
             mock.patch("builtins.print"):
            result = self.proxy.set_proxy_mode("outbound")

        self.assertTrue(result)
        self.assertEqual(events, [("ensure", "xray"), ("apply", "outbound")])

    def test_failed_start_does_not_apply_mode_and_cleans_proxy_environment(self):
        events = []

        with mock.patch.object(self.proxy, "require_service", return_value=self.runtime), \
             mock.patch.object(self.proxy, "ensure_proxy_running", return_value=False), \
             mock.patch.object(self.proxy, "cleanup_env_file", side_effect=lambda: events.append("profile-cleaned")), \
             mock.patch.object(self.proxy, "clear_global_env_file", side_effect=lambda: events.append("environment-cleaned")), \
             mock.patch.object(self.proxy, "apply_mode") as apply_mode, \
             mock.patch("builtins.print"):
            result = self.proxy.set_proxy_mode("global")

        self.assertFalse(result)
        apply_mode.assert_not_called()
        self.assertEqual(events, ["profile-cleaned", "environment-cleaned"])

    def test_missing_service_also_cleans_stale_proxy_environment(self):
        events = []

        with mock.patch.object(self.proxy, "require_service", return_value=None), \
             mock.patch.object(self.proxy, "cleanup_env_file", side_effect=lambda: events.append("profile-cleaned")), \
             mock.patch.object(self.proxy, "clear_global_env_file", side_effect=lambda: events.append("environment-cleaned")), \
             mock.patch.object(self.proxy, "ensure_proxy_running") as ensure, \
             mock.patch.object(self.proxy, "apply_mode") as apply_mode, \
             mock.patch("builtins.print"):
            result = self.proxy.set_proxy_mode("inbound")

        self.assertFalse(result)
        ensure.assert_not_called()
        apply_mode.assert_not_called()
        self.assertEqual(events, ["profile-cleaned", "environment-cleaned"])

    def test_ensure_proxy_running_enables_service_and_waits_for_inbounds(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(self.proxy, "run", return_value=completed) as run, \
             mock.patch.object(self.proxy, "wait_for_proxy_ready", return_value=(True, "")) as wait:
            result = self.proxy.ensure_proxy_running(self.runtime)

        self.assertTrue(result)
        run.assert_called_once_with(["systemctl", "enable", "xray", "--now"], timeout=15)
        wait.assert_called_once_with("xray")

    def test_readiness_requires_active_service_and_both_local_inbounds(self):
        with mock.patch.object(self.proxy, "service_is_active", return_value=True), \
             mock.patch.object(self.proxy, "socks_inbound_ready", return_value=True), \
             mock.patch.object(self.proxy, "http_inbound_ready", return_value=True):
            ready, reason = self.proxy.wait_for_proxy_ready("xray", timeout=0)

        self.assertTrue(ready)
        self.assertEqual(reason, "")

        with mock.patch.object(self.proxy, "service_is_active", return_value=True), \
             mock.patch.object(self.proxy, "socks_inbound_ready", return_value=False), \
             mock.patch.object(self.proxy, "http_inbound_ready", return_value=True):
            ready, reason = self.proxy.wait_for_proxy_ready("xray", timeout=0)

        self.assertFalse(ready)
        self.assertIn("SOCKS5", reason)

    def test_all_three_modes_with_generic_local_inbounds(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             LocalInbound(socks=True) as socks, \
             LocalInbound() as http:
            root = Path(temp_dir)
            profile = root / "profile.d" / "linux-proxy.sh"
            environment = root / "environment"
            mode_file = root / "mode.json"
            profile.parent.mkdir(parents=True)
            environment.write_text("KEEP_ME=yes\n", encoding="utf-8")

            self.proxy.SOCKS_PORT = socks.port
            self.proxy.HTTP_PORT = http.port
            self.proxy.PROXY_SH = str(profile)
            self.proxy.ENV_FILE = str(environment)
            self.proxy.MODE_FILE = str(mode_file)
            self.proxy.PROXY_ENV_LINES = [
                f"HTTP_PROXY=http://127.0.0.1:{http.port}",
                f"HTTPS_PROXY=http://127.0.0.1:{http.port}",
                f"ALL_PROXY=socks5h://127.0.0.1:{socks.port}",
                f"http_proxy=http://127.0.0.1:{http.port}",
                f"https_proxy=http://127.0.0.1:{http.port}",
                f"all_proxy=socks5h://127.0.0.1:{socks.port}",
            ]
            completed = subprocess.CompletedProcess([], 0, "", "")

            with mock.patch.object(self.proxy, "require_service", return_value=self.runtime), \
                 mock.patch.object(self.proxy, "run", return_value=completed), \
                 mock.patch.object(self.proxy, "service_is_active", return_value=True), \
                 mock.patch("builtins.print"):
                self.assertTrue(self.proxy.set_proxy_mode("inbound"))
                self.assertFalse(profile.exists())
                self.assertNotIn("_PROXY=", environment.read_text(encoding="utf-8").upper())

                self.assertTrue(self.proxy.set_proxy_mode("outbound"))
                self.assertEqual(len(profile.read_text(encoding="utf-8").splitlines()), 6)
                self.assertNotIn("_PROXY=", environment.read_text(encoding="utf-8").upper())

                self.assertTrue(self.proxy.set_proxy_mode("global"))
                self.assertEqual(len(profile.read_text(encoding="utf-8").splitlines()), 6)
                self.assertEqual(
                    len([line for line in environment.read_text(encoding="utf-8").splitlines() if "_PROXY=" in line.upper()]),
                    6,
                )

            self.assertEqual(json.loads(mode_file.read_text(encoding="utf-8"))["mode"], "global")
            self.assertIn("KEEP_ME=yes", environment.read_text(encoding="utf-8"))

    def test_linux_proxy_environment_overrides_make_runtime_paths_generic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            values = {
                "LINUX_PROXY_DATA_DIR": str(root / "data"),
                "LINUX_PROXY_SERVICE": "custom-proxy-service",
                "LINUX_PROXY_CONFIG": str(root / "custom-config.json"),
                "LINUX_PROXY_SOCKS_PORT": "12080",
                "LINUX_PROXY_HTTP_PORT": "12081",
                "LINUX_PROXY_PROFILE_FILE": str(root / "profile.sh"),
                "LINUX_PROXY_ENV_FILE": str(root / "environment"),
            }
            with mock.patch.dict(os.environ, values):
                proxy = load_proxy_module()

            self.assertEqual(proxy.DATA_DIR, root / "data")
            self.assertEqual(proxy.SERVICE_CANDIDATES[0], "custom-proxy-service")
            self.assertEqual(proxy.CONFIG_CANDIDATES[0], str(root / "custom-config.json"))
            self.assertEqual(proxy.SOCKS_PORT, 12080)
            self.assertEqual(proxy.HTTP_PORT, 12081)
            self.assertEqual(proxy.PROXY_SH, str(root / "profile.sh"))
            self.assertEqual(proxy.ENV_FILE, str(root / "environment"))

    def test_subscription_decoder_supports_raw_base64_and_urlsafe_base64(self):
        raw = "vless://test-id@example.invalid:443?encryption=none#TEST"
        standard = base64.b64encode(raw.encode()).decode()
        urlsafe = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

        self.assertEqual(self.proxy.decode_subscription_text(raw), raw)
        self.assertEqual(self.proxy.decode_subscription_text(standard), raw)
        self.assertEqual(self.proxy.decode_subscription_text(urlsafe), raw)
        self.assertEqual(self.proxy.decode_subscription_text("not-a-subscription"), "not-a-subscription")

    def test_vless_tls_and_reality_generate_protocol_specific_settings(self):
        tls_uri = (
            "vless://test-id@example.invalid:443?encryption=none&type=ws&security=tls"
            "&sni=edge.example.invalid&host=cdn.example.invalid&path=%2Fproxy&alpn=h2%2Chttp%2F1.1#TLS"
        )
        tls = self.proxy.build_vless_outbound(tls_uri, set())
        tls_stream = tls["streamSettings"]
        self.assertIn("tlsSettings", tls_stream)
        self.assertNotIn("realitySettings", tls_stream)
        self.assertEqual(tls_stream["tlsSettings"]["serverName"], "edge.example.invalid")
        self.assertEqual(tls_stream["wsSettings"]["headers"]["Host"], "cdn.example.invalid")
        self.assertEqual(tls_stream["wsSettings"]["path"], "/proxy")

        reality_uri = (
            "vless://test-id@example.invalid:443?encryption=none&type=tcp&security=reality"
            "&sni=edge.example.invalid&pbk=public-key&sid=abcd#REALITY"
        )
        reality = self.proxy.build_vless_outbound(reality_uri, set())
        reality_stream = reality["streamSettings"]
        self.assertIn("realitySettings", reality_stream)
        self.assertNotIn("tlsSettings", reality_stream)
        self.assertEqual(reality_stream["realitySettings"]["publicKey"], "public-key")

    def test_invalid_vless_records_are_ignored_without_crashing(self):
        self.assertIsNone(self.proxy.build_vless_outbound("vless://missing-port.example.invalid", set()))
        self.assertIsNone(self.proxy.build_vless_outbound("vless://id@example.invalid:not-a-port", set()))
        self.assertIsNone(self.proxy.build_vless_outbound("https://example.invalid/node", set()))

    def test_systemd_config_path_parser_handles_quotes_and_confdir(self):
        quoted = 'ExecStart=/usr/local/bin/xray run -config "/etc/xray/custom config.json"'
        confdir = "ExecStart=/usr/local/bin/xray run --confdir=/etc/xray/conf.d"

        self.assertEqual(self.proxy.parse_config_path_from_unit(quoted), "/etc/xray/custom config.json")
        self.assertEqual(self.proxy.parse_config_path_from_unit(confdir), "/etc/xray/conf.d/config.json")

    def test_atomic_write_replaces_complete_file_and_preserves_existing_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text("old", encoding="utf-8")
            os.chmod(path, 0o640)

            self.proxy.atomic_write_text(path, "new\n", mode=0o600)

            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])


if __name__ == "__main__":
    unittest.main()
