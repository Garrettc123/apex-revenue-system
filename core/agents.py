#!/usr/bin/env python3
"""
GENESIS Engine — Agent Stubs
Replace these with real LLM calls (Gemini, OpenAI, etc.)
"""
import os

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")


def _llm_call(system_prompt: str, user_prompt: str) -> str:
    """Stub — swap in real API call here."""
    if GEMINI_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_KEY)
            resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=f"{system_prompt}\n\n{user_prompt}"
            )
            return resp.text
        except Exception as e:
            return f"# LLM Error: {e}\n# Prompt was: {user_prompt[:200]}"
    # Offline stub — returns a minimal valid Python file for testing
    return f"# AUTO-GENERATED STUB\n# Agent: system\n# Prompt: {user_prompt[:120]}\npass\n"


def backend_agent(prompt: str) -> str:
    return _llm_call(
        "You are a senior backend engineer. Write complete, production-quality Python code.",
        prompt
    )


def frontend_agent(prompt: str) -> str:
    return _llm_call(
        "You are a senior frontend engineer. Write complete React/TypeScript components.",
        prompt
    )


def testing_agent(prompt: str) -> str:
    return _llm_call(
        "You are a QA engineer. Write comprehensive pytest test suites for the given code.",
        prompt
    )


def coordinator_agent(prompt: str) -> str:
    return _llm_call(
        "You are a technical project coordinator. Analyze requirements and produce a JSON task graph.",
        prompt
    )


# ValidationAgent is handled internally by TopologicalExecutor._run_validation()
def validation_agent(prompt: str) -> str:
    """Passthrough — executor intercepts and runs pytest directly."""
    return prompt
