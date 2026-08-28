---
name: sonar-agent-analysis
description: >-
  Run Sonar static analysis for agent-written code via SonarQube MCP and
  SonarQube for IDE. Use after generating or modifying code, when fixing
  Sonar issues, when remote Sonar is down, or when the user asks for
  compliant / Clean as You Code work.
---

# Sonar agent analysis (SonarLint / SonarQube)

## Contract

1. Local MCP/IDE results are **not** a CI quality-gate pass.
2. Prefer **remote** connected mode; fall back to **local Docker** server; then standalone IDE.
3. Reload policy on every session: `sonar-mcp-up --workspace "$PWD"`.
4. For C#, also follow skill `csharp-sonarqube`.

## Bridge

```bash
[ -f ~/.config/sft/bridge.env ] && . ~/.config/sft/bridge.env
BRIDGE="${SFT_AGENT_BRIDGE:-${BRIDGE:-}}"
# If unset, use your EasyScanPKG checkout path.
```

## Task start

```bash
"$BRIDGE/bin/sonar-mcp-up" --workspace "$PWD" --json
```

- If credentials missing → user gets a **prompt** for URL/token
- If remote unreachable/expired token → **warning dialog**, then prompt to update; else **local fallback**
- Note `backend` in JSON: `remote`, `local`, or `standalone`
- Disable automatic analysis via MCP `toggle_automatic_analysis` if available

## Task end (required)

1. Collect absolute paths of files created or modified.
2. Prefer MCP `analyze_file_list` when IDE bridge is up (`ide_port` in spin-up output).
3. Else: `"$BRIDGE/bin/sonar-analyze" --json <abs-paths...>`
4. Fix BLOCKER/CRITICAL findings; re-analyze.
5. Re-enable automatic analysis if disabled earlier.
6. Do not claim server quality gate passed without CI confirmation.

## Credential issues mid-task

```bash
"$BRIDGE/bin/sonar-credentials" --test
"$BRIDGE/bin/sonar-mcp-up" --workspace "$PWD"
```

## Fallback order

1. Remote connected + IDE `analyze_file_list`
2. Local Docker SonarQube (`backend=local`) + IDE/MCP
3. Standalone SonarLint via IDE only
4. Report analysis unavailable (hooks fail-open)

## New machine

Run once: `"$BRIDGE/install.sh" --workspace "$PWD" --project-key <key>`
