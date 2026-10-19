import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agentic_mcp_foundation import LifecycleError, launcher, processes, publication
from agentic_mcp_foundation.jobs import JobEngine, JobStore
from agentic_mcp_foundation.scaffold import scaffold
from agentic_mcp_foundation.transport import atomic_json


def finished(store, job):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = store.get(job["id"])
        if current["state"] in ("succeeded", "failed", "cancelled", "interrupted"):
            return current
        time.sleep(0.01)
    pytest.fail("Job failed to finish")


def test_durable_idempotency_conflict_and_restart_receipts(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    calls = []

    def echo(request, context):
        calls.append(request)
        return request

    engine = JobEngine(store, {"echo": echo})
    first = engine.submit("echo", {"text": "hello"}, "stable-key")
    assert finished(store, first)["result"] == {"text": "hello"}
    assert engine.submit("echo", {"text": "hello"}, "stable-key")["id"] == first["id"]
    with pytest.raises(LifecycleError, match="different intent"):
        engine.submit("echo", {"text": "different"}, "stable-key")
    store.create("unfinished", "echo", {"text": "not executed"})
    reopened = JobStore(store.path)
    reopened.recover()
    assert reopened.get(first["id"])["result"] == {"text": "hello"}
    assert reopened.find("unfinished", "echo", {"text": "not executed"})["state"] == "interrupted"
    assert len(calls) == 1


def test_cancelled_active_work_keeps_capacity_and_blocks_drain(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    entered, release = threading.Event(), threading.Event()

    def slow(request, context):
        entered.set()
        assert release.wait(3)
        return {"done": True}

    engine = JobEngine(store, {"slow": slow, "update": slow}, capacity=1, exclusive=("update",))
    first = engine.submit("slow", {}, "first")
    assert entered.wait(3)
    store.cancel(first["id"])
    assert store.get(first["id"])["cancel_requested"]
    with pytest.raises(LifecycleError, match="capacity"):
        engine.submit("slow", {}, "second")
    with pytest.raises(LifecycleError, match="active"):
        engine.drain()
    with pytest.raises(LifecycleError, match="maintenance"):
        engine.submit("update", {}, "update")
    release.set()
    assert finished(store, first)["state"] == "cancelled"


def test_queued_cancel_never_invokes_adapter(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    entered, release = threading.Event(), threading.Event()
    seen = []

    def task(request, context):
        seen.append(request["n"])
        entered.set()
        assert release.wait(3)
        return {"n": request["n"]}

    engine = JobEngine(store, {"task": task})
    first = engine.submit("task", {"n": 1}, "one")
    assert entered.wait(3)
    second = engine.submit("task", {"n": 2}, "two")
    assert store.cancel(second["id"])["state"] == "cancelled"
    release.set()
    assert finished(store, first)["state"] == "succeeded"
    engine.pending.join()
    assert seen == [1]


def test_launcher_integrity_and_explicit_previous_payload(tmp_path):
    root = tmp_path / "payloads/release-one"
    root.mkdir(parents=True)
    (root / "code.py").write_text("verified")
    (root / "python").symlink_to(sys.executable)
    manifest = {
        "protocol": 1,
        "contract_major": 1,
        "release": root.name,
        "python": "python",
        "module": "example",
        "files": {"code.py": hashlib.sha256(b"verified").hexdigest()},
    }
    atomic_json(tmp_path / "active.json", manifest)
    atomic_json(tmp_path / "last-healthy.json", manifest)
    assert launcher.validate(tmp_path)[0] == root
    (root / "code.py").write_text("corrupt")
    with pytest.raises(ValueError, match="integrity"):
        launcher.validate(tmp_path)
    manifest["release"] = "../../outside"
    atomic_json(tmp_path / "active.json", manifest)
    with pytest.raises(ValueError, match="identity"):
        launcher.validate(tmp_path)


def test_generated_second_tool_runs_and_recovers_across_mcp_clients(tmp_path):
    project = scaffold("receipt-demo", tmp_path / "receipt-demo")
    env = {
        "PYTHONPATH": str(project / "src") + os.pathsep + os.getenv("PYTHONPATH", ""),
        "AGENTIC_TOOL_HOME": str(tmp_path / "demo-runtime"),
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "receipt_demo"], env=env)

    async def run():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            response = await client.call_tool(
                "submit_echo",
                {
                    "text": "independent adapter",
                    "idempotency_key": "receipt-demo-key",
                },
            )
            assert not response.isError
            job = json.loads(response.content[0].text)
            identity = await client.call_tool("get_status", {})
            pid = json.loads(identity.content[0].text)["pid"]
        # The first façade exited. The durable coordinator and receipt remain available.
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            identity = await client.call_tool("get_status", {})
            assert json.loads(identity.content[0].text)["pid"] == pid
            result = await client.call_tool("get_job", {"job_id": job["id"]})
            recovered = json.loads(result.content[0].text)
            assert recovered["state"] == "succeeded"
            assert recovered["result"] == {"text": "independent adapter"}
            replay = await client.call_tool(
                "submit_echo",
                {
                    "text": "independent adapter",
                    "idempotency_key": "receipt-demo-key",
                },
            )
            assert json.loads(replay.content[0].text)["id"] == job["id"]
            conflict = await client.call_tool(
                "submit_echo",
                {
                    "text": "different",
                    "idempotency_key": "receipt-demo-key",
                },
            )
            assert conflict.isError

    asyncio.run(run())
    with pytest.raises(FileExistsError):
        scaffold("receipt-demo", project)


def test_scaffold_rejects_path_names(tmp_path):
    with pytest.raises(ValueError):
        scaffold("../escape", tmp_path / "bad")


def test_failed_publication_restores_all_control_files(tmp_path, monkeypatch):
    (tmp_path / "active.json").write_bytes(b"old-active")
    (tmp_path / "settings.json").write_bytes(b"old-settings")
    original = publication.atomic_bytes

    def interrupted(path, data):
        if data == b"new-settings":
            raise OSError("simulated disk failure")
        original(path, data)

    monkeypatch.setattr(publication, "atomic_bytes", interrupted)
    with pytest.raises(OSError, match="disk failure"):
        publication.publish(
            tmp_path, {"active.json": b"new-active", "settings.json": b"new-settings"}
        )
    assert (tmp_path / "active.json").read_bytes() == b"old-active"
    assert (tmp_path / "settings.json").read_bytes() == b"old-settings"
    assert not (tmp_path / "publication").exists()


def test_abrupt_publication_is_recovered_before_next_install(tmp_path):
    transaction = tmp_path / "publication"
    transaction.mkdir()
    (transaction / "0").write_bytes(b"previous")
    atomic_json(
        transaction / "journal.json",
        {
            "entries": [
                {"name": "active.json", "directory": False, "existed": True},
            ]
        },
    )
    (tmp_path / "active.json").write_bytes(b"partially-published")
    publication.recover(tmp_path)
    assert (tmp_path / "active.json").read_bytes() == b"previous"


def test_process_recovery_rejects_reused_identity(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    record = tmp_path / "owner.json"
    try:
        processes.remember(record, child)
        recovered = processes.recover(record)
        assert recovered is not None and recovered.pid == child.pid
        invalid = json.loads(record.read_text())
        invalid["start"] = "different-process-birth"
        atomic_json(record, invalid)
        assert processes.recover(record) is None
        assert child.poll() is None
        recovered.terminate()
        child.wait(timeout=3)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_queued_deadline_does_not_reach_handler(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    entered, release = threading.Event(), threading.Event()
    seen = []

    def task(request, context):
        seen.append(request["n"])
        entered.set()
        assert release.wait(3)
        return {"ok": True}

    engine = JobEngine(store, {"task": task}, queue_timeout=0.05)
    first = engine.submit("task", {"n": 1}, "active")
    assert entered.wait(3)
    waiting = engine.submit("task", {"n": 2}, "waiting")
    time.sleep(0.06)
    assert engine.get(waiting["id"])["error"]["code"] == "QUEUE_TIMEOUT"
    release.set()
    assert finished(store, first)["state"] == "succeeded"
    engine.pending.join()
    assert seen == [1]


def test_post_publication_smoke_failure_rolls_back(tmp_path):
    (tmp_path / "active.json").write_bytes(b"known-good")

    def bad_smoke():
        raise RuntimeError("installed command failed")

    with pytest.raises(RuntimeError, match="installed command"):
        publication.publish(tmp_path, {"active.json": b"candidate"}, verify=bad_smoke)
    assert (tmp_path / "active.json").read_bytes() == b"known-good"


def test_darwin_identity_uses_kernel_birth_time_and_rejects_unverifiable_process(monkeypatch):
    import ctypes
    from types import SimpleNamespace

    state = {"pid": 123, "status": 2, "readable": True}

    def info(pid, flavor, argument, buffer, size):
        assert flavor == 3 and size == 136
        result = ctypes.cast(buffer, ctypes.POINTER(processes._ProcBSDInfo)).contents
        result.pid, result.status = state["pid"], state["status"]
        result.start_sec, result.start_usec = 1700000000, 123456
        return size if state["readable"] else 0

    def path(pid, buffer, size):
        buffer.value = b"/private/runtime/ollama"
        return len(buffer.value)

    monkeypatch.setattr(
        processes.ctypes,
        "CDLL",
        lambda *a, **k: SimpleNamespace(
            proc_pidinfo=info,
            proc_pidpath=path,
        ),
    )
    monkeypatch.setattr(processes.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(processes, "_darwin_boot", lambda: "boot-session")
    assert processes.identity(123) == {
        "pid": 123,
        "start": "1700000000:123456",
        "executable": "/private/runtime/ollama",
        "boot": "boot-session",
    }
    state["status"] = 5
    with pytest.raises(ProcessLookupError):
        processes.identity(123)
    state.update(status=2, pid=999)
    with pytest.raises(ProcessLookupError):
        processes.identity(123)
    state.update(pid=123, readable=False)
    with pytest.raises(ProcessLookupError):
        processes.identity(123)
