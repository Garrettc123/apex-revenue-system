# Engineering Control Loop

This subsystem operationalizes three engineering AI use cases in `apex-revenue-system`:

1. **Codebase context** — inventories the repository, file types, important entrypoints, manifests, and workflows.
2. **Bug triage** — ranks open GitHub issues using deterministic signals for security, revenue/payment impact, production impact, defects, and delivery failures.
3. **Launch security gate** — checks for common security controls including secret-file exposure, dependency manifests, CI/tests, webhook signatures, payment idempotency, authentication boundaries, input validation, audit logging, external-side-effect approval, and health endpoints.

## AI layer

Set the repository secret `OPENAI_API_KEY` to enable the optional AI review. The workflow uses `gpt-5.6-luna` by default for cost-sensitive analysis. Without the secret, deterministic analysis still runs.

The AI receives a bounded repository snapshot rather than unrestricted repository access. It is instructed to distinguish evidence from hypotheses and not recommend destructive autonomous changes.

## Workflow

`.github/workflows/engineering-control-loop.yml` runs on pull requests, manual dispatch, and weekdays on a schedule. It publishes a Markdown report to the GitHub Actions job summary and uploads JSON/Markdown artifacts.

## Governance

This system is advisory. It does **not** merge pull requests, modify production infrastructure, rotate secrets, send customer communications, or execute external financial actions. Those actions remain behind explicit engineering controls and human approval.

## Operating model

```text
Repository
   -> Codebase inventory
   -> Issue triage
   -> CI failure inventory
   -> Security launch gate
   -> Optional AI synthesis
   -> Job summary + artifact
   -> Human decision
```

## Recommended rollout

Start with `FAIL_ON_SECURITY_REVIEW=false`. Review the generated report for several runs, tune false positives, then decide which checks should become hard CI blockers. Do not make the AI review itself the sole release authority.
