# agentic-mcp-foundation

A reusable lifecycle for agent-operated local MCP tools: shared coordinator discovery,
durable SQLite job receipts, bounded execution, idempotency, cooperative cancellation,
verified launchers, and recoverable control-file publication.

Linux and macOS share POSIX file locks. Process ownership uses kernel birth time,
executable identity and boot identity (`/proc` on Linux, `libproc` on macOS), so a reused
PID never grants authority. macOS support is implemented with native and simulated process-identity tests.
A Linux/macOS CI matrix is prepared locally; publishing it requires GitHub `workflow`
permission. Physical MacBook backend qualification belongs to the consuming tool.

## Generate a tool

```bash
uv sync --extra dev
uv run agentic-mcp scaffold receipt-demo --output /tmp/receipt-demo
cd /tmp/receipt-demo
uv sync
uv run python -m receipt_demo
```

The generated MCP adapter implements echo submission, durable job polling, cancellation,
and shared coordinator recovery. It includes plugin metadata and an agent skill. Replace
echo with typed domain operations; provide domain-specific readiness, provisioning,
validation, health checks and probes.

This is an application convention, not a change to the MCP protocol. There is no generic
shell-execution tool. The initial standalone consumer is
[local-vision-mcp](https://github.com/GeorgeQLe/local-vision-mcp); FormSeed's operating
model informed the contract but FormSeed has not been migrated.

See the [lifecycle contract](docs/lifecycle-contract.md) for boundaries, security,
recovery, retention and installation semantics. Distribution currently uses Git tags
and commit pins; the package is not published to PyPI.

## Development

```bash
uv sync --extra dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q
uv build --wheel
```
