# Credential-leak prevention and CI enforcement (MF-011)

## Implemented

MeasuredField includes a zero-network Python scanner that checks tracked
worktree paths or changed staged Git blobs. It detects recognizable secret
material and prohibited sensitive filenames. Findings contain only a relative
filename, line number and rule category. Values are not printed or submitted to
a provider.

Run from the canonical MeasuredField checkout:

    python -B scripts/secret_scan.py --tracked
    python -B scripts/secret_scan.py --staged

The tracked mode scans all Git-tracked files as present on disk; it runs after
checkout in CI. The staged mode scans Git index blobs, not just the working
tree. It therefore detects credentials added to the index even if the source
file was subsequently edited without re-staging.

The scanner recognizes common OpenAI, Anthropic, GitHub, Stripe, Google, Slack
and AWS key formats, PEM private-key headers, credential-bearing service URLs,
and sensitive filenames such as .env, .env.production, credentials.json,
token.json, .pem, .key and .p12.

The GitHub Actions CI job runs this check BEFORE dependency installation and
database integration tests, and fails when findings exist.

## Remaining limitations

This is a deterministic pattern scanner, not proof that the repository is
secret-free. Obfuscated strings, unknown token formats, encrypted blobs, huge
files, and already-published Git history require independent security tools.

Before the first public push:
- Use an established scanner against the entire repository history.
- Enable provider/GitHub push-protection where available.
- Review CI artifacts, logs, third-party integration permissions and secrets.
- Rotate/revoke any exposed credential. Deleting it from the latest commit is
  insufficient to make the old credential safe.

## Multi-agent release procedure

Only the agent holding the board integration gate may stage, run the full
regression suite and commit. Use explicit file paths with git add, never
git add -A when different agents share the same checkout.

If the scanner finds a credential, treat it as a release blocker. Post only
its rule category and file location to the board, never the detected value.

All scanner test fixtures are synthetic and built from split string fragments.
No real customer or provider secrets are needed.
