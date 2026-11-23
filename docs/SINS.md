# sins

`sins` is a local, rule based scanner that reports recurring code and text
patterns using Paladin's mortal/venial vocabulary. It includes a standard
library TOML loader, regex and pinned ast-grep engines, JSON and text reports,
and rule example checks. Rules are additions: repo-local rules cannot
replace a built-in ID.

## Rule format

Put built-in rules in `lib/sins/rules/<language>/<id>.toml`; repo additions go
in `<repo>/.sins/rules/*.toml`. A complete regex rule looks like this:

```toml
id = "python-bare-except"
version = 1
phrase = "catch every exception with a bare except"
description = "Bare except clauses can swallow process-control exceptions."
language = "python"
severity = "major" # info, minor, major, critical, blocker
mortal = false
mortal_threshold = 2
engine = "regex"
tags = ["reliability"]

[paths]
include = ["**/*.py"]
exclude = ["**/vendor/**"]

[match]
pattern = "^\\s*except\\s*:"
# Optional Python re flags: IGNORECASE, DOTALL, ASCII (comma or pipe separated).

[fix]
kind = "template" # template or rewrite
template = "Catch the narrowest expected exception."
fix_hint = "Preserve process-control exceptions."

[examples]
bad = ["try:\n    work()\nexcept:\n    pass\n"]
good = ["try:\n    work()\nexcept ValueError:\n    pass\n"]
```

`examples.bad` and `examples.good` are required non-empty string lists. A
rewrite fix also needs one clean `examples.fixed` item per bad example. Severity
and fix metadata are descriptive in S1. A mortal finding blocks; a venial sin
blocks only when its unforgiven count exceeds `mortal_threshold`. If omitted,
the threshold is unlimited; set it explicitly (including `0`) to enable a cap.
Regex matching uses Python `re` with MULTILINE. Files over 2 MiB, files with a
NUL byte in the first 8 KiB, and common generated/build directories are skipped.

Structural rules set `engine = "ast-grep"`; their `[match]` table is passed as
the ast-grep rule body. For example:

```toml
engine = "ast-grep"
[match.rule]
kind = "except_clause"
regex = "^except\\s*:"
```

The binary is pinned to 0.51.3 in `lib/sins/engines/pins.toml`. Installation
verifies the release zip before extracting only `ast-grep`:
`./bin/sins engines install`. `./bin/sins engines status` reports the resolved
path, version, and hash status. `SINS_AST_GREP` selects an explicit binary, but
it must pass the same version and hash checks. The CLI never looks up or invokes
`sg`, and scans never download engines automatically. Scans use two threads
and a 120 second timeout.

`sins fix --repo . --sin py-open-without-encoding` prints a unified diff by
default. Add `--apply` to atomically apply rewrites. The fixer refuses modified,
staged, or ignored files and restores a file if parsing fails or the sin count
does not decrease. Only rules declaring `fix.kind = "rewrite"` are eligible.

## Repo policy and forgiveness

`.sins/policy.toml` overlays rule defaults without replacing rules:

```toml
[sins.py-bare-except]
mortal_threshold = 3
enabled = true
mortal = false

[paths]
exclude = ["vendor/**", "generated/**"]
```

Only `mortal_threshold` (integer >= 0), `enabled` (boolean), and `mortal`
(boolean) are accepted per sin. Unknown sin IDs, keys, or invalid values are
configuration errors (exit 2). Global `paths.exclude` patterns apply to every
rule in addition to each rule's own excludes. Policy values overlay the rule
defaults; disabled sins are omitted from the scan.

Forgiveness must be in a real comment and include a reason:

```python
except:  # sins: forgive py-bare-except -- legacy API boundary
# sins: forgive py-bare-except -- upstream compatibility requirement
except:
```

The standalone comment applies to the next non-blank line. C++ recognizes
`//` and `/* */` comments and also supports Paladin's `//forgive me` for that
line. Quoted strings are skipped when looking for comment markers. Forgiven
findings remain in the report (`forgiven: true`) and are excluded from the
unforgiven counts. A marker without a non-empty reason creates the venial
built-in meta finding `sins-forgive-without-reason`.

## Fingerprints, baselines, and diffs

Each finding includes a 16-character SHA-256 fingerprint derived from its sin
ID, repository-relative path, whitespace-collapsed matched text, and nearest
preceding non-blank non-match line (`<bof>` at the beginning of a file). Counts
use fingerprint multisets, so line shifts remain stable and another identical
finding still counts as an additional occurrence.

```bash
./bin/sins baseline write --repo . [--out .sins/baseline.json]
./bin/sins scan --repo . --baseline .sins/baseline.json --format json
./bin/sins diff --repo . --base origin/main --json
./bin/sins diff --repo . --base origin/main --head feature/ref
./bin/sins scan --repo . --fail-on new --base origin/main
```

Baseline JSON stores per-sin fingerprint multisets and totals with the rules
version. Scans report `count_new` per sin and `totals.new`. `diff` defaults its
base snapshot to `merge-base(<REF>, HEAD)` and compares it with the working tree
unless `--head` names another ref. Detached temporary Git worktrees keep ref
scans isolated and are removed after each scan, including on failure. Refs are
validated before being passed to Git. `--fail-on new` uses the same merge-base
comparison and defaults to `origin/main`; without that ref, pass `--base`.

Under `--fail-on new`, added suppression comments and changes to `.sins` policy,
baseline, or rule TOML are listed in `policy_tampering`. Any new unforgiven
finding or tampering exits 1. `--allow-policy-change` leaves the entries in the
report but does not fail on tampering. Invalid policy, rule, Git ref, or scan
configuration still exits 2.

## Commands

```bash
./bin/sins scan --repo .
./bin/sins count --repo . --json .sins/out/report.json
./bin/sins scan --repo . --rule py-bare-except --fail-on any --format json
./bin/sins test
./bin/sins engines status
./bin/sins fix --repo . --sin py-open-without-encoding
./bin/sins list
./bin/sins new my-new-sin --language python --out .sins/rules
./bin/sins --version --json
```

`--rules-dir DIR` can be repeated to load additional TOML packs. `--rule ID`
can be repeated to select rules. `--fail-on mortal` is the default;
`--fail-on any` gates on every unforgiven finding. `scan` and `count` have identical
behavior and both print the count table by default. `--json OUT` also writes a
report file (`-` writes JSON to stdout); `--format json` prints it to stdout.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Clean under the selected failure policy, or examples pass |
| 1 | Mortal sin present, venial threshold exceeded, or `--fail-on any` finding |
| 2 | Invalid configuration/rule, unsupported selected engine, unreadable repo, or failed rule examples |
| 3 | Reserved for a non-converging loop (later slice) |

Precedence is 2 > 3 > 1 > 0.

## Add a sin in three steps

1. Run `sins new <id> --language <language> --out <directory>` and edit the
   TOML fields and Python regex. The generated placeholder intentionally fails
   the example check.
2. Add representative bad and good snippets; run `sins test --rules-dir <pack>`.
3. Put the reviewed rule under `.sins/rules/`, then run `sins scan --repo .`.
   For ast-grep rules, `[match]` is the engine rule body and examples run
   against the pinned binary.

In `fgl-review@1` responses, `proposed_fix: null` is allowed for nit findings.
Blocker, major, and minor findings still require a proposed fix object.

## Roadmap

- S2: review-with-fix schema and reviewer guidance.
- S3: policy, forgiveness, baselines, fingerprints, diff, and `--fail-on new` (implemented).
- S4: pinned ast-grep engine and safe rewrites (implemented).
- S5: pinned semantic engine and external checker importer.
- S6: trusted Paladin report federation.
- S7: Sonar/SARIF output and EasyScanPKG adapter.
- S8: bounded scan-fix-rescan loop and resource controls.
- S9: reviewed Python/C++ starter pack.
- S10: offline release bundle and localhost server.
- S11: identical Nitrogen and LabCluster rollout.
- S12: per-scan rootless Podman Sonar integration on the lab cluster.
