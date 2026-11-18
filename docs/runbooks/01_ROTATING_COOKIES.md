# Runbook 1 — Rotating YouTube / Instagram / Facebook Cookies

## When to Use
- A job fails during `INGEST` with `COOKIES_REQUIRED` (`HTTP 403`, age-restricted video, Instagram/Facebook login wall, or YouTube bot check).
- The HITL subsystem raises a `CREDENTIAL` blocker (`CLIPPER_COOKIES_TXT_REQUIRED`).

## Procedure
1. Export a fresh Netscape-format `cookies.txt` from a throwaway/dedicated browser session (using an extension like *Get cookies.txt LOCALLY*).
2. Place the file in the gitignored `secrets/` directory:
   ```bash
   mkdir -p secrets
   cp ~/Downloads/cookies.txt secrets/cookies.txt
   chmod 600 secrets/cookies.txt
   ```
3. Verify `.env` points to the cookie file:
   ```dotenv
   CLIPPER_COOKIES_FILE=secrets/cookies.txt
   ```
4. Validate cookie health without exposing secrets:
   ```bash
   curl -s http://localhost:8000/api/v1/health | jq '.subsystems'
   ```
5. Resolve any open cookie blocker via CLI or Admin UI:
   ```bash
   .venv/bin/python -m hitl.cli resolve BLK-XXXX --note "Rotated secrets/cookies.txt"
   ```

> **Security Rule**: Never commit `secrets/cookies.txt` to git or paste cookie contents into issues/emails. `make secret-scan` will reject any file containing Netscape cookie headers.
