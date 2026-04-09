"""
SSH Exec MCP Server
Tools: exec_command, exec_background, exec_wait, get_job_output, list_hosts
Hosts defined in hosts.yaml — reconnect-on-demand per call.
"""

import os
import socket
import time
import yaml
import paramiko
from mcp.server.fastmcp import FastMCP
from paramiko.ssh_exception import NoValidConnectionsError

CONFIG_PATH = os.environ.get("HOSTS_CONFIG", os.path.join(os.path.dirname(__file__), "hosts.yaml"))
PORT = int(os.environ.get("PORT", "8890"))


def _load_hosts() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f).get("hosts", {})


def _connect(h: dict) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = dict(
        hostname=h["host"],
        port=h.get("port", 22),
        username=h["username"],
        timeout=10,
        banner_timeout=15,
        auth_timeout=15,
    )
    if "private_key_path" in h:
        kw["key_filename"] = h["private_key_path"]
    elif "key_file" in h:
        kw["key_filename"] = h["key_file"]
    else:
        kw["password"] = h.get("password", "")
    client.connect(**kw)
    return client


def _resolve(name: str):
    """Return (host_config, error_string). One of them is None."""
    hosts = _load_hosts()
    if name not in hosts:
        available = ", ".join(sorted(hosts.keys()))
        return None, "ERROR: Unknown host '" + name + "'. Available: " + available
    return hosts[name], None


def _sudo_wrap(command: str, sudo_password: str | None):
    """Return (wrapped_command, inject_password)."""
    if sudo_password and command.strip().startswith("sudo"):
        wrapped = command.strip().replace("sudo", "sudo -S -p ''", 1)
        return wrapped, True
    return command, False


def _run(client: paramiko.SSHClient, command: str, sudo_password: str | None, timeout: int):
    run_cmd, inject = _sudo_wrap(command, sudo_password)
    stdin, stdout, stderr = client.exec_command(run_cmd, timeout=timeout, get_pty=False)
    if inject:
        stdin.write(sudo_password + "\n")
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


def _ssh_error(exc: Exception, h: dict) -> str:
    if isinstance(exc, socket.timeout):
        return "ERROR: Connection timed out to " + h["host"]
    if isinstance(exc, paramiko.AuthenticationException):
        return "ERROR: Authentication failed for " + h["username"] + "@" + h["host"]
    if isinstance(exc, NoValidConnectionsError):
        return "ERROR: Cannot connect to " + h["host"] + " — " + str(exc)
    return "ERROR: " + type(exc).__name__ + ": " + str(exc)


mcp = FastMCP("ssh-exec", host="0.0.0.0", port=PORT, stateless_http=True)


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
def list_hosts() -> str:
    """List all available named SSH hosts that can be used with exec_command / exec_background."""
    hosts = _load_hosts()
    if not hosts:
        return "No hosts configured."
    return "\n".join(
        name + ": " + h["username"] + "@" + h["host"] + ":" + str(h.get("port", 22))
        for name, h in hosts.items()
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False})
def exec_command(name: str, command: str, timeout: int = 120) -> str:
    """
    Execute a shell command on a named SSH host and wait for it to finish.

    Args:
        name:    Host alias (use list_hosts to see options).
        command: Shell command. Sudo is supported — password injected automatically.
        timeout: Execution timeout in seconds (default 120). For tasks longer than
                 10 minutes use exec_wait or exec_background instead.

    Returns stdout, [stderr] section if any, and [exit: N] on the last line.
    Returns ERROR: on connection or auth failure.
    """
    h, err = _resolve(name)
    if err:
        return err
    client = None
    try:
        client = _connect(h)
        return _run(client, command, h.get("sudo_password"), timeout)
    except Exception as exc:
        return _ssh_error(exc, h)
    finally:
        if client:
            client.close()


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False})
def exec_background(name: str, command: str) -> str:
    """
    Run a long-running command in the background on a named SSH host.
    The command survives session disconnect. Output is saved to a log file on the remote host.

    Args:
        name:    Host alias (use list_hosts to see options).
        command: Shell command to run. Sudo is supported — password injected automatically.

    Returns a job_id. Use get_job_output(name, job_id) to check progress or retrieve output.
    """
    h, err = _resolve(name)
    if err:
        return err

    job_id = "mcp_bg_" + str(int(time.time()))
    log_file = "/tmp/" + job_id + ".log"
    pid_file = "/tmp/" + job_id + ".pid"

    run_cmd, inject = _sudo_wrap(command, h.get("sudo_password"))
    bg_cmd = (
        "nohup bash -c " + repr(run_cmd) + " > " + log_file + " 2>&1 & "
        "echo $! > " + pid_file + " && echo " + job_id
    )

    client = None
    try:
        client = _connect(h)
        stdin, stdout, stderr = client.exec_command(bg_cmd, timeout=15, get_pty=False)
        if inject:
            stdin.write(h["sudo_password"] + "\n")
            stdin.flush()
        stdin.close()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            err_out = stderr.read().decode("utf-8", errors="replace").strip()
            return "ERROR: Failed to start background job. " + err_out
        return "Started job_id=" + job_id + "  log=" + log_file + "  host=" + name
    except Exception as exc:
        return _ssh_error(exc, h)
    finally:
        if client:
            client.close()


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False})
def exec_wait(name: str, command: str, timeout: int = 3600, poll_interval: int = 10) -> str:
    """
    Run a long-running command in the background and block until it finishes.
    Ideal for Ansible playbooks, Docker builds, package installs, or any task
    that may take minutes to hours.

    Args:
        name:          Host alias (use list_hosts to see options).
        command:       Shell command to run. Sudo is supported.
        timeout:       Maximum total wait time in seconds (default 3600 = 1 hour).
                       Returns partial output if exceeded.
        poll_interval: How often to check for completion in seconds (default 10).

    Returns the full stdout/stderr output of the command once it completes,
    plus [exit: N] and [duration: Xs] on the last lines.
    Returns ERROR: on connection or auth failure.
    """
    h, err = _resolve(name)
    if err:
        return err

    # Start as background job
    job_id = "mcp_bg_" + str(int(time.time()))
    log_file = "/tmp/" + job_id + ".log"
    pid_file = "/tmp/" + job_id + ".pid"
    exit_file = "/tmp/" + job_id + ".exit"

    run_cmd, inject = _sudo_wrap(command, h.get("sudo_password"))
    # Wrap to capture exit code separately
    bg_cmd = (
        "nohup bash -c "
        + repr(run_cmd + "; echo $? > " + exit_file)
        + " > " + log_file + " 2>&1 & "
        "echo $! > " + pid_file + " && echo " + job_id
    )

    client = None
    try:
        client = _connect(h)
        stdin, stdout, stderr = client.exec_command(bg_cmd, timeout=15, get_pty=False)
        if inject:
            stdin.write(h["sudo_password"] + "\n")
            stdin.flush()
        stdin.close()
        start_out = stdout.read().decode("utf-8", errors="replace").strip()
        if stdout.channel.recv_exit_status() != 0:
            err_out = stderr.read().decode("utf-8", errors="replace").strip()
            return "ERROR: Failed to start job. " + err_out
        client.close()
        client = None
    except Exception as exc:
        return _ssh_error(exc, h)
    finally:
        if client:
            client.close()

    # Poll until done
    start_time = time.time()
    check_cmd = (
        "PID=$(cat " + pid_file + " 2>/dev/null); "
        "if kill -0 $PID 2>/dev/null; then echo RUNNING; "
        "else echo DONE; fi"
    )

    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout:
            # Timeout — return whatever output we have
            client2 = None
            try:
                client2 = _connect(h)
                _, out_s, _ = client2.exec_command("cat " + log_file + " 2>/dev/null", timeout=15)
                output = out_s.read().decode("utf-8", errors="replace").rstrip()
            except Exception:
                output = "(could not retrieve output)"
            finally:
                if client2:
                    client2.close()
            return output + "\n[exit: TIMEOUT after " + str(int(elapsed)) + "s]"

        time.sleep(poll_interval)

        client3 = None
        try:
            client3 = _connect(h)
            _, status_out, _ = client3.exec_command(check_cmd, timeout=15)
            status = status_out.read().decode("utf-8", errors="replace").strip()
            client3.close()
            client3 = None
        except Exception as exc:
            return "ERROR polling job: " + str(exc)
        finally:
            if client3:
                client3.close()

        if status == "DONE":
            elapsed = time.time() - start_time
            client4 = None
            try:
                client4 = _connect(h)
                _, out_s, _ = client4.exec_command(
                    "cat " + log_file + " 2>/dev/null; "
                    "echo \"[exit: $(cat " + exit_file + " 2>/dev/null || echo unknown)]\"; "
                    "echo \"[duration: " + str(int(elapsed)) + "s]\"",
                    timeout=30
                )
                return out_s.read().decode("utf-8", errors="replace").rstrip()
            except Exception as exc:
                return "ERROR reading output: " + str(exc)
            finally:
                if client4:
                    client4.close()


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
def get_job_output(name: str, job_id: str) -> str:
    """
    Retrieve the current output of a background job started with exec_background.

    Args:
        name:   Same host alias used when starting the job.
        job_id: The job_id returned by exec_background.

    Returns the log contents so far, plus whether the job is still running or finished.
    """
    h, err = _resolve(name)
    if err:
        return err

    log_file = "/tmp/" + job_id + ".log"
    pid_file = "/tmp/" + job_id + ".pid"

    check_cmd = (
        "if [ ! -f " + pid_file + " ]; then echo STATUS=NOT_FOUND; exit 0; fi; "
        "PID=$(cat " + pid_file + "); "
        "if kill -0 $PID 2>/dev/null; then echo STATUS=RUNNING; else echo STATUS=DONE; fi; "
        "echo '---'; "
        "cat " + log_file + " 2>/dev/null || echo '(no output yet)'"
    )

    client = None
    try:
        client = _connect(h)
        stdin, stdout, _ = client.exec_command(check_cmd, timeout=15, get_pty=False)
        stdin.close()
        return stdout.read().decode("utf-8", errors="replace").rstrip()
    except Exception as exc:
        return _ssh_error(exc, h)
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
