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

## CipherBank findings

Sonar findings for CipherBank application code belong in the CipherBank repository.
Track them in `docs/ISSUE_BACKLOG.md` Track B and open CipherBank PRs separately.
