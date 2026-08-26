"""
Topological task executor with dependency resolution and bounded self-healing.
"""
from __future__ import annotations

import logging
import subprocess
from enum import Enum
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TopologicalExecutor")


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    HEALING = "HEALING"


class Task:
    def __init__(
        self,
        task_id: str,
        agent: str,
        prompt: str,
        depends_on: Optional[List[str]] = None,
        max_attempts: int = 3,
    ):
        self.task_id = task_id
        self.id = task_id  # alias used by some callers
        self.agent = agent
        self.prompt = prompt
        self.depends_on = list(depends_on or [])
        self.dependencies = self.depends_on  # alias
        self.status = TaskStatus.PENDING
        self.attempts = 0
        self.max_attempts = max_attempts
        self.output: Optional[str] = None
        self.result: Any = None
        self.error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "prompt": self.prompt,
            "depends_on": self.depends_on,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "output": self.output,
            "error": self.error,
        }


class TopologicalExecutor:
    def __init__(
        self,
        agents: Optional[Dict[str, Any]] = None,
        agents_map: Optional[Dict[str, Any]] = None,
        output_dir: str = "/tmp/genesis_output",
        max_retries: int = 3,
    ):
        # Support both constructor styles used in the codebase
        self.agents: Dict[str, Any] = agents if agents is not None else (agents_map or {})
        self.tasks: Dict[str, Task] = {}
        self.output_dir = output_dir
        self.max_retries = max_retries
        self._log: List[str] = []
        self._heal_counter = 0

    def add_task(self, task: Task) -> None:
        self.tasks[task.task_id] = task

    def inject_task(self, task: Task) -> None:
        """Add a task that was created dynamically (e.g. during healing)."""
        self.tasks[task.task_id] = task
        self._log.append(f"injected:{task.task_id}")

    def _deps_satisfied(self, task: Task) -> bool:
        for dep_id in task.depends_on:
            dep = self.tasks.get(dep_id)
            if dep is None or dep.status != TaskStatus.SUCCESS:
                return False
        return True

    def _deps_failed(self, task: Task) -> bool:
        for dep_id in task.depends_on:
            dep = self.tasks.get(dep_id)
            if dep is not None and dep.status == TaskStatus.FAILED:
                return True
        return False

    def _run_agent(self, task: Task) -> None:
        agent = self.agents.get(task.agent)
        if agent is None:
            task.status = TaskStatus.FAILED
            task.error = f"Agent {task.agent} not found."
            self._log.append(f"failed:{task.task_id}:unknown_agent")
            return

        task.status = TaskStatus.RUNNING
        task.attempts += 1
        try:
            if callable(agent):
                result = agent(task.prompt)
            elif hasattr(agent, "run"):
                result = agent.run(task.prompt)
            else:
                raise TypeError(f"Agent {task.agent} is not callable")

            if isinstance(result, dict):
                task.result = result
                task.output = str(result.get("output", result))
            else:
                task.result = result
                task.output = str(result) if result is not None else ""

            # Validation agents: treat prompt as path and run pytest
            if task.agent == "ValidationAgent" or task.task_id.startswith("validate-"):
                self._validate_with_pytest(task)
            else:
                task.status = TaskStatus.SUCCESS
                self._log.append(f"success:{task.task_id}")
        except Exception as exc:
            task.error = str(exc)
            task.status = TaskStatus.FAILED
            self._log.append(f"failed:{task.task_id}:{exc}")

    def _validate_with_pytest(self, task: Task) -> None:
        target = task.prompt  # convention: prompt holds the test file path
        try:
            result = subprocess.run(
                ["pytest", target, "-q"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                task.status = TaskStatus.SUCCESS
                task.output = result.stdout
                self._log.append(f"validated:{task.task_id}")
            else:
                task.error = (result.stderr or result.stdout or "pytest failed")[-2000:]
                self._attempt_heal(task)
        except Exception as exc:
            task.error = str(exc)
            self._attempt_heal(task)

    def _attempt_heal(self, task: Task) -> None:
        """Bounded self-heal: inject a unique FIX task, then fail if exhausted."""
        if task.attempts >= task.max_attempts:
            task.status = TaskStatus.FAILED
            self._log.append(f"heal_exhausted:{task.task_id}")
            return

        task.status = TaskStatus.HEALING
        self._heal_counter += 1
        fix_id = f"FIX-{task.task_id}-{self._heal_counter}"
        fix_agent_name = "BackendAgent" if "BackendAgent" in self.agents else (
            next(iter(self.agents), task.agent)
        )
        fix_task = Task(
            fix_id,
            fix_agent_name,
            f"Fix validation failure for {task.prompt}: {task.error or ''}",
            depends_on=[],
            max_attempts=1,
        )
        self.inject_task(fix_task)
        # Run the fix immediately (best-effort)
        self._run_agent(fix_task)

        # Re-attempt the original validation task
        task.attempts += 1
        if task.attempts >= task.max_attempts:
            task.status = TaskStatus.FAILED
            self._log.append(f"heal_exhausted:{task.task_id}")
        else:
            # One more validation attempt after the fix
            self._validate_with_pytest(task)

    def run(self) -> Dict[str, Any]:
        """Execute all tasks in topological order until none remain runnable."""
        # Safety: prevent infinite loops
        max_iterations = max(50, len(self.tasks) * 10 + 20)
        iterations = 0

        while iterations < max_iterations:
            iterations += 1
            progressed = False
            # Snapshot keys so inject_task during iteration is safe
            for tid in list(self.tasks.keys()):
                task = self.tasks[tid]
                if task.status != TaskStatus.PENDING:
                    continue
                if self._deps_failed(task):
                    # Leave blocked tasks as PENDING (per test expectation)
                    continue
                if not self._deps_satisfied(task):
                    continue
                self._run_agent(task)
                progressed = True

            if not progressed:
                break

        success = sum(1 for t in self.tasks.values() if t.status == TaskStatus.SUCCESS)
        failed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.FAILED)
        healing = sum(1 for t in self.tasks.values() if t.status == TaskStatus.HEALING)
        return {
            "total": len(self.tasks),
            "success": success,
            "failed": failed,
            "healing": healing,
            "log": list(self._log),
        }

    # Back-compat alias used by older callers
    def run_all(self) -> Dict[str, Any]:
        return self.run()

    def execute_agent_task(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        self._run_agent(task)
