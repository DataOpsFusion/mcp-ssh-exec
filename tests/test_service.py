from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

if "paramiko" not in sys.modules:
    fake_paramiko = types.ModuleType("paramiko")
    fake_paramiko.AuthenticationException = type("AuthenticationException", (Exception,), {})
    fake_paramiko.SSHException = type("SSHException", (Exception,), {})
    fake_paramiko.AutoAddPolicy = type("AutoAddPolicy", (), {})
    fake_paramiko.RejectPolicy = type("RejectPolicy", (), {})
    fake_paramiko.SSHClient = type("SSHClient", (), {})
    fake_exc = types.ModuleType("paramiko.ssh_exception")
    fake_exc.BadHostKeyException = type("BadHostKeyException", (Exception,), {})
    fake_exc.NoValidConnectionsError = type("NoValidConnectionsError", (Exception,), {})
    fake_exc.SSHException = fake_paramiko.SSHException
    sys.modules["paramiko"] = fake_paramiko
    sys.modules["paramiko.ssh_exception"] = fake_exc

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ssh_service import SSHService
from settings import Settings


class SSHServiceTest(unittest.TestCase):
    def test_list_hosts_reads_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "hosts.yaml"
            config.write_text(
                "hosts:\n"
                "  devops:\n"
                "    host: 127.0.0.1\n"
                "    username: devops\n"
            )
            service = SSHService(
                Settings(
                    config_path=config,
                    known_hosts_path=base / "known_hosts",
                    allow_unknown_hosts=False,
                    port=8890,
                )
            )

            self.assertIn("devops: devops@127.0.0.1:22", service.list_hosts())


if __name__ == "__main__":
    unittest.main()
