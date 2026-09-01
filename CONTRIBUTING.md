# Contributing to EasyScanPKG

## Development

```bash
python3 -m unittest discover -s tests -v
./bin/easyscan-check --offline
```

## Pull requests

- Keep changes focused on the helper (bash/Python/skills/docs).
- Do **not** commit tokens, `sonar-local-admin.json`, `.env` secrets, or Sonar image layers.
- Do **not** vendor Build Wrapper or commercial plugins.
- Prefer Apache-2.0 compatible contributions.

## Scanned application findings

Sonar findings for application code belong in that application's repository, not here.
Use `docs/ISSUE_BACKLOG.md` for EasyScanPKG tooling work only.
