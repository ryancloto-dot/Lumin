# Experimental Features

This package contains opt-in experimental features that are not part of the
default Lumin path.

Goals:

- keep risky features isolated from the core proxy path
- make every experiment request-scoped and easy to disable
- keep metrics and headers separate from stable features
- allow gradual promotion from experiment → Pro feature

## Current experiments

- `cget_v0`
  - safe output trimming
  - preamble trimming
  - optional signoff trimming
  - no streaming cancellation yet

## Request shape

Clients can enable experiments per request:

```json
{
  "model": "gpt-5.4-mini",
  "messages": [{"role": "user", "content": "Review this diff"}],
  "lumin_experiments": ["cget_v0"]
}
```

Legacy single-feature toggles are also supported:

- `lumin_experimental_cget`
- `lumin_experimental_scaffold_fill`
- `lumin_experimental_spe`

## Global controls

- `LUMIN_ENABLE_EXPERIMENTS=true|false`
- `LUMIN_ALLOWED_EXPERIMENTS=cget_v0,scaffold_fill_v0,spe_v0`
