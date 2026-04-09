# mcp-ssh-exec

Execute shell commands on named SSH hosts. Hosts are defined in `hosts.yaml`. Reconnects on every call — no persistent connection state.

## Tools

| Tool | Description |
|------|-------------|
| `list_hosts` | List configured host aliases and their user/host/port |
| `exec_command` | Run a command and wait for output (timeout: 120s) |
| `exec_background` | Run a long command in the background; returns a `job_id` |
| `exec_wait` | Run a command and block until it finishes (timeout: up to 1h) |
| `get_job_output` | Retrieve current output of a background job |

## Usage

Sudo is supported — password is injected automatically from `hosts.yaml`. Use `exec_background` or `exec_wait` for Ansible/Docker builds that run longer than 2 minutes.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `HOSTS_CONFIG` | Path to `hosts.yaml` (default: `./hosts.yaml`) |
| `PORT` | HTTP port to bind (default: `8890`) |

## MCP Connection

```json
{
  "type": "http",
  "url": "http://<host>:<PORT>/mcp"
}
```

## CI/CD

Images are built on every push to `main` and pushed to:
- Harbor: `harbor.homeserverlocal.com/mcp/mcp-ssh-exec:latest`
- Docker Hub: `dataopsfusion/mcp-ssh-exec:latest`

