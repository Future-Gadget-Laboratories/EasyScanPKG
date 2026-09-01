# Using EasyScanPKG Sonar from other agent environments

This playbook is for **Cursor cloud agents, Codex, or any checkout** that can run Docker + the EasyScanPKG scripts.

## Goals

1. **Activate** local Sonar (or connect remote)
2. **Scan** the workspace
3. **Find the fix list** (checklist)
4. **Fix until empty**

## One-time setup

```bash
git clone <EasyScanPKG-url> ~/EasyScanPKG   # or use existing checkout
cd ~/EasyScanPKG
./commission.sh --workspace /path/to/your/app --skip-remote-creds --no-prompt
./bin/install-skills.sh                    # installs into ~/.cursor/skills
```

Skills installed:

| Skill | Purpose |
| --- | --- |
| `easyscan-bootstrap` | Start Sonar, contexts, verify |
| `sonar-fix-queue` | Find checklist + fix loop until empty |
| `sonar-local-ops` | Projects, scan, issues CLI detail |
| `sonar-mcp-lifecycle` | MCP / credentials / policy DB |
| `sonar-agent-analysis` | End-of-task IDE/MCP file analyze |

## Every session

```bash
. ~/.config/sft/bridge.env
"$BRIDGE/bin/sonar-local-up"
"$BRIDGE/bin/sonar-context" use local   # or your named context
"$BRIDGE/bin/easyscan-check" --require-local
```

## Local access token for agent skills

Agents need `SONARQUBE_TOKEN` in `~/.config/sft/sonar-local.env` (never print it).

```bash
# Preferred — mint from local Docker Sonar
"$BRIDGE/bin/sonar-credentials" --local --bootstrap --test

# Or paste a token from the local UI (My Account → Security)
"$BRIDGE/bin/sonar-credentials" --local --cli --test

# Inspect paths only (safe in chat)
"$BRIDGE/bin/sonar-credentials" --paths --json

# Load into this shell without echoing
set -a; . ~/.config/sft/sonar-local.env; set +a

# Bind a context to the token *file* (stores a ref, not the secret)
"$BRIDGE/bin/sonar-context" create local \
  --url http://127.0.0.1:9000 \
  --token-ref ~/.config/sft/sonar-local.env \
  --project-key local-<repo> --use
```

Skill **`sonar-local-ops`** has the full local-token cookbook; **`sonar-mcp-lifecycle`**
covers remote vs local credential CLIs.

## Find things to fix

```bash
# Full open-issue checklist
"$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" --refresh
$EDITOR "$PWD/.sft/issue-checklist.md"

# Or critical only
"$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" \
  --severity BLOCKER,CRITICAL --refresh \
  --output .sft/issue-checklist-critical.md
```

Remediation hints: `$BRIDGE/templates/remediation.easyscanpkg.json`  
Human summary: `$BRIDGE/docs/FIX_QUEUE.md`

## Fix until empty

Follow skill **`sonar-fix-queue`**: code fixes → re-scan → `export --refresh` → stop at `open_count=0`.

## Remote Sonar instead of local

```bash
"$BRIDGE/bin/sonar-credentials" --cli --test   # writes ~/.config/sft/sonar.env
"$BRIDGE/bin/sonar-context" create remote-prod \
  --url https://sonar.example.com --token-ref ~/.config/sft/sonar.env \
  --project-key my.project --use
```
