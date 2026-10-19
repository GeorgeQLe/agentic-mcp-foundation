# Agentic MCP lifecycle contract v1

This is a repository-owned convention, not an extension to the MCP wire specification.
It captures the reusable lifecycle shared by local-vision and FormSeed's operating model.
FormSeed is a reference implementation in TypeScript; it has not been migrated to this Python
package. Tools retain their own domain schemas, validation, authorization and quality criteria.

## Implementation boundary

`agentic-mcp-foundation` is independently buildable and has no vision, Blender or model
dependencies. It supplies SQLite job receipts, bounded execution, idempotency, cancellation,
private loopback RPC, startup and ownership locks, coordinator generation discovery, recoverable
publication and a standard-library launcher. Linux and macOS use POSIX supervisor locks; process ownership recovery uses Linux `/proc`
or macOS `libproc`. macOS hardware qualification remains the consumer's responsibility.
This is an initial reusable foundation, not a published package-index release.

An adapter supplies:

- Typed operations and their input validation, including resource/path authorization.
- Per-capability readiness blockers and executable repair actions where supported.
- Backend installation, stop/start, a meaningful health check and real operation probes.
- Output evidence, model/producer provenance and domain quality qualification.
- An agent skill explaining routing, recovery and limitations.

`agentic-mcp scaffold receipt-demo --output /tmp/receipt-demo` creates a working second adapter,
plugin declaration and skill. Its echo operation demonstrates the lifecycle independently of
local-vision. Replace that operation with domain handlers; add backend-specific readiness,
provisioning and probes. Install from a Git commit/tag or build the foundation wheel with `uv build --wheel`. The
starter's plugin expects the consumer package installed in its selected Python environment;
verified standalone distribution remains an explicit integration step, illustrated by local-vision.

## Identity and readiness

Every coordinator publishes a transport protocol version, tool contract major, build digest and
application version when available. Discovery checks the required contract before attaching.
An incompatible major reports `INCOMPATIBLE_RUNTIME`; it does not execute a prepared payload.
A compatible different build retires the old coordinator only when its job engine is idle.

The private endpoint record is mode 0600 beneath a user-owned runtime directory. RPC binds to
loopback, authenticates a random token, bounds request bodies and disables HTTP proxy inheritance.
Public health/status responses do not include that token. Same-user OS processes are inside the
trust boundary; this is not an OS sandbox or a multi-user network service.

Readiness reports connection health separately from individual capability readiness. Each capability
has `ready` and `blockers`; blockers have a stable `code`, explanatory `message` and optional typed
`repair`. Backend readiness, a successful probe and workload quality qualification are distinct.
The generic scaffold intentionally has no backend to provision and reports only coordinator health.

## Job receipts

Job receipts contain:

| Field | Meaning |
| --- | --- |
| `id`, `idempotency_key` | Durable identity and caller retry identity |
| `operation`, `request` | Validated intent; local-vision binds inference to media SHA256 |
| `state`, `phase` | Lifecycle state and bounded progress message |
| `result`, `error` | Retained outcome; errors have `code`, `message`, `retryable` |
| `created`, `updated` | Unix timestamps |
| `cancel_requested` | Cooperative cancellation request |

States are `queued`, `running`, `succeeded`, `failed`, `cancelled` and `interrupted`.
Use the same key for the same intent after reconnecting; a changed operation, request or media hash
returns `IDEMPOTENCY_CONFLICT`. Replaying a terminal failed/cancelled/interrupted receipt does not
authorize execution again. Inspect state and use a fresh key for a new intentional attempt.

The coordinator persists the job before acknowledging submission and owns execution independently
of MCP client connections. After coordinator loss, exclusive ownership is acquired before marking
unfinished receipts `interrupted`. Never automatically replay operations whose completion is
uncertain. Completed results remain available. Local-vision scopes discovery and receipts to its
configuration and normalized allowed roots; another scope cannot retrieve those receipts.

Queued work has a capacity bound and deadline. Expiration is enforced when polled or selected by
a worker; it never reaches a domain handler after expiration. A queued cancellation never reaches
the handler. Active cancellation is cooperative; capacity remains occupied until the handler returns.
Cancelled/expired queue entries are removed by workers, so capacity can remain occupied until those
entries are consumed. Idempotency keys and receipts currently have no automatic retention policy;
operators should protect and size their runtime storage appropriately.

## Installation and updates

Bootstrap/doctor must work outside MCP: a server that cannot start cannot repair itself through a
tool call. A stable launcher lives outside source checkouts and host plugin caches. Immutable
side-by-side Python payloads carry a file hash manifest, application identity, contract major and
installed smoke-test evidence. Python itself remains a host prerequisite. Large model/native
backend payloads have separate adapter manifests and lifecycles.

The installer stages and tests before publishing control state. Small control files and the plugin
directory have a durable publication journal: caught failures restore the old state; a later
installer recovers a publication interrupted by process loss. Prepared large payloads may remain
after abrupt termination and are retained for inspection. Old payloads are not automatically deleted,
so running clients never lose their interpreter or loaded code to cache cleanup.

Backend updates follow `prepare → drain → activate → health-check`. Hold exclusive backend usage
ownership through activation, checks and rollback. Local-vision shares backend locks across scopes
inside one installation. Separate installation homes must not jointly manage one Ollama endpoint.
Unrelated external services are reusable for inference but cannot be stopped or updated without
verified ownership. Linux/macOS ownership records bind PID, boot ID, process start time and executable;
a reused PID does not grant authority. Coordinators may recover an owned orphan backend after loss.

An Ollama update stages a complete release and verifies it before activation. Failed startup, model
pull or capability health check restores the previous selection and restarts the previous owned
server. Model tag updates/downloads are not transactional with binary rollback. Native drivers,
external services, AMD/JetPack add-ons and OS package installation remain adapter/host concerns.

## Conformance evidence

Both the production adapter and the generated second adapter are exercised over actual MCP stdio.
Tests cover reconnect recovery without duplicate execution, key conflicts, media identity,
private scopes, coordinator loss, cancellation/capacity, update rollback, process identity recovery,
manifest corruption and recoverable publication. CI additionally builds both wheels, installs locked
dependencies in an isolated payload and exercises its actual MCP launch command and catalog.

`scripts/live_acceptance.py` is an opt-in real-backend check, outside ordinary CI. It uses a dedicated
installation home and endpoint, downloads Ollama, runs the configured model and retains the receipt
and probe report. It can reuse existing cached blobs without changing the original cache. A successful
color probe is basic operation evidence; broad accuracy/latency qualification still requires the
existing benchmark matrix.

The compatibility plugin layout follows [OpenAI's plugin packaging documentation](https://developers.openai.com/plugins/build/plugins).
