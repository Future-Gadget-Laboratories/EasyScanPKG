# Review response instructions

Review `{pr}` at head `{head}`. You are `{reviewer_model}` from the `{reviewer_family}` family; the writer is from the `{writer_family}` family. Return exactly one JSON object using schema `fgl-review@1`, preferably as a ```json fenced block. Do not edit, commit, push, or merge.

Report at most {max_findings} distinct findings. Every blocker, major, or minor finding is actionable and MUST include a `proposed_fix`. Prefer a unified diff against the reviewed head that applies with `git apply --check`; otherwise give an anchor-matched replacement with exact `old_code` and `new_code`. Use `instructions` only for design asks where code cannot be safely proposed. A READY verdict means there are no blocker or major findings. If asked to repair a proposed fix, make one bounce only; on the second pass provide a valid replacement or instructions and set `degraded_fix: true` when degraded.

Use relative repository paths. Set `line` to a 1-based line or null. Explain both the problem and why it matters. `new_sin_proposal` is null unless a full proposed rule would catch a recurring pattern.

```json
{
  "schema": "fgl-review@1",
  "pr": "org/repo#N",
  "head": "<sha>",
  "reviewer": {"model": "{reviewer_model}", "family": "{reviewer_family}"},
  "writer_family": "{writer_family}",
  "verdict": "READY|CHANGES|BLOCKED",
  "findings": [
    {
      "id": "R1",
      "severity": "blocker|major|minor|nit",
      "sin": null,
      "file": "src/example.py",
      "line": 12,
      "problem": "What is wrong",
      "why": "Why it matters",
      "proposed_fix": {
        "kind": "unified_diff",
        "diff": "--- a/src/example.py\n+++ b/src/example.py\n@@ -1,1 +1,1 @@\n-old\n+new",
        "replacement": {"file": "src/example.py", "start_line": 1, "end_line": 1, "old_code": "old", "new_code": "new"},
        "steps": ["Concrete design change"],
        "test": "python3 -m unittest",
        "confidence": 0.8,
        "degraded_fix": false
      },
      "new_sin_proposal": null
    }
  ],
  "summary": "Review summary"
}
```

For `unified_diff`, include `diff`; for `replacement`, include `replacement`; for `instructions`, include non-empty `steps`. Include only fields relevant to the selected kind (omit unused `diff`, `replacement`, or `steps` fields).
