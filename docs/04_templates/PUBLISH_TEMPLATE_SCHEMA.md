---
id: publish-template-schema
owner: pipeline
status: approved-v2
---
# Publish template (title / description / hashtags)

```json
{ "schema_version":1, "meta":{"id":"yt_shorts_hinglish","name":"YouTube Shorts - Hinglish","version":1},
  "platform":"youtube_shorts", "language":"hinglish", "script":"latn",
  "title":{"template":"{{title}}","max_chars":70,"hashtags_in_title":0,"case":"sentence"},
  "description":{"template":"{{hook}}\n{{cta}}\n\n{{summary}}\n\n{{transcript_excerpt}}\n\n{{hashtags}}",
                 "max_chars":5000,"transcript_excerpt":{"enabled":true,"max_chars":600},
                 "source_credit":"Source: {{source_title}} ({{source_url}})"},
  "hashtags":{"count":[3,5],"include":["#Shorts"],"ban":[],"style":"CamelCase"},
  "tags":{"max_total_chars":500},
  "pinned_comment":{"enabled":true,"template":"Aapko kya lagta hai?"},
  "rules":{"first_line_max_chars":100,"no_links_in_first_line":true} }
```
## Variables
`{{title}} {{hook}} {{summary}} {{cta}} {{hashtags}} {{tags}} {{transcript_excerpt}} {{source_title}} {{source_url}} {{channel_name}} {{channel_handle}} {{clip_index}} {{date}}`
Unknown variable -> validation warning, rendered as empty. No code execution in templates (logic-less mustache-style only).

## Platform limits (from `config/platforms.yaml`, verify periodically)
YouTube title <= 100, description <= 5000, tags total <= 500, hashtags counted <= 15 per video; Instagram caption <= 2200; TikTok caption short (configurable).

## Validation
Render template with a sample clip in en/hi/hinglish/bn, run limit validator, show counts live in admin.
