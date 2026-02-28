#!/usr/bin/env python3
"""
GENESIS Engine — Entrypoint
Takes a natural language prompt and produces a fully-tested, self-healed project.
"""
import os
from core.topological_executor import TopologicalExecutor, Task
from core.agents import (
    backend_agent, frontend_agent,
    testing_agent, coordinator_agent, validation_agent
)


def build_project(project_prompt: str, output_dir: str = "genesis_output") -> dict:
    """
    Full pipeline:
      T1 (Coordinator) -> T2 (Backend) -> T3 (Testing) -> T4 (Validate)
                       -> T5 (Frontend)
    """
    print(f"\n[GENESIS] Building: {project_prompt[:80]}...\n")

    os.makedirs(output_dir, exist_ok=True)

    agents = {
        "CoordinatorAgent": coordinator_agent,
        "BackendAgent":     backend_agent,
        "FrontendAgent":    frontend_agent,
        "TestingAgent":     testing_agent,
        "ValidationAgent":  validation_agent,
    }

    ex = TopologicalExecutor(agents=agents, output_dir=output_dir)

    # --- Build the initial task graph ---
    ex.add_task(Task(
        "T1", "CoordinatorAgent",
        f"Decompose into backend + frontend tasks: {project_prompt}"
    ))
    ex.add_task(Task(
        "T2", "BackendAgent",
        f"Write FastAPI backend for: {project_prompt}",
        depends_on=["T1"]
    ))
    ex.add_task(Task(
        "T3", "TestingAgent",
        f"Write pytest tests for the FastAPI backend of: {project_prompt}",
        depends_on=["T2"]
    ))
    ex.add_task(Task(
        "T4", "FrontendAgent",
        f"Write React frontend for: {project_prompt}",
        depends_on=["T1"]
    ))
    ex.add_task(Task(
        "validate-T3", "ValidationAgent",
        # prompt = path to test file pytest will run
        f"{output_dir}/tests/test_backend.py",
        depends_on=["T3"]
    ))

    summary = ex.run()
    return summary


if __name__ == "__main__":
    result = build_project(
        "A SaaS revenue dashboard with user auth, subscription billing, and real-time metrics"
    )
    print("\n=== GENESIS SUMMARY ===")
    for k, v in result.items():
        if k != "log":
            print(f"  {k}: {v}")
