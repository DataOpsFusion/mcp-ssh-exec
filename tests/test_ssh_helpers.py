from __future__ import annotations

import sys
import tempfile
import types
import socket
import unittest
from pathlib import Path

if "paramiko" not in sys.modules:
    fake_paramiko = types.ModuleType("paramiko")

    class _AuthenticationException(Exception):
        pass

    class _SSHException(Exception):
        pass

    class _BadHostKeyException(Exception):
        pass

    class _NoValidConnectionsError(Exception):
        def __init__(self, *args, **kwargs):
            errors = kwargs.get("errors") if kwargs else (args[0] if args else None)
            super().__init__(str(errors or {}))

    fake_paramiko.AuthenticationException = _AuthenticationException
    fake_paramiko.SSHException = _SSHException
    fake_paramiko.AutoAddPolicy = type("AutoAddPolicy", (), {})
    fake_paramiko.RejectPolicy = type("RejectPolicy", (), {})
    fake_paramiko.SSHClient = type("SSHClient", (), {})

    fake_exc = types.ModuleType("paramiko.ssh_exception")
    fake_exc.BadHostKeyException = _BadHostKeyException
    fake_exc.NoValidConnectionsError = _NoValidConnectionsError
    fake_exc.SSHException = _SSHException

    sys.modules["paramiko"] = fake_paramiko
    sys.modules["paramiko.ssh_exception"] = fake_exc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ssh_helpers import (
    load_hosts,
    resolve_auth_value,
    resolve_host,
    resolved_host,
    ssh_error,
    sudo_wrap,
    validate_job_id,
)

paramiko = sys.modules["paramiko"]
BadHostKeyException = sys.modules["paramiko.ssh_exception"].BadHostKeyException
NoValidConnectionsError = sys.modules["paramiko.ssh_exception"].NoValidConnectionsError
SSHException = sys.modules["paramiko.ssh_exception"].SSHException


def _write_temp_hosts_yaml(content: str) -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    handle.write(content)
    handle.flush()
    handle.close()
    return handle.name


class LoadHostsTests(unittest.TestCase):
    def test_load_hosts_returns_mapping_when_hosts_exist(self) -> None:
        path = _write_temp_hosts_yaml(
            "hosts:\n"
            "  devops:\n"
            "    host: 192.168.0.70\n"
            "    username: devops\n"
            "    password_env: SSH_DEVOPS_SERVER_PASSWORD\n"
        )
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))

        hosts = load_hosts(path)

        self.assertIn("devops", hosts)
        self.assertEqual(hosts["devops"]["host"], "192.168.0.70")

    def test_load_hosts_returns_empty_mapping_for_non_mapping_root(self) -> None:
        path = _write_temp_hosts_yaml("- just\n- a\n- list\n")
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))

        self.assertEqual(load_hosts(path), {})

    def test_load_hosts_returns_empty_mapping_for_non_mapping_hosts_key(self) -> None:
        path = _write_temp_hosts_yaml("hosts:\n  - bad\n  - shape\n")
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))

        self.assertEqual(load_hosts(path), {})


class AuthResolutionTests(unittest.TestCase):
    def test_resolve_auth_value_prefers_literal_value(self) -> None:
        host = {"host": "example", "password": "literal", "password_env": "SHOULD_NOT_BE_USED"}

        value = resolve_auth_value(host, "password", "password_env", environ={"SHOULD_NOT_BE_USED": "env"})

        self.assertEqual(value, "literal")

    def test_resolve_auth_value_reads_environment_backed_value(self) -> None:
        host = {"host": "example", "password_env": "SSH_PASSWORD"}

        value = resolve_auth_value(host, "password", "password_env", environ={"SSH_PASSWORD": "secret"})

        self.assertEqual(value, "secret")

    def test_resolve_auth_value_raises_when_env_is_missing(self) -> None:
        host = {"host": "example", "password_env": "SSH_PASSWORD"}

        with self.assertRaisesRegex(ValueError, "Missing required environment variable 'SSH_PASSWORD'"):
            resolve_auth_value(host, "password", "password_env", environ={})

    def test_resolved_host_merges_password_fields(self) -> None:
        host = {
            "host": "example",
            "username": "devops",
            "password_env": "SSH_PASSWORD",
            "sudo_password_env": "SSH_SUDO_PASSWORD",
        }

        resolved = resolved_host(host, environ={"SSH_PASSWORD": "secret", "SSH_SUDO_PASSWORD": "sudo-secret"})

        self.assertEqual(resolved["password"], "secret")
        self.assertEqual(resolved["sudo_password"], "sudo-secret")
        self.assertNotIn("password_env", resolved)
        self.assertNotIn("sudo_password_env", resolved)

    def test_resolved_host_allows_key_only_hosts(self) -> None:
        host = {
            "host": "example",
            "username": "devops",
            "private_key_path": "/ssh-keys/id_ed25519",
        }

        resolved = resolved_host(host, environ={})

        self.assertEqual(resolved["private_key_path"], "/ssh-keys/id_ed25519")

    def test_resolved_host_requires_authentication(self) -> None:
        host = {"host": "example", "username": "devops"}

        with self.assertRaisesRegex(ValueError, "missing authentication"):
            resolved_host(host, environ={})


class ResolutionAndValidationTests(unittest.TestCase):
    def test_resolve_host_returns_host_when_present(self) -> None:
        host, error = resolve_host("devops", {"devops": {"host": "127.0.0.1", "username": "devops"}})

        self.assertEqual(host["host"], "127.0.0.1")
        self.assertIsNone(error)

    def test_resolve_host_returns_error_when_missing(self) -> None:
        host, error = resolve_host("missing", {"devops": {"host": "127.0.0.1", "username": "devops"}})

        self.assertIsNone(host)
        self.assertEqual(error, "ERROR: Unknown host 'missing'. Available: devops")

    def test_validate_job_id_accepts_expected_format(self) -> None:
        self.assertIsNone(validate_job_id("mcp_bg_abc12345"))

    def test_validate_job_id_rejects_shell_injection_payload(self) -> None:
        self.assertEqual(validate_job_id("mcp_bg_abc;rm -rf /"), "ERROR: Invalid job_id format.")

    def test_sudo_wrap_injects_password_for_sudo_commands(self) -> None:
        wrapped, inject = sudo_wrap("sudo systemctl restart ssh", "secret")

        self.assertTrue(inject)
        self.assertEqual(wrapped, "sudo -S -p '' systemctl restart ssh")

    def test_sudo_wrap_keeps_non_sudo_command(self) -> None:
        wrapped, inject = sudo_wrap("echo ok", "secret")

        self.assertFalse(inject)
        self.assertEqual(wrapped, "echo ok")


class ErrorMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = {"host": "192.168.0.70", "username": "devops"}

    def test_timeout_maps_cleanly(self) -> None:
        self.assertEqual(ssh_error(socket.timeout(), self.host), "ERROR: Connection timed out to 192.168.0.70")

    def test_authentication_error_maps_cleanly(self) -> None:
        error = ssh_error(paramiko.AuthenticationException("bad credentials"), self.host)

        self.assertEqual(error, "ERROR: Authentication failed for devops@192.168.0.70")

    def test_bad_host_key_maps_cleanly(self) -> None:
        error = ssh_error(
            BadHostKeyException("192.168.0.70", None, None),
            self.host,
        )

        self.assertEqual(error, "ERROR: Host key verification failed for 192.168.0.70")

    def test_no_valid_connections_maps_cleanly(self) -> None:
        error = ssh_error(
            NoValidConnectionsError({("192.168.0.70", 22): ConnectionRefusedError("refused")}),
            self.host,
        )

        self.assertTrue(error.startswith("ERROR: Cannot connect to 192.168.0.70"))

    def test_ssh_exception_maps_cleanly(self) -> None:
        error = ssh_error(SSHException("protocol error"), self.host)

        self.assertEqual(error, "ERROR: SSH error for 192.168.0.70 — protocol error")

    def test_value_error_maps_cleanly(self) -> None:
        error = ssh_error(ValueError("bad config"), self.host)

        self.assertEqual(error, "ERROR: bad config")

    def test_generic_error_maps_cleanly(self) -> None:
        error = ssh_error(RuntimeError("boom"), self.host)

        self.assertEqual(error, "ERROR: RuntimeError: boom")


if __name__ == "__main__":
    unittest.main()
