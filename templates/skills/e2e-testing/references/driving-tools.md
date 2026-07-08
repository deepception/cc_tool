# Driving the App Under Test

How to launch, drive, and evidence each app type. Probe for MCP drivers with ToolSearch (keywords: "playwright", "browser", "chrome", "screenshot", "device") — absence after probing means the driver is not connected in this session.

| App type | Launch discovery | Driver probe order | A scenario step is | Evidence to capture |
|----------|-----------------|--------------------|--------------------|---------------------|
| Web (SPA / server-rendered) | README, `run` skill, package.json scripts, docker-compose | Playwright MCP → chrome-devtools MCP | navigate / click / fill / assert visible state | screenshot, console errors, failing request (URL, status) |
| API | README, OpenAPI/Swagger route, docker-compose | none needed — `curl`/`httpie` via Bash | one request (or an authed sequence) + response assertions | full request/response, status, timing |
| CLI | README, `--help`, Makefile | none needed — Bash | one invocation with args/stdin + exit-code/stdout assertions | command, exit code, stdout/stderr |
| TUI | README, `--help` | tmux (`send-keys` / `capture-pane`) | keypress sequence + captured screen state | pane captures before/after |
| Mobile / desktop | build docs, device config | vision-agent MCP (device_launch, tap, screenshot) | tap / type_text / swipe + screenshot assertion | screenshot per step |

Before the first scenario: start the stack, wait for the health probe, log in once per role to validate fixtures. A failing probe stops the run — report it rather than executing against a dead backend.

## No driver available

For a GUI app with no browser/device MCP connected: state it plainly, offer paired mode, and name the fix for next time — for web, a Playwright MCP server (`claude mcp add playwright -- npx @playwright/mcp@latest`); for mobile, vision-agent. Do not simulate agent-run mode from source reading — executing means observing the running app.

## Unknown app type

Adapt by analogy from the nearest row and say so in the plan doc's header.
