#!/usr/bin/env python3
"""
GENESIS Engine — Layer 4: Self-Healing Topological Executor
Generate -> Test -> Validate -> Fail -> Fix -> Validate -> Pass
"""
import subprocess
import time
import json
import os
from datetime import datetime
from typing import Callable, Dict, Any, Optional


class TaskStatus:
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    SUCCESS   = "SUCCESS"
    FAILED    = "FAILED"
    HEALING   = "HEALING"
    SKIPPED   = "SKIPPED"


class Task:
    def __init__(self, task_id: str, agent: str, prompt: str, depends_on: list = None):
        self.task_id    = task_id
        self.agent      = agent
        self.prompt     = prompt
        self.depends_on = depends_on or []
        self.status     = TaskStatus.PENDING
        self.output     = None
        self.error      = None
        self.attempts   = 0
        self.max_attempts = 3

    def to_dict(self):
        return {
            "task_id":    self.task_id,
            "agent":      self.agent,
            "status":     self.status,
            "attempts":   self.attempts,
            "output":     self.output,
            "error":      self.error,
        }


class TopologicalExecutor:
    """
    Executes a DAG of agent tasks with self-healing on test failure.
    """

    def __init__(self, agents: Dict[str, Callable], output_dir: str = "genesis_output"):
        self.agents     = agents      # {"BackendAgent": callable, ...}
        self.tasks: Dict[str, Task] = {}
        self.output_dir = output_dir
        self.memory: list = []        # execution log
        os.makedirs(output_dir, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Graph management
    # ------------------------------------------------------------------ #

    def add_task(self, task: Task):
        self.tasks[task.task_id] = task

    def inject_task(self, task: Task):
        """Dynamically inject a repair task into the graph at runtime."""
        self.tasks[task.task_id] = task
        self._log(f"[INJECT] Dynamic task {task.task_id} injected into graph")

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    def run(self):
        self._log("[GENESIS] Topological executor starting...")
        iteration = 0
        max_iterations = len(self.tasks) * self.tasks[list(self.tasks.keys())[0]].max_attempts * 2 if self.tasks else 50

        while True:
            iteration += 1
            if iteration > max_iterations:
                self._log("[ABORT] Max iterations exceeded — possible dependency cycle")
                break

            ready = self._get_ready_tasks()
            if not ready:
                if all(t.status in (TaskStatus.SUCCESS, TaskStatus.SKIPPED) for t in self.tasks.values()):
                    self._log("[GENESIS] All tasks complete ✅")
                elif any(t.status == TaskStatus.FAILED for t in self.tasks.values()):
                    self._log("[GENESIS] Halted — unrecoverable failure ❌")
                break

            for task in ready:
                self._execute_task(task)

        self._save_report()
        return self._build_summary()

    def _get_ready_tasks(self) -> list:
        """Return tasks whose dependencies are all SUCCESS."""
        ready = []
        for task in self.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            deps_done = all(
                self.tasks.get(d, Task(d, "", "")).status == TaskStatus.SUCCESS
                for d in task.depends_on
            )
            if deps_done:
                ready.append(task)
        return ready

    def _execute_task(self, task: Task):
        task.status   = TaskStatus.RUNNING
        task.attempts += 1
        self._log(f"[RUN] {task.task_id} (attempt {task.attempts}) via {task.agent}")

        agent_fn = self.agents.get(task.agent)
        if not agent_fn:
            task.status = TaskStatus.FAILED
            task.error  = f"No agent registered for '{task.agent}'"
            self._log(f"[FAIL] {task.task_id}: {task.error}")
            return

        try:
            result = agent_fn(task.prompt)
            task.output = result

            # --- If this is a validation task, run the actual test suite ---
            if task.agent == "ValidationAgent":
                self._run_validation(task)
            else:
                task.status = TaskStatus.SUCCESS
                self._log(f"[OK] {task.task_id} succeeded")

        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error  = str(exc)
            self._log(f"[FAIL] {task.task_id} raised exception: {exc}")

    # ------------------------------------------------------------------ #
    # Self-Healing Validation
    # ------------------------------------------------------------------ #

    def _run_validation(self, val_task: Task):
        """
        Run pytest on the file specified in val_task.prompt.
        On failure, inject a FIX task back to the originating agent.
        """
        test_file = val_task.prompt.strip()
        self._log(f"[VALIDATE] Running pytest on {test_file}")

        proc = subprocess.run(
            ["python", "-m", "pytest", test_file, "-v", "--tb=short"],
            capture_output=True, text=True
        )

        if proc.returncode == 0:
            val_task.status = TaskStatus.SUCCESS
            val_task.output = proc.stdout
            self._log(f"[PASS] Validation {val_task.task_id} ✅")
        else:
            val_task.status = TaskStatus.FAILED
            val_task.error  = proc.stdout + proc.stderr
            self._log(f"[FAIL] Validation {val_task.task_id} ❌ — triggering self-heal")
            self._heal(val_task, proc.stdout + proc.stderr)

    def _heal(self, val_task: Task, error_log: str):
        """
        Find the code task that produced the failing tests,
        inject a FIX task, reset the validation task to PENDING.
        """
        # Derive the original code task from the validation task ID
        # Convention: val task ID = "validate-T5" → code task ID = "T4" (one level up)
        base_id   = val_task.task_id.replace("validate-", "")
        fix_id    = f"{base_id}-FIX-{val_task.attempts}"

        # Find the producing task
        producing_task = self._find_producer(val_task)
        if not producing_task:
            self._log(f"[HEAL] Cannot find producer for {val_task.task_id} — marking FAILED")
            val_task.status = TaskStatus.FAILED
            return

        if producing_task.attempts >= producing_task.max_attempts:
            self._log(f"[HEAL] {producing_task.task_id} exceeded max attempts — giving up")
            val_task.status = TaskStatus.FAILED
            return

        # Build the self-healing prompt
        fix_prompt = (
            f"SELF-HEAL REQUEST for task {producing_task.task_id}.\n"
            f"Original prompt: {producing_task.prompt}\n"
            f"Original output:\n{producing_task.output}\n"
            f"Pytest error log:\n{error_log}\n"
            f"Analyze the error and provide a corrected, complete version of the file."
        )

        fix_task = Task(
            task_id    = fix_id,
            agent      = producing_task.agent,
            prompt     = fix_prompt,
            depends_on = producing_task.depends_on,
        )

        # Re-queue: inject fix, reset validation to wait for fix
        self.inject_task(fix_task)
        val_task.status     = TaskStatus.PENDING
        val_task.depends_on = [fix_id]
        val_task.attempts   = 0

        producing_task.status = TaskStatus.HEALING
        self._log(f"[HEAL] Injected {fix_id} → validation {val_task.task_id} reset to PENDING")

    def _find_producer(self, val_task: Task) -> Optional[Task]:
        """Find the most recent non-validation dependency of this validation task."""
        for dep_id in reversed(val_task.depends_on):
            dep = self.tasks.get(dep_id)
            if dep and dep.agent != "ValidationAgent":
                return dep
        # Fallback: search by naming convention
        base = val_task.task_id.replace("validate-", "")
        return self.tasks.get(base)

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #

    def _log(self, msg: str):
        ts  = datetime.utcnow().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        self.memory.append(line)

    def _save_report(self):
        report_path = os.path.join(self.output_dir, "execution_report.json")
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "tasks": {tid: t.to_dict() for tid, t in self.tasks.items()},
            "log": self.memory,
        }
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        self._log(f"[REPORT] Saved to {report_path}")

    def _build_summary(self) -> Dict[str, Any]:
        statuses = [t.status for t in self.tasks.values()]
        return {
            "total":    len(self.tasks),
            "success":  statuses.count(TaskStatus.SUCCESS),
            "failed":   statuses.count(TaskStatus.FAILED),
            "healing":  statuses.count(TaskStatus.HEALING),
            "log":      self.memory,
        }
