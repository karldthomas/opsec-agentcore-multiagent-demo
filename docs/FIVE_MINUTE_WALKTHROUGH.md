# Five-minute OpsFusion walkthrough

1. Run the local checks: `python3 -m pytest -q` and
   `(cd apps/web && npm run build)`.
2. Start the API with `uvicorn apps.api.app:app --reload`.
3. Open the console and launch `INC-2048`.
4. Use the live investigation trace on the first screen. Three specialist lanes
   show the business question, actual tool call, reason for the call, and
   returned result. Hover for a compact summary and select a lane to inspect
   the skill, prompt trace, bounded inputs, and evidence IDs.
5. Expand Findings and evidence, Tool calls and observations, or Analysis
   quality below the trace. Read-only evidence-gap checks run automatically and
   the workflow stops at the completed analysis checkpoint.
6. Review the analysis checks. They confirm evidence citations, causal
   coverage, hidden-state isolation, and an unchanged simulator snapshot.
7. For the durability demo, inject `after_telemetry` in a local workflow,
   observe the controlled interruption, then resume the same run ID. Completed
   findings remain in the evidence workspace and are not duplicated.

All data in this walkthrough is synthetic. No production resource or customer
record is accessed.
