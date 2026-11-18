---
trigger: always_on
description: Security and privacy rules
---
- Secrets only in `.env` or `secrets/` (gitignored). Never print them. Never send them by email.
- Drive access is read-only unless a task explicitly says otherwise. Never delete anything in Drive.
- Downloaded videos are private user data: no uploads to third parties except the configured LLM provider, and only transcript text/frames the task requires.
- Pin dependency versions. New dependency needs a one-line justification in the PR/ticket.
