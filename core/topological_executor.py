import subprocess
import logging
from typing import Dict, Any, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TopologicalExecutor")

class Task:
    def __init__(self, id: str, agent: str, prompt: str, dependencies: List[str] = None):
        self.id = id
        self.agent = agent
        self.prompt = prompt
        self.dependencies = dependencies or []
        self.status = "PENDING"
        self.result = None

class TopologicalExecutor:
    def __init__(self, agents_map: Dict[str, Any]):
        self.agents = agents_map
        self.tasks: Dict[str, Task] = {}
        self.max_retries = 3

    def add_task(self, task: Task):
        self.tasks[task.id] = task

    def execute_agent_task(self, task_id: str):
        task = self.tasks[task_id]
        logger.info(f"🚀 Executing Task [{task.id}] via {task.agent}")
        
        agent = self.agents.get(task.agent)
        if not agent:
            raise ValueError(f"Agent {task.agent} not found.")

        # 1. Generate code (Mocking actual LLM call for structure)
        task.result = agent.run(task.prompt)
        task.status = "COMPLETED"

        # 2. If it's a Testing task, immediately spawn a dynamic Validation task
        if "test" in task.id.lower() or task.agent == "TestingAgent":
            self._run_validation_loop(task)

    def _run_validation_loop(self, test_task: Task):
        retries = 0
        validation_passed = False
        target_file = test_task.result.get("file_path", "tests/backend/test_auth.py")

        while not validation_passed and retries < self.max_retries:
            logger.info(f"🔄 Spawning dynamic Validation task for [{test_task.id}] - Attempt {retries+1}")
            
            try:
                # 3. The Runtime Execution Validation
                result = subprocess.run(
                    ["pytest", target_file],
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if result.returncode == 0:
                    logger.info(f"✅ Validation Passed for {target_file}")
                    validation_passed = True
                else:
                    logger.error(f"❌ Validation Failed. Triggering Self-Healing protocol.")
                    self._trigger_self_healing(target_file, result.stdout, result.stderr)
                    retries += 1

            except Exception as e:
                logger.error(f"⚠️ Validation runtime error: {str(e)}")
                self._trigger_self_healing(target_file, str(e), str(e))
                retries += 1

        if not validation_passed:
            logger.critical(f"🛑 Self-healing exhausted for {target_file}. Halting branch.")

    def _trigger_self_healing(self, target_file: str, stdout: str, stderr: str):
        """Dynamically injects a fix task back to the BackendAgent"""
        fix_prompt = f"""
        The code for {target_file} failed validation. 
        Pytest error log:
        {stderr[-1000:]} 
        
        Analyze the error and provide a fixed version of the file(s).
        """
        logger.info(f"🔧 Injecting FIX task to BackendAgent for {target_file}")
        
        # In a full system, this calls the BackendAgent dynamically:
        fix_agent = self.agents.get("BackendAgent")
        if fix_agent:
            fix_agent.run(fix_prompt)
        
        logger.info("✅ Fix applied. Loop will re-validate.")

    def run_all(self):
        # Simplistic topological sort/run
        for task_id in self.tasks:
            if self.tasks[task_id].status == "PENDING":
                self.execute_agent_task(task_id)
