# Lumin Product Checklist

This is the working punch list for making Lumin faster, better for the user, and simpler.

## Faster

- [x] Reduce dashboard task polling delay so task progress appears almost immediately.
- [x] Skip compression verification on one-shot dashboard/mobile control requests.
- [ ] Keep the NanoClaw control runtime warm between requests instead of paying fresh startup overhead.
- [ ] Stream task progress and partial output back into the chat while the desktop agent is working.
- [ ] Add a fast-path router for simple control tasks like file listing, file reading, and short summaries.
- [ ] Track latency breakdowns separately for queue time, startup time, model time, and UI wait time.
- [ ] Benchmark cold start vs warm start so speed work is measurable.

## Better For The User

- [x] Make chat behave like a support-style control surface with visible task states.
- [x] Add built-in starter presets for common working styles.
- [x] Add `autoresearch` preset inspired by Karpathy's March 2026 loop.
- [ ] Default "workspace" requests to the host app repo path users actually mean.
- [ ] Show a clearer "what happened" timeline for each task: queued, claimed, running, completed, fallback.
- [ ] Add a first-run guided setup that verifies desktop agent, NanoClaw auth, and repo access.
- [ ] Show preset descriptions and recommended use cases in both dashboard and mobile settings.
- [ ] Add reusable prompt templates for common tasks like repo scan, write draft, compare options, and research brief.

## Simpler

- [x] Add built-in presets so users do not need to import OpenClaw files just to get started.
- [ ] Reduce competing dashboard metrics so every card derives from the same daily data source.
- [ ] Hide internal control traffic entirely from user-facing request history and summaries.
- [ ] Consolidate "group", "preset", and "mode" language so the mental model is easier to understand.
- [ ] Add one obvious primary action in settings: "Make this my main agent."
- [ ] Replace path-heavy copy with friendlier labels unless the user explicitly wants technical detail.
- [ ] Write a short "How Lumin works" doc for migration users coming from OpenClaw.

## AutoResearch Notes

The `autoresearch` preset follows the same broad shape described in Andrej Karpathy's March 2026 `karpathy/autoresearch` repo:

- narrow editable surface
- short fixed-budget experiments
- objective metric
- keep or discard based on measured improvement
- persistent experiment log
