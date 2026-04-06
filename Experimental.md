# Experimental Features

This file is the repo's experimental-features tab.

Use it for high-upside ideas that are promising, measurable, and explicitly opt-in, but not yet safe enough to become default Lumin behavior.

## Active Experimental Features

| Name | Status | Tier | Main Goal | Best Fit | Main Risk |
|---|---|---|---|---|---|
| Scaffold + Fill | Designed | Pro Experimental | Reduce expensive-model output by having a strong model emit only the hard structure, then a tiny model fill predictable boilerplate | Python codegen, CRUD, schemas, repetitive tests | Cheap model invents logic if placeholders are too open |
| Speculative Preflight Execution (SPE) | Designed | Pro Experimental | Predict likely next agent steps, prefetch likely tool results, and later draft predictable follow-up steps cheaply | Tool-heavy agent loops, repeated debugging patterns, NanoClaw/OpenClaw workflows | Prediction misses waste compute or introduce orchestration complexity |
| Confidence-Gated Early Termination (CGET) | Designed | Pro Experimental | Trim or stop low-value output once the useful answer is already delivered | Agentic/tool-call responses, terse factual answers, codegen postamble trimming | Cutting off something substantive and degrading trust |

## Experiment: Scaffold + Fill

### Goal

Reduce output token cost by splitting code generation into two stages:

1. A strong model writes the high-value structure, logic, and decisions.
2. A tiny cheap model fills tightly constrained placeholder regions.

The user still receives one normal final Python output.

### Why It Matters

- Could save much more than alias-style output compression on boilerplate-heavy code.
- Plays especially well with Python codegen workloads.
- Keeps the expensive model focused on reasoning and architecture instead of repetitive field expansion.
- Feels like secret-sauce routing plus transpilation combined.

### Best Targets

- Pydantic models
- FastAPI CRUD routes
- request/response schema boilerplate
- repetitive tests
- repository/service glue
- serializer or mapping boilerplate
- logging/error-wrapper boilerplate

### Bad Targets

- algorithmic code
- security-sensitive code
- concurrency-heavy code
- subtle business logic
- refactors touching existing complex code
- anything where placeholder content is not predictable from local context

### Proposed API Shape

Request-level experimental flags only:

- `lumin_experimental_scaffold_fill: true|false`
- `lumin_fill_language: "python"`
- `lumin_fill_model: "gpt-5.4-nano"` by default
- `lumin_fill_verify: true|false` default `true`

Response headers:

- `X-Lumin-Experimental-Scaffold-Fill: on|off`
- `X-Lumin-Fill-Status: pass|fallback|skipped|verify_fail`
- `X-Lumin-Fill-Model: <model name>`
- `X-Lumin-Fill-Saved: <dollar amount>`

### Flow

1. User sends a normal code-generation request.
2. Lumin classifies whether this looks like boilerplate-heavy Python generation.
3. If not, skip the experiment.
4. If yes, Lumin adds a scaffold instruction for the expensive model.
5. Expensive model returns Python with explicit placeholder blocks.
6. Lumin extracts each placeholder and sends only that local context to the tiny fill model.
7. Tiny fill model replaces placeholders.
8. Lumin verifies the completed code.
9. If verification passes, return the fully expanded Python to the user.
10. If anything fails, fall back to the normal expensive-model-only path.

### Safety Rules

- Never use this by default.
- Pro experimental only.
- Python only in v1.
- Never allow the fill model to invent hidden reasoning.
- Never allow the fill model to rewrite non-placeholder code.
- If verification fails, discard the filled result.
- If placeholder detection is ambiguous, skip the experiment.

### Verification

Minimum verification for v1:

- all placeholders removed
- final code parses with `ast.parse()`
- final code compiles
- output is still Python-only
- no unexpected edits outside placeholder ranges

### Activation Gate

Only run when all of these are true:

- request is explicitly opt-in
- language is Python
- prompt looks like code generation
- predicted output is large enough
- prompt looks boilerplate-heavy
- predicted savings exceed the extra second-pass overhead

### Metrics

- average expensive-model output tokens saved
- average cheap-model fill tokens spent
- net dollar savings per request
- verification pass rate
- fallback rate
- placeholder classes with best savings
- failure rate by scaffold type

## Experiment: Speculative Preflight Execution (SPE)

### Goal

Predict the likely next agent step before the current step finishes, then prefetch or prepare the most likely follow-up so Lumin can reduce latency and eventually skip some expensive model calls entirely.

### Why It Matters

- Attacks the number of calls, not just the size of each call.
- Gives Lumin a second major value prop: lower cost and faster agent execution.
- Fits NanoClaw/OpenClaw-style loops where the next action is often highly predictable.

### Best Targets

- debugging loops
- read-file then patch-file workflows
- run-tests then inspect-failure flows
- list-directory then read-file exploration loops
- repeated route/model generation tasks

### Bad Targets

- creative writing
- broad brainstorming
- novel exploratory tasks with weak repetition
- highly ambiguous multi-path reasoning sessions

### Proposed API Shape

Request-level flags:

- `lumin_experimental_spe: true|false`
- `lumin_spe_mode: "prefetch" | "draft"` with `prefetch` as the safe first mode

Response headers:

- `X-Lumin-Experimental-SPE: on|off`
- `X-Lumin-SPE-Status: prefetched|drafted|skipped|fallback`
- `X-Lumin-SPE-Confidence: <float>`

### Flow

1. Observe the current agent step.
2. Predict the likely next step using heuristics first, learned models later.
3. If confidence is high enough:
   - prefetch likely tool results
   - or warm the cache
   - or later draft the likely next cheap-model response
4. When the real next request arrives, compare it to the prediction.
5. Use the prefetched result or speculative draft only when it matches tightly enough.

### Safety Rules

- Never mutate session state based on speculative work alone.
- Never serve a speculative draft unless verification passes.
- Disable automatically when prediction accuracy is poor for a session.
- Start with prefetch only before any draft-serving path.

### Verification

- compare predicted next step vs real next step
- measure hit rate by workflow type
- for speculative drafts, compare draft vs real response on a sampling basis

### Activation Gate

- opt-in only
- agentic workflow detected
- enough session history exists
- previous prediction accuracy is above threshold

### Metrics

- prediction accuracy
- latency saved
- speculative results reused
- expensive calls skipped
- draft verification pass rate

## Experiment: Confidence-Gated Early Termination (CGET)

### Goal

Reduce wasted output tokens by trimming or stopping low-value output once the useful answer is already delivered.

### Why It Matters

- It attacks the output side, which is still largely untouched.
- Output tokens are often the most expensive tokens.
- Agentic consumers do not benefit from pleasantries, signoffs, or repeated summaries.

### Best Targets

- tool-call responses
- agentic factual answers
- code generation responses with long post-code explanations
- structured debugging answers

### Bad Targets

- creative writing
- brainstorming
- open-ended human conversation
- anything explicitly asking for detailed explanation

### Proposed API Shape

Request-level flags:

- `lumin_experimental_cget: true|false`
- `lumin_cget_mode: "preamble" | "signoff" | "full"`

Response headers:

- `X-Lumin-Experimental-CGET: on|off`
- `X-Lumin-CGET-Status: trimmed|terminated|skipped|fallback`
- `X-Lumin-CGET-Saved-Tokens: <int>`
- `X-Lumin-CGET-Confidence: <float>`

### Flow

1. Classify request intent.
2. For safe agentic intents, buffer the response stream.
3. Detect preamble, signoff, or filler segments.
4. Trim or terminate only when confidence is high enough.
5. Sample full completions in the background to verify safety.

### Safety Rules

- Ship off by default.
- Start with preamble trimming only.
- Disable automatically for creative or conversational requests.
- If confidence is low, do nothing.
- If verification misses are too high, auto-disable for the session.

### Verification

- compare trimmed vs full sampled outputs
- log whether removed content contained substantive information
- maintain per-model and per-intent safety thresholds

### Activation Gate

- opt-in only
- task-oriented or agentic intent
- model profile supports it
- no explicit request for detailed explanation

### Metrics

- output tokens saved
- preamble tokens trimmed
- signoff tokens trimmed
- early termination miss rate
- savings by model and intent type

## Experiment Template

Copy this section for future ideas.

### Experiment: <name>

#### Goal

#### Why It Matters

#### Best Targets

#### Bad Targets

#### API Shape

#### Flow

#### Safety Rules

#### Verification

#### Activation Gate

#### Metrics

#### Next Step
