# Cognisync TUI

Keyboard-first Ratatui client for the CogniSync API. The backend requires JWT
authentication on conversations, models, and tools, so the TUI signs in first
(`/login` or `/register`; `F2` swaps between the two) and attaches the bearer
token to every request.

## Run

Start the backend (`python -m backend.main`), then:

```bash
cd cognisync-tui
cargo run -- --backend-url http://localhost:8001
```

The default is `http://localhost:8001`. It can also be set via the
`COGNISYNC_BACKEND_URL` environment variable or `config.toml` (copy
`config.example.toml`). Remote targets must use HTTPS unless explicitly allowed
in that config.

## Keys

`/` opens commands; arrows and Enter navigate them; Escape dismisses. `Ctrl+N`,
`Ctrl+H`, `Ctrl+M`, and `Ctrl+T` open new session, history, models, and backend
tools. Enter submits the prompt. `Ctrl+C` exits cleanly.

Before sending a prompt, use `/models` (or `Ctrl+M`). The picker loads the
backend's real eligible catalog. Use Up/Down to move, Space to add/remove a
model, Enter to save the selection, and Escape to discard picker edits. The
server advertises the enforced selection bounds (default: 1–4), and every
confirmed model id is sent in the council request.

Use `/search` to toggle server-side DuckDuckGo grounding for the next prompts.
The final conversation event includes its actual source titles and URLs when
results are available; a failed or empty search is reported by the backend
rather than shown as a success.

History lists only the signed-in user's conversations (`Ctrl+H`); `d` deletes
the highlighted conversation, Enter reopens it. Prompt runs use the SSE
`/api/conversations/{id}/message/events` endpoint and display streamed model
results plus the persisted final synthesis. Screens that are not backed by
server endpoints state that limitation instead of inventing behavior.

## Runs, states, and rankings

A prompt is sent to `POST /api/conversations/{id}/message/events` with the
JWT only in the `Authorization` header. The TUI renders the backend's real
event sequence: `stage1_start` (selected models become *thinking*),
`stage1_result` (each card settles as *complete* or *failed*),
`stage2_start`/`stage2_result` (peer review), `rankings` (real aggregate
average ranks), `stage3_start`/`stage3_result` (chairman), then the terminal
`final` payload (answer, sources, search metadata) and `done`.

The animated spinner only runs while a request is in flight and is driven by
the terminal event loop's tick, so keyboard input stays responsive. States
are inferred only from request start and received events; there is no fake
progress. If the stream ends without a terminal event the TUI clears loading
and reports a protocol error instead of showing a blank or stuck chat.

Rankings use the backend contract: lower average rank is better. A winner is
declared only when one model has the unique best average; equal best averages
are shown as a tie. Web search is a backend-integrated DuckDuckGo service
(toggled with `/search`), not an MCP tool; the TUI renders its actual status,
result count, errors, and source titles/URLs — no results are fabricated.

Cancellation is not supported by the current backend (there is no cancel
endpoint); quitting the TUI stops reading the stream but the server still
finishes and persists the run.
