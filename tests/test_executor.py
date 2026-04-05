"""
Unit tests for the TopologicalExecutor and self-healing logic.
"""
import pytest
from core.topological_executor import TopologicalExecutor, Task, TaskStatus


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_executor(agents=None):
    agents = agents or {}
    return TopologicalExecutor(agents=agents, output_dir="/tmp/test_genesis_output")


def stub_agent(prompt: str) -> str:
    return f"# stub output for: {prompt[:40]}"


def failing_agent(prompt: str) -> str:
    raise RuntimeError("agent intentionally failed")


# ── Task model ───────────────────────────────────────────────────────────────

def test_task_defaults():
    t = Task("T1", "BackendAgent", "Build an API")
    assert t.task_id == "T1"
    assert t.status == TaskStatus.PENDING
    assert t.attempts == 0
    assert t.depends_on == []
    assert t.max_attempts == 3


def test_task_to_dict():
    t = Task("T1", "BackendAgent", "Build an API")
    d = t.to_dict()
    assert d["task_id"] == "T1"
    assert d["agent"] == "BackendAgent"
    assert d["status"] == TaskStatus.PENDING


# ── Basic execution ──────────────────────────────────────────────────────────

def test_single_task_success():
    ex = make_executor({"BackendAgent": stub_agent})
    ex.add_task(Task("T1", "BackendAgent", "write some code"))
    summary = ex.run()
    assert summary["success"] == 1
    assert summary["failed"] == 0
    assert ex.tasks["T1"].status == TaskStatus.SUCCESS


def test_dependency_chain():
    ex = make_executor({"A": stub_agent, "B": stub_agent})
    ex.add_task(Task("T1", "A", "step 1"))
    ex.add_task(Task("T2", "B", "step 2", depends_on=["T1"]))
    ex.run()
    assert ex.tasks["T1"].status == TaskStatus.SUCCESS
    assert ex.tasks["T2"].status == TaskStatus.SUCCESS


def test_unknown_agent_fails_task():
    ex = make_executor({})
    ex.add_task(Task("T1", "NoSuchAgent", "prompt"))
    ex.run()
    assert ex.tasks["T1"].status == TaskStatus.FAILED


def test_agent_exception_fails_task():
    ex = make_executor({"BadAgent": failing_agent})
    ex.add_task(Task("T1", "BadAgent", "prompt"))
    ex.run()
    assert ex.tasks["T1"].status == TaskStatus.FAILED
    assert ex.tasks["T1"].error is not None


def test_task_output_stored():
    ex = make_executor({"BackendAgent": stub_agent})
    ex.add_task(Task("T1", "BackendAgent", "write some code"))
    ex.run()
    assert ex.tasks["T1"].output is not None
    assert "stub output" in ex.tasks["T1"].output


# ── Dependency resolution ────────────────────────────────────────────────────

def test_blocked_task_does_not_run_when_dependency_fails():
    ex = make_executor({"BadAgent": failing_agent, "GoodAgent": stub_agent})
    ex.add_task(Task("T1", "BadAgent", "will fail"))
    ex.add_task(Task("T2", "GoodAgent", "blocked by T1", depends_on=["T1"]))
    ex.run()
    assert ex.tasks["T1"].status == TaskStatus.FAILED
    # T2 never runs because T1 failed
    assert ex.tasks["T2"].status == TaskStatus.PENDING


def test_parallel_independent_tasks_all_succeed():
    ex = make_executor({"A": stub_agent, "B": stub_agent, "C": stub_agent})
    ex.add_task(Task("T1", "A", "a"))
    ex.add_task(Task("T2", "B", "b"))
    ex.add_task(Task("T3", "C", "c"))
    summary = ex.run()
    assert summary["success"] == 3
    assert summary["failed"] == 0


# ── inject_task ──────────────────────────────────────────────────────────────

def test_inject_task_adds_to_graph():
    ex = make_executor({"A": stub_agent})
    ex.add_task(Task("T1", "A", "original"))
    injected = Task("T_injected", "A", "injected task")
    ex.inject_task(injected)
    assert "T_injected" in ex.tasks


# ── Self-healing: termination guarantee ──────────────────────────────────────

def test_validation_failure_terminates_after_max_attempts(tmp_path):
    """
    A ValidationAgent task that always fails should terminate (FAILED)
    after max_attempts, not loop forever.
    """
    test_file = str(tmp_path / "nonexistent_test.py")

    def dummy_fix_agent(prompt: str) -> str:
        return "# still broken\n"

    ex = TopologicalExecutor(
        agents={"TestingAgent": stub_agent, "ValidationAgent": stub_agent},
        output_dir=str(tmp_path),
    )
    # T1 produces test code, validate-T1 runs pytest on a non-existent file
    ex.add_task(Task("T1", "TestingAgent", "write tests"))
    val_task = Task("validate-T1", "ValidationAgent", test_file, depends_on=["T1"])
    val_task.max_attempts = 2  # low limit to keep test fast
    ex.add_task(val_task)

    summary = ex.run()

    # The validation task must be FAILED (not still PENDING/RUNNING)
    assert ex.tasks["validate-T1"].status == TaskStatus.FAILED


def test_heal_injects_unique_fix_ids(tmp_path):
    """
    Each heal cycle must inject a fix task with a distinct ID
    so no task is silently overwritten.
    """
    test_file = str(tmp_path / "no_such_test.py")

    ex = TopologicalExecutor(
        agents={"TestingAgent": stub_agent, "ValidationAgent": stub_agent},
        output_dir=str(tmp_path),
    )
    ex.add_task(Task("T1", "TestingAgent", "write tests"))
    val_task = Task("validate-T1", "ValidationAgent", test_file, depends_on=["T1"])
    val_task.max_attempts = 3
    ex.add_task(val_task)

    ex.run()

    # Collect all injected fix task IDs
    fix_ids = [tid for tid in ex.tasks if "FIX" in tid]
    # All IDs must be unique (no overwriting)
    assert len(fix_ids) == len(set(fix_ids)), "Duplicate fix task IDs detected"


# ── Summary structure ────────────────────────────────────────────────────────

def test_summary_keys():
    ex = make_executor({"A": stub_agent})
    ex.add_task(Task("T1", "A", "x"))
    summary = ex.run()
    for key in ("total", "success", "failed", "healing", "log"):
        assert key in summary, f"Missing key: {key}"


def test_summary_totals_match_tasks():
    ex = make_executor({"A": stub_agent})
    ex.add_task(Task("T1", "A", "x"))
    ex.add_task(Task("T2", "A", "y"))
    summary = ex.run()
    assert summary["total"] == 2
    assert summary["success"] == 2
