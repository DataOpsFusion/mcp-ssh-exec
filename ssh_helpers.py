"""Pure helpers for ssh-exec configuration, validation, and error formatting."""

from __future__ import annotations

import os
import re
import socket
from typing import Mapping

import paramiko
import yaml
from paramiko.ssh_exception import BadHostKeyException, NoValidConnectionsError, SSHException

_JOB_ID_RE = re.compile(r"^mcp_bg_[A-Za-z0-9_-]{8,64}$")


def load_hosts(config_path: str) -> dict:
    """Load host definitions from YAML, returning an empty mapping on malformed top-level input."""
    with open(config_path) as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        return {}
    hosts = data.get("hosts") or {}
    return hosts if isinstance(hosts, dict) else {}


def resolve_auth_value(
    host: Mapping[str, object],
    field: str,
    env_field: str,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a literal or env-backed auth field from a host definition."""
    if field in host and host[field] not in (None, ""):
        return str(host[field])
    env_name = host.get(env_field)
    if not env_name:
        return None
    source = os.environ if environ is None else environ
    value = source.get(str(env_name), "").strip()
    if not value:
        raise ValueError(
            "Missing required environment variable '{0}' for host '{1}'".format(
                str(env_name),
                host["host"],
            )
        )
    return value


def resolved_host(host: Mapping[str, object], environ: Mapping[str, str] | None = None) -> dict:
    """Return a host config with env-backed auth material resolved."""
    resolved = dict(host)
    password = resolve_auth_value(resolved, "password", "password_env", environ=environ)
    sudo_password = resolve_auth_value(resolved, "sudo_password", "sudo_password_env", environ=environ)
    private_key_path = resolve_auth_value(resolved, "private_key_path", "private_key_path_env", environ=environ)
    passphrase = resolve_auth_value(resolved, "passphrase", "passphrase_env", environ=environ)

    for key in ("password", "sudo_password", "private_key_path", "passphrase"):
        resolved.pop(key + "_env", None)

    if password:
        resolved["password"] = password
    if sudo_password:
        resolved["sudo_password"] = sudo_password
    if private_key_path:
        resolved["private_key_path"] = private_key_path
    if passphrase:
        resolved["passphrase"] = passphrase
    if "private_key_path" not in resolved and "password" not in resolved:
        raise ValueError(
            "Host '{0}' is missing authentication. Configure password_env or private_key_path.".format(
                resolved["host"]
            )
        )
    return resolved


def resolve_host(name: str, hosts: Mapping[str, dict]) -> tuple[dict | None, str | None]:
    """Return (host_config, error_string). One of them is None."""
    if name not in hosts:
        available = ", ".join(sorted(hosts.keys()))
        return None, "ERROR: Unknown host '{0}'. Available: {1}".format(name, available)
    return dict(hosts[name]), None


def sudo_wrap(command: str, sudo_password: str | None) -> tuple[str, bool]:
    """Return a sudo-aware command string and whether stdin password injection is needed.

    The password is written to the remote process's stdin over the encrypted SSH channel.
    It is never logged or passed via subprocess arguments. Do not log the returned bool
    alongside the password value.
    """
    if sudo_password and command.strip().startswith("sudo"):
        # -S reads password from stdin; -p '' suppresses the prompt so it doesn't
        # appear in captured output. The password travels only inside the SSH tunnel.
        wrapped = command.strip().replace("sudo", "sudo -S -p ''", 1)
        return wrapped, True
    return command, False


def validate_job_id(job_id: str) -> str | None:
    """Validate a background job identifier."""
    if _JOB_ID_RE.fullmatch(job_id):
        return None
    return "ERROR: Invalid job_id format."


def ssh_error(exc: Exception, host: Mapping[str, object]) -> str:
    """Map low-level SSH exceptions to stable MCP-facing error strings."""
    if isinstance(exc, socket.timeout):
        return "ERROR: Connection timed out connecting to " + str(host["host"]) + " — check host is reachable and SSH port is open"
    if isinstance(exc, ConnectionRefusedError):
        return "ERROR: Connection refused by " + str(host["host"]) + " — SSH port may be closed or firewalled"
    if isinstance(exc, paramiko.AuthenticationException):
        return "ERROR: Authentication failed for {0}@{1} — check password or SSH key configuration".format(host["username"], host["host"])
    if isinstance(exc, BadHostKeyException):
        return "ERROR: Host key verification failed for " + str(host["host"]) + " — host key has changed or known_hosts is stale"
    if isinstance(exc, NoValidConnectionsError):
        return "ERROR: Cannot connect to " + str(host["host"]) + " — " + str(exc)
    if isinstance(exc, SSHException):
        return "ERROR: SSH protocol error for " + str(host["host"]) + " — " + str(exc)
    if isinstance(exc, ValueError):
        return "ERROR: " + str(exc)
    return "ERROR: " + type(exc).__name__ + ": " + str(exc)
