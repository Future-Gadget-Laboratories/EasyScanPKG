# sins

`sins` is a local, rule based scanner that reports recurring code and text
patterns using Paladin's mortal/venial vocabulary. S1 includes a standard
library TOML loader, a bounded regular expression engine, JSON and text
reports, and rule example checks. Rules are additions: repo-local rules cannot
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

## Commands

```bash
./bin/sins scan --repo .
./bin/sins count --repo . --json .sins/out/report.json
./bin/sins scan --repo . --rule py-bare-except --fail-on any --format json
./bin/sins test
./bin/sins list
./bin/sins new my-new-sin --language python --out .sins/rules
./bin/sins --version --json
```

`--rules-dir DIR` can be repeated to load additional TOML packs. `--rule ID`
can be repeated to select rules. `--fail-on mortal` is the default;
`--fail-on any` gates on every finding. `--fail-on new` is reserved for S3 and
returns a configuration error in S1. `scan` and `count` have identical
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

## Roadmap

- S2: review-with-fix schema and reviewer guidance.
- S3: policy, forgiveness, baselines, fingerprints, diff, and `--fail-on new`.
- S4: pinned ast-grep engine and safe rewrites.
- S5: pinned semantic engine and external checker importer.
- S6: trusted Paladin report federation.
- S7: Sonar/SARIF output and EasyScanPKG adapter.
- S8: bounded scan-fix-rescan loop and resource controls.
- S9: reviewed Python/C++ starter pack.
- S10: offline release bundle and localhost server.
- S11: identical Nitrogen and LabCluster rollout.
- S12: per-scan rootless Podman Sonar integration on the lab cluster.
