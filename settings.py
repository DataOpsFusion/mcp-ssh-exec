from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    config_path: Path           # Path to hosts.yaml host registry
    known_hosts_path: Path      # Path to SSH known_hosts file for host key verification
    allow_unknown_hosts: bool   # If True, auto-accept unknown host keys (INSECURE — use only on trusted LANs)
    port: int                   # TCP port this MCP server listens on
    connect_timeout: int        # Seconds to wait for TCP connection to SSH server
    banner_timeout: int         # Seconds to wait for SSH banner after TCP connect
    auth_timeout: int           # Seconds to wait for SSH authentication to complete


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    env = environ if environ is not None else os.environ
    base_dir = Path(env.get("SSH_EXEC_BASE_DIR", Path(__file__).resolve().parent)).resolve()
    return Settings(
        config_path=Path(env.get("HOSTS_CONFIG", str(base_dir / "hosts.yaml"))),
        known_hosts_path=Path(env.get("SSH_KNOWN_HOSTS", str(base_dir / "known_hosts"))),
        allow_unknown_hosts=str(env.get("SSH_ALLOW_UNKNOWN_HOSTS", "")).strip().lower() in {"1", "true", "yes"},
        port=int(env.get("PORT", "8890")),
        connect_timeout=int(env.get("SSH_CONNECT_TIMEOUT", "10")),
        banner_timeout=int(env.get("SSH_BANNER_TIMEOUT", "15")),
        auth_timeout=int(env.get("SSH_AUTH_TIMEOUT", "15")),
    )
