# Review format: `fgl-review@1`

Every review is a JSON object (optionally returned in a `json` fenced block) with these required top-level fields:

| Field | Meaning |
| --- | --- |
| `schema` | Exactly `fgl-review@1` |
| `pr`, `head` | Review target and immutable reviewed commit |
| `reviewer` | `{ "model": string, "family": string }` |
| `writer_family` | Model family that wrote the change; must differ from reviewer family, ignoring case |
| `verdict` | `READY`, `CHANGES`, or `BLOCKED` |
| `findings` | Array of findings described below |
| `summary` | Short overall assessment |

Each finding requires a unique `id`, `severity` (`blocker`, `major`, `minor`, `nit`), `sin` (string or null), relative `file`, `line` (1-based integer or null), `problem`, and `why`. `new_sin_proposal` is optional and may be a rule proposal or null. A READY review cannot contain blocker or major findings.

Blocker, major, and minor findings are actionable and require `proposed_fix`; nits may omit it. Fix forms are:

* `{"kind":"unified_diff","diff":"..."}`: a non-empty patch with relative `---`/`+++` paths and at least one hunk. The drafter checks it with `git apply --check --recount`.
* `{"kind":"replacement","replacement":{"file":"...","start_line":1,"end_line":2,"old_code":"...","new_code":"..."}}`: line numbers are guidance; the exact old-code anchor must occur once.
* `{"kind":"instructions","steps":["..."]}`: concrete steps, reserved for design asks without a safe code patch.

Any fix may also include `test`, `confidence` from 0 through 1, and `degraded_fix` boolean. The stdlib validator in `lib/sins/review.py` is the runtime source of truth; the JSON Schema at `lib/sins/schemas/fgl-review-1.schema.json` supports documentation and interop.

## Drafter flow

1. Save the reviewer response and run `sins review validate review.json`.
2. Run `sins review check-fixes review.json --repo .`. Unified diffs are checked without applying them; replacements are checked for exactly one old-code anchor. Instructions and optional nit fixes are skipped.
3. Apply valid diffs in finding order. If earlier changes shift later hunks, adapt with the replacement anchor or make a focused edit. Run each suggested test and the relevant project gates.
4. Reply to every finding as **applied**, **adapted**, or **declined**, with a short reason. Include the validation/test evidence for applied changes.
5. Bounce invalid/missing proposed fixes to the reviewer once. On the second pass, accept a valid replacement or concrete instructions with `degraded_fix: true`; do not create an endless review loop.

The reviewer never applies changes or pushes. A proposed new sin remains a proposal and must pass `sins test` and normal rule review before enablement.

## Guidance for review skills

Use the following wording when updating the named review skills (the skills live outside this repository and are not modified here):

* **code-review-checklist:** “For every blocker, major, or minor finding, provide `proposed_fix`: preferably a unified diff against the reviewed head that passes `git apply --check`; otherwise provide an exact old-code/new-code replacement. Include a test command or test name when useful. Nits may omit a fix.”
* **code-review-checklist-addons:** “Keep language-specific recommendations actionable: identify the file and line, explain the failure mode, and include an applicable patch or exact replacement for every actionable finding. Do not invent a fix that changes behavior without evidence; use concrete instructions only for design-level asks.”
* **code-review-composer-index:** “Normalize each specialist finding into `fgl-review@1`; preserve its proposed diff/replacement and test evidence, deduplicate by root cause, and ensure reviewer family differs from writer family.”
* **pr-review-round-relay-gated:** “Step 2 triage consumes each finding’s `proposed_fix` and records whether it is applicable. Step 3 applies or adapts the fix, runs its test, and replies per finding as applied/adapted/declined. Bounce missing or invalid fixes once; then use a valid replacement or degraded instructions.”
* **agent-run-feedback:** “Pool proposed fixes from reviews into the ranked fix list, retaining finding IDs, file anchors, test suggestions, and confidence. Do not rank a finding as actionable when its required fix is absent.”
* **cipherbank-review-grade-prs:** “Return `fgl-review@1` and include a concrete proposed fix for each blocker, major, or minor finding. Prefer a patch against the reviewed head; otherwise supply exact replacement anchors. READY means no blocker or major finding.”
