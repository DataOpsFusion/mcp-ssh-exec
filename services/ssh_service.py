from __future__ import annotations

import secrets
import shlex
import threading
from dataclasses import dataclass

import paramiko

from settings import Settings
from ssh_helpers import load_hosts, resolve_host, resolved_host, ssh_error, sudo_wrap, validate_job_id

# Module-level SSH connection pool keyed by host alias.
# Connections are reused across calls to avoid per-call TCP+auth overhead.
# _POOL_LOCK serialises pool mutations; individual paramiko clients are not
# thread-safe so callers must not share a client across concurrent requests.
_POOL: dict[str, paramiko.SSHClient] = {}
_POOL_LOCK = threading.Lock()


@dataclass
class SSHService:
    settings: Settings

    def _load_hosts(self) -> dict:
        """Load hosts from the configuration file."""
        return load_hosts(str(self.settings.config_path))

    def _connect(self, h: dict) -> paramiko.SSHClient:
        """Establish an SSH connection to the resolved host."""
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        if self.settings.known_hosts_path.exists():
            client.load_host_keys(str(self.settings.known_hosts_path))
        if self.settings.allow_unknown_hosts:
            # SECURITY RISK: AutoAddPolicy accepts any host key without verification.
            # This allows man-in-the-middle attacks. Only enable on isolated, trusted networks.
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        resolved = resolved_host(h)
        kw = dict(
            hostname=resolved["host"],
            port=resolved.get("port", 22),
            username=resolved["username"],
            timeout=self.settings.connect_timeout,
            banner_timeout=self.settings.banner_timeout,
            auth_timeout=self.settings.auth_timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        if "private_key_path" in resolved:
            kw["key_filename"] = resolved["private_key_path"]
            if "passphrase" in resolved:
                kw["passphrase"] = resolved["passphrase"]
        else:
            kw["password"] = resolved.get("password", "")
        client.connect(**kw)
        return client

    def _get_connection(self, name: str, h: dict) -> paramiko.SSHClient:
        """Return a cached or new SSH connection for the given host alias.

        If the cached connection is no longer active it is evicted and a fresh one returned.
        """
        with _POOL_LOCK:
            client = _POOL.get(name)
            if client is not None:
                transport = client.get_transport()
                if transport is not None and transport.is_active():
                    return client
                # Stale entry — remove before reconnecting
                try:
                    client.close()
                except Exception:
                    pass
                del _POOL[name]
            client = self._connect(h)
            _POOL[name] = client
            return client

    def _evict_connection(self, name: str) -> None:
        """Close and remove a connection from the pool."""
        with _POOL_LOCK:
            client = _POOL.pop(name, None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _resolve(self, name: str) -> dict:
        """Resolve a host by name from the configuration, raising ValueError if not found."""
        h, err = resolve_host(name, self._load_hosts())
        if err:
            raise ValueError(err)
        if h is None:
            raise ValueError(f"ERROR: Unknown host '{name}'.")
        return h

    def _run(self, client: paramiko.SSHClient, command: str, sudo_password: str | None, timeout: int) -> str:
        """Execute a shell command via the SSH client and return the output."""
        run_cmd, inject = sudo_wrap(command, sudo_password)
        stdin, stdout, stderr = client.exec_command(run_cmd, timeout=timeout, get_pty=False)
        if inject:
            stdin.write((sudo_password or "") + "\n")
            stdin.flush()
        stdin.close()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        parts = []
        if out.strip():
            parts.append(out.rstrip())
        if err.strip():
            parts.append("[stderr]\n" + err.rstrip())
        parts.append("[exit: " + str(exit_code) + "]")
        return "\n".join(parts)

    def list_hosts(self) -> str:
        """List all configured SSH hosts."""
        hosts = self._load_hosts()
        if not hosts:
            return "No hosts configured."
        return "\n".join(
            name + ": " + h["username"] + "@" + h["host"] + ":" + str(h.get("port", 22))
            for name, h in hosts.items()
        )

    def exec_command(self, name: str, command: str, timeout: int = 30) -> str:
        """Run a shell command on a host."""
        h = self._resolve(name)
        warning = ""
        if self.settings.allow_unknown_hosts:
            warning = "[WARNING: allow_unknown_hosts=True — host key not verified, MITM risk]\n"
        try:
            resolved = resolved_host(h)
            client = self._get_connection(name, h)
            try:
                result = self._run(client, command, resolved.get("sudo_password"), timeout)
            except Exception:
                self._evict_connection(name)
                raise
            return warning + result
        except Exception as exc:
            if isinstance(exc, (ValueError, RuntimeError)):
                raise
            raise RuntimeError(ssh_error(exc, h)) from exc

    def exec_background(self, name: str, command: str) -> str:
        """Run a background command on a host."""
        h = self._resolve(name)
        if command.strip().startswith("sudo"):
            raise ValueError("ERROR: Background sudo commands are blocked. Use exec_command for privileged commands.")
        job_id = "mcp_bg_" + secrets.token_hex(12)
        log_file = "/tmp/" + job_id + ".log"
        pid_file = "/tmp/" + job_id + ".pid"
        bg_cmd = (
            "nohup bash -lc " + shlex.quote(command) +
            " > " + shlex.quote(log_file) + " 2>&1 < /dev/null & "
            "echo $! > " + shlex.quote(pid_file) + " && echo " + shlex.quote(job_id)
        )
        client = None
        try:
            client = self._connect(h)
            stdin, stdout, stderr = client.exec_command(bg_cmd, timeout=15, get_pty=False)
            stdin.close()
            out = stdout.read().decode("utf-8", errors="replace").strip()
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err_out = stderr.read().decode("utf-8", errors="replace").strip()
                raise RuntimeError("ERROR: Failed to start background job. " + err_out)
            if out != job_id:
                raise RuntimeError("ERROR: Failed to confirm background job start.")
            return "Started job_id=" + job_id + "  log=" + log_file + "  host=" + name
        except Exception as exc:
            if isinstance(exc, (ValueError, RuntimeError)):
                raise
            raise RuntimeError(ssh_error(exc, h)) from exc
        finally:
            if client:
                client.close()

    def get_job_output(self, name: str, job_id: str) -> str:
        """Get output from a background job."""
        h = self._resolve(name)
        err = validate_job_id(job_id)
        if err:
            raise ValueError(err)
        log_file = "/tmp/" + job_id + ".log"
        pid_file = "/tmp/" + job_id + ".pid"
        check_cmd = (
            "if [ ! -f " + shlex.quote(pid_file) + " ]; then echo STATUS=NOT_FOUND; exit 0; fi; "
            "PID=$(cat " + shlex.quote(pid_file) + "); "
            "if kill -0 $PID 2>/dev/null; then echo STATUS=RUNNING; else echo STATUS=DONE; fi; "
            "echo '---'; "
            "cat " + shlex.quote(log_file) + " 2>/dev/null || echo '(no output yet)'"
        )
        client = None
        try:
            client = self._connect(h)
            stdin, stdout, _ = client.exec_command(check_cmd, timeout=15, get_pty=False)
            stdin.close()
            return stdout.read().decode("utf-8", errors="replace").rstrip()
        except Exception as exc:
            if isinstance(exc, (ValueError, RuntimeError)):
                raise
            raise RuntimeError(ssh_error(exc, h)) from exc
        finally:
            if client:
                client.close()
