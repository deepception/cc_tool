[← README](../../README.md)

# Sandboxing Claude Code

For projects where the agent runs untrusted code, touches cloud credentials, or you just want a stronger trust boundary than file-level permissions, `cc-devcontainer` drops a `.devcontainer/` into your project. Then start the container with whichever tool your IDE supports — Claude Code runs inside Docker with:

- **Filesystem** — project bind-mounted at `/workspace`; nothing outside it (host `~/.ssh`, `~/.config/gh`, host `~/.claude.json`) is visible. Optional read-only cloud creds via `--cloud`.
- **Network** — default-deny egress + ipset allowlist (Anthropic API, npm/PyPI, GitHub IP ranges, VS Code hosts, `astral.sh`, plus cloud hosts when `--cloud` is set). Disable with `--firewall off`.
- **Policy & tooling** — `managed-settings.json` at `/etc/claude-code/` blocks `--dangerously-skip-permissions` from inside; node, python3, and uv are in the image, so a project's own MCP servers run as-is; GitHub via `gh` CLI (host `GITHUB_TOKEN`/`GH_TOKEN` carried through, or `gh auth login` inside).

```bash
cc-devcontainer /path/to/project                # cloud=none, firewall on (safest)
cc-devcontainer /path/to/project --cloud aws    # adds awscli + bind-mounts ~/.aws read-only
cc-devcontainer /path/to/project --cloud gcp    # adds google-cloud-cli + bind-mounts ~/.config/gcloud read-only
cc-devcontainer /path/to/project --firewall off # disable firewall (host-network parity)
cc-devcontainer /path/to/project --share-mcp-auth \
    --mcp-domains api.atlassian.com,mycompany.atlassian.net   # carry host MCPs (Atlassian, etc.) into the container
cc-setup /path/to/project --devcontainer --cloud aws    # one-shot: project setup + container
```

**`--cloud` choices**

| `--cloud` | CLI installed | Mount (read-only) | Env vars exposed | Firewall additions |
|-----------|---------------|-------------------|------------------|---------------------|
| `none` (default) | — | — | — | — |
| `aws` | `awscli` | `~/.aws` → `/home/node/.aws` | `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE`, `AWS_PROFILE`, `AWS_REGION` | `*.amazonaws.com` (sts, s3, ec2, iam, ssm, sso) |
| `gcp` | `google-cloud-cli` | `~/.config/gcloud` → `/home/node/.config/gcloud` | `CLOUDSDK_CONFIG`, `GOOGLE_APPLICATION_CREDENTIALS`, `CLOUDSDK_CORE_PROJECT` | `*.googleapis.com`, `accounts.google.com`, `oauth2.googleapis.com` |

Re-running `cc-devcontainer` is idempotent — pass `--force` to overwrite existing `.devcontainer/` files.

**Bringing host MCPs (Atlassian, GitHub, etc.) into the container** — by default the container only sees project-scope MCPs from the project's own `.mcp.json`, if it has one. To carry your host's user-scope MCPs in, pass `--share-mcp-auth` (bind-mounts host `~/.claude.json` read-only) plus `--mcp-domains` to allowlist the API hosts those MCPs talk to. Tradeoff: anything in the container can read the bind-mounted MCP tokens — the firewall blocks exfiltration to non-allowlisted destinations, but the tokens themselves are visible. Use this only when the agent runs code you trust.

**`--mcp-domains` per MCP** — `--share-mcp-auth` carries over *all* MCPs from `~/.claude.json`; the `--mcp-domains` list just controls which extra hosts the firewall lets through. Add the rows that apply to you:

| MCP | `--mcp-domains` to add |
|-----|------------------------|
| GitHub (`mcp__github`) | *nothing* — GitHub IPs already allowlisted |
| Atlassian (`mcp__atlassian`) | `api.atlassian.com,<company>.atlassian.net` |
| Linear (`mcp__linear`) | `api.linear.app,linear.app` |
| Notion (`mcp__notion`) | `api.notion.com` |
| Slack (`mcp__slack`) | `slack.com,api.slack.com,slack-edge.com` |
| DataHub | `<your-datahub-host>` (e.g. `datahub.mycompany.com`) |
| Sentry MCP | `sentry.io` — *already in base allowlist* |

Examples:

```bash
# GitHub-only user — no --mcp-domains needed
cc-devcontainer /path --cloud aws --share-mcp-auth

# Linear + Notion
cc-devcontainer /path --cloud aws --share-mcp-auth \
    --mcp-domains api.linear.app,api.notion.com

# Atlassian
cc-devcontainer /path --cloud aws --share-mcp-auth \
    --mcp-domains api.atlassian.com,mycompany.atlassian.net
```

If an MCP times out, find what host it's hitting (check that MCP's README or the `env`/`url` fields under its entry in `~/.claude.json`), then re-run with the host added to `--mcp-domains` and rebuild.

**Authenticating Claude Code inside the container** — the firewall allowlist intentionally does NOT include `claude.ai` / `console.anthropic.com`, so OAuth login from inside the container will time out. Authenticate on the host once and pass a token in via env-file (matches Anthropic's official devcontainer pattern). One command does it all:

```bash
cc-token                                 # runs claude setup-token, writes
                                         # CLAUDE_CODE_OAUTH_TOKEN to ~/.zshrc
                                         # (or ~/.bashrc), prints next steps
source ~/.zshrc                          # reload current shell
# Then in Cursor: Dev Containers: Rebuild Container
```

The token is forwarded automatically through `containerEnv` (already wired in [templates/devcontainer/devcontainer.json](../../templates/devcontainer/devcontainer.json)). Re-run `cc-token` whenever the token expires (typically months apart). API-key users can `export ANTHROPIC_API_KEY=...` instead — the env var is also forwarded.

**Starting the container** — `.devcontainer/` follows the open [Dev Container Specification](https://containers.dev/), so any compatible tool works:

| Environment | How to launch |
|-------------|---------------|
| **VS Code / Cursor** | Install the `Dev Containers` extension → open the project folder → Command Palette → `Dev Containers: Reopen in Container` |
| **JetBrains** (IntelliJ, PyCharm, etc.) | File → Remote Development → Dev Containers → `New Dev Container From Local Project` |
| **CLI (any IDE, no extension)** | `npm install -g @devcontainers/cli`, then `devcontainer up --workspace-folder .` and `devcontainer exec --workspace-folder . claude` |

**Note on `Reopen in Container`** — that command only appears in the Command Palette when the currently-opened folder contains a `.devcontainer/`. Run `cc-devcontainer .` first, then open the project; the extension also shows a notification offering to reopen. If you want to start the container without first opening the folder, use `Dev Containers: Open Folder in Container...`.

## `/sandbox` for non-container projects

If you don't run the devcontainer, Claude Code's native `/sandbox` is the lighter-weight alternative: it enforces Bash filesystem and network access at the OS level (Seatbelt on macOS, bubblewrap on Linux) without Docker. Key fact for this setup: path-based Read/Edit deny rules merge into the sandbox filesystem boundary, so the literal-path entries of cc_tool's deny list (`~/.kube/**`, `~/.docker/config.json`, `~/.netrc`, `~/.config/gcloud/**`, `~/.aws/**`, …) gain real OS-level teeth once `/sandbox` is on — a Bash command can no longer reach those paths, not just the Read/Edit tools. Glob-pattern entries (`**/*service-account*.json`, `**/*.pem`, `**/id_rsa*`, …) are OS-enforced only on macOS (Seatbelt supports glob rules); Linux bubblewrap supports literal paths only, so on Linux those entries still bind only the Read/Edit tools. This is especially worth turning on for unattended/loop runs, where Bash would otherwise have no human gate.
