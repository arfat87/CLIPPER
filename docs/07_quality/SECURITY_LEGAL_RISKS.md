---
id: security-legal-risks
owner: orchestrator
status: approved-v2
---
# Security, legal and risk register

## Legal
Downloading from YouTube/Instagram/Facebook may violate platform terms unless the owner owns the content or has permission. Personal-use default; UI requires a "I have rights to this content" checkbox (`rights_confirmed` stored on the job); public deployment must enforce it. Respect font and music licences (no music in v1). Do not redistribute downloaded sources.

## Security controls
| Threat | Control |
|---|---|
| Secret leakage | `.env`/`secrets/` gitignored; redacting logger; pre-commit secret scan; never in tickets/email/UI |
| Prompt injection via transcript/Drive/web | Data-not-instructions rule; schema-validated LLM output; no tools for scorer; index validation; sanitised titles |
| Malicious archive (template import) | zip-slip protection, size/file-count caps, type allow-list, no executables |
| SSRF via URL input | http/https only, block private/loopback/link-local ranges, redirect limits |
| Command injection | Argument lists for ffmpeg/yt-dlp; strict URL parsing; no shell=True |
| Drive abuse | Read-only scopes; no mutating calls (tested); untrusted content; path sanitisation |
| Admin exposure | Off by default in prod; argon2 password; session cookie; rate limit; audit log |
| Data privacy | Local-first; only transcript text (and optionally frames) to the chosen LLM; Ollama keeps it local; delete job removes files |
| Dependency risk | Pinned versions, lockfiles, justification for new deps, periodic audit |

## Risk register
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| yt-dlp breaks on platform change | High | Med | Pinned + `make update-ytdlp`, nightly test, clear error, upload fallback |
| IG/FB login walls | High | Med | Cookies blocker; local upload path |
| Hinglish/Bengali ASR errors | High | High | Bake-off, pluggable engines, transcript editor, confidence down-rank |
| Complex-script rendering bugs | Med | High | libass+HarfBuzz test at T-0.4, snapshot suite, per-script fallbacks |
| LLM picks weak clips | Med | High | Hybrid fusion, feedback tuning, evaluation harness, Clip Brief |
| Long videos slow/costly | Med | Med | Chunking, caching, map-reduce budget, GPU worker |
| Agents conflict/drift | Med | High | Ownership map, contract freeze, reviewer gate, round limit |
| Autonomous stalls | Med | Med | Branch-level blocking, status reports, digest |
| Volatile API facts (quotas, scopes) | High | Med | `verify:` notes, re-check tasks |
| Fonts unlicensed | Med | Med | licence field mandatory, `license_ok`, export without fonts |
| Drive token/consent friction | Med | Low | Service account default, OAuth fallback |
