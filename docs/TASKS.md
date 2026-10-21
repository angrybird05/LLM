# TASKS.md — From FastAPI backend to a terminal coding agent

Goal: turn this project into an interactive terminal coding agent like Claude Code.
Work through the asks **in order** — each one builds on the previous. Check off tasks
as they land. One ask ≈ one working session.

---

## Comparison: Claude Code vs this project today

| Capability | Claude Code | This project today |
|---|---|---|
| Agent loop (LLM → tools → LLM → … until done) | ✅ core of the product | ❌ **one** LLM call; tool results are never fed back |
| Streaming with tool calls | ✅ | ❌ `stream_complete` drops `tool_use` blocks; `stream_chat` ignores tools |
| Interactive terminal UI (REPL) | ✅ | ❌ one-shot CLI command |
| Tool set | Read, Write, **Edit**, Glob, Grep, Bash, WebFetch/Search, Task, Todo… | read/write/list/glob/grep/bash/web_* — no Edit |
| Shell execution | runs locally in your cwd, permission-gated | Docker-only sandbox (`unix://` socket — broken on this Windows machine); files written by `write` are invisible to `bash` |
| Permission system (approve before act) | ✅ prompts, allowlists, modes | ❌ none — tools execute unconditionally |
| System prompt / project memory (CLAUDE.md) | ✅ | ❌ none sent |
| Context management (compaction, token budget) | ✅ auto-compaction | ❌ full history resent forever |
| Session resume (`--continue`, `--resume`) | ✅ | ⚠️ sessions exist in DB, no resume UX |
| Git awareness (status, commits, diffs, undo) | ✅ | ❌ none |
| Slash commands (/help /model /compact …) | ✅ | ❌ |
| Subagents, plan mode, MCP, hooks | ✅ advanced | ❌ |

**Verdict:** solid skeleton (providers, registry, sessions, sandbox, API), but the two
things that make an *agent* an agent — the loop and the terminal experience — don't exist yet.

---

## Ask 0 — Quick fixes (do first, ~30 min) ✅ DONE 2026-08-22

Small repairs that unblock everything else:

- [x] Add `[project.scripts]` entry point to `pyproject.toml`
      (`coding-agent = "coding_agent.main:cli"`), then `pip install -e .` so the README commands work
- [x] Fix auth bypass in `src/coding_agent/api/middleware/auth.py` — bypass was keyed off
      `RATE_LIMIT_ENABLED=false && APP_ENV=development`; now gated by explicit `AUTH_ENABLED`
      (default true). Verified: no key -> 401, valid key -> 200
- [x] Health check: 1s timeout on Redis probe; reports `redis: disabled` when rate limiting is off.
      Verified: /health responds fast with status ok
- [x] Update stale model lists (`core/llm/anthropic.py`, `openai.py`) to current IDs;
      default model now `claude-sonnet-5`

## Ask 1 — The agentic loop (the heart) 🔴 ✅ DONE 2026-08-22

Claude Code's essence: call the model → if it asks for tools, execute them → feed results back →
repeat until the model answers without tool calls.

- [x] Rewrite `ChatService.process_chat` into the loop; iteration cap `AGENT_MAX_ITERATIONS=25`
      with a clean "[stopped]" notice
- [x] Assistant turns persist WITH their tool_use blocks; migration `002` adds
      `messages.seq` (exact ordering) and `tool_calls.tool_use_id` (valid round-trip)
- [x] History rebuild (`_build_history`) emits assistant(tool_use) followed by per-call
      `tool` result messages — verified against both providers' formats
- [x] `stream_chat` is tool-aware and yields structured events:
      `session | text | tool_use_start | tool_start | tool_result | turn_done`
- [x] Providers emit event dicts from raw streams (Anthropic content blocks, OpenAI deltas)
- [x] Tool errors are fed back to the LLM as error results so it can recover
- [x] Verified: offline scripted loop (write→bash→answer), history round-trip, session resume,
      streamed tool events, plus one live provider call

**Files:** `services/chat_service.py`, `core/llm/base.py`, `core/llm/anthropic.py`, `core/llm/openai.py`, `models/orm.py`, `alembic/versions/002_*`
**Done when:** ~~agent performs write → bash → final answer in one turn~~ ✔ (`verify_ask1.py`)

## Ask 2 — Local-first tools + Edit 🟠 ✅ DONE 2026-08-22

A terminal agent must work on *your* repo in *your* shell, not an isolated container.

- [x] New `edit` tool — exact unique-match string replacement, helpful errors
      (not found / ambiguous with count), replace_all option
- [x] Local shell backend: `SHELL_MODE=local` (default) runs bash on the host in the
      workspace (Git Bash auto-detected, falls back to cmd); Docker stays opt-in via
      `SHELL_MODE=docker`. Timeout + output truncation included
- [x] Path safety: all file tools resolve inside `WORKSPACE_ROOT` (default: launch dir);
      escapes rejected
- [x] glob/grep/list skip `.git`, `.venv`, caches etc. and honor `.gitignore` (pathspec);
      grep caps at 200 matches, read at 2000 lines, outputs truncated at 30k chars
- [x] `web_search` implemented for real (DuckDuckGo HTML endpoint, no API key needed)
- [x] Sandbox retained as opt-in (`core/tools/shell.py` unchanged)

**Files:** new `core/tools/{edit,shell_local,paths}.py`; rewritten `filesystem.py`, `web.py`, `tools/__init__.py`; new settings in `config.py`
**Done when:** ~~agent edits a real file and runs commands locally~~ ✔

## Ask 3 — The terminal REPL 🟠 ✅ DONE 2026-08-23 (polish completed)

- [x] `coding-agent` (no args) launches the REPL: banner, streaming markdown panel,
      per-turn status line (tokens ↑↓ · steps · seconds)
- [x] Tool activity rendered Claude Code style: `● name(args)` + dimmed output summary;
      `edit` calls show a red/green diff before/after lines
- [x] Slash commands: `/help /clear /model /provider /sessions /resume /tools /tokens /exit`
      (/compact deliberately deferred to Ask 6)
- [x] Ctrl+C interrupts the current turn without quitting; Ctrl+D/Ctrl+C at prompt exits
- [x] SQL echo suppressed inside the REPL; UTF-8 forced on Windows streams (cp1252 fix)
- [x] `chat` subcommand kept for one-shot/scriptable use, now event-aware (`--stream`)
- [x] Multi-line input and ↑ command history ✅ 2026-08-23 — new `tui/input_line.py`
  (LineReader on prompt_toolkit): Enter submits, trailing `\` continues onto the
  next line, Alt+Enter always continues; pasted text keeps its newlines (bracketed
  paste) for edit-then-submit; ↑/↓ recall history when the cursor is on the first/
  last line and move within multi-line drafts otherwise; history persists across
  launches at `~/.agent/history` (FileHistory, pre-loaded synchronously so the
  first ↑ can't outrun the async loader). Piped/non-TTY input falls back to plain
  console lines with the same `\` rule (smokes unaffected), and an undrivable
  terminal degrades to that fallback instead of crashing. Verified by
  `verify_polish.py`: 11 checks driving real key bindings headlessly via pipe
  input. Bonus fix surfaced by the smoke: the REPL never ran `init_db()`, so a
  fresh DATABASE_URL crashed on the first DB touch — now initialized at startup

**Files:** new `tui/app.py`, `tui/renderer.py`; `main.py` callback wiring
**Done when:** ~~full coding conversation happens in the terminal~~ ✔ (multi-line/history polish open)

## Ask 4 — Permissions & safety 🟡 ✅ DONE 2026-08-22

- [x] Approval gate before risky tools (bash, write, edit): yellow panel shows the action
      (command / diff preview), then prompts `[y]es / [n]o / [a]lways this session`
- [x] Permission modes like Claude Code: `default` (ask) / `auto-edit` (edits auto, bash asks) /
      `full-auto` — via `--mode/-m` flag, `PERMISSION_MODE` setting, or `/permissions <mode>`
      live in the REPL; invalid values rejected at startup and in the command
- [x] Dangerous-bash deny-list (`rm -rf`, sudo, force-push, drop table, shutdown, disk-wipe
      patterns…) bypasses mode shortcuts — full-auto still asks, and a "no" is obeyed
- [x] Diff preview shown *before* an edit applies; decline feeds back to the LLM as a
      structured `{error, declined: true}` result so it adapts instead of crashing;
      renderer shows `⏎ user declined`; headless callers (API/no UI) get a fail-safe deny
- [x] "always" approvals persist per session (`session_allow` set); `/clear` and new
      sessions reset them; other risky tools keep asking

**Files:** new `core/permissions.py`; `ChatService(permission_gate=…)` hook in `_run_tool`;
`tui/app.py` prompt loop + `/permissions`; `tui/renderer.py` approval panel
**Done when:** ~~nothing touches disk or runs a command without consent unless a mode allows it~~ ✔
(`verify_ask4.py`: 8/8 checks incl. deny-feedback-to-model, always-scope, danger-list override,
headless fail-safe, previews)

## Ask 5 — System prompt & project memory 🟡 ✅ DONE 2026-08-23

Right now the model gets zero context about where it is.

- [x] Real system prompt built per turn by new `core/context_builder.py`: identity +
      guidelines, OS/shell/Python/cwd/date, workspace tree (2 levels, ignores junk),
      tool list with usage guidance — ~2k chars total
- [x] Project memory: `AGENT.md` preferred, falls back to `CLAUDE.md`; global
      `~/.agent/AGENT.md` merged alongside; each capped at 8k chars; `/memory` shows
      what was found and its contents
- [x] Git snapshot in the prompt: branch, dirty/clean + untracked count, last 5 commit
      subjects (GitPython); degrades to "(not a git repository)" outside a repo
- [x] Prompt injected fresh per user turn (stable across loop iterations → provider-cache
      friendly), never persisted into the session transcript
- [x] Fixed two latent Ask 1 bugs surfaced by live testing:
      * **Anthropic streaming dropped all tool arguments** — `tool_input_delta` events
        carried no block id, so every streamed tool call arrived as `{}`. Provider now
        tracks block index → id and stamps it on deltas.
      * Anthropic SDK 1.0.0 compat: no `temperature` param; empty tools/system must be
        omitted, not sent as null. Also: SQLAlchemy echo silencing needs
        `logger.disabled = True` (setLevel is ignored by echo writes).
      * ReadTool missing-path now returns a clear error instead of KeyError

**Files:** new `core/context_builder.py`; `chat_service.py` (+system_prompt param),
`llm/anthropic.py`, `tui/app.py` (/memory + injection), `main.py`, `tools/filesystem.py`
**Done when:** ~~asking "what does this project do?" in the REPL gets a correct answer without pasting anything~~ ✔
(live run: agent read README.md + pyproject.toml itself, answered in one sentence, 2 steps / 5.2s;
`verify_ask5.py` 9/9 offline; ask1+ask4 suites still green)

## Ask 6 — Context & token management 🟡 ✅ DONE 2026-08-23

- [x] Context estimate surfaced everywhere: status line shows `ctx ~N` after each turn,
      `/tokens` includes it; chars/4 heuristic in `compactor.py` (no tokenizer downloads),
      exact provider ↑↓ numbers still shown alongside
- [x] Auto-compaction: when the estimate crosses `COMPACT_THRESHOLD` (0.75) of
      `CONTEXT_TOKEN_LIMIT` (200k), older history is summarized by the LLM into a briefing;
      the most recent `COMPACT_KEEP_RECENT` (16) messages stay verbatim. Split always lands
      on a user-message boundary so assistant(tool_use)/tool pairs are never torn apart;
      the summary rides in as a *system* message (no role-alternation or orphaned tool-result
      issues) and is persisted in `session_metadata` so new processes keep it. Rolling:
      a prior briefing is prepended to the next summarizer input and replaced by its output
- [x] Re-summarize guard: history is rebuilt from the DB every turn, so once over the limit
      the estimate alone would fire compaction every turn — compaction now waits until
      `body_len > compact_summarized + COMPACT_KEEP_RECENT`
- [x] `/compact` manual trigger (prints before → after token stats); `/clear` starts fresh
- [x] Tool outputs capped on resend: history replays only a tail (`HISTORY_OUTPUT_LIMIT`
      = 6000 chars, `[truncated head…]` + tail since errors live at the bottom); the FULL
      result stays in the DB for inspection/export
- [x] Fixed a latent service bug this exposed: `SessionService.get()` returned stale
      identity-map instances (fresh writes invisible within one context) — now uses
      `populate_existing=True`

**Files:** new `services/compactor.py`; `chat_service.py`, `session_service.py`,
`config.py` (4 new settings), `tui/app.py` (/compact + ctx display), `tui/renderer.py`
**Done when:** ~~a long session survives past the model's context window without an API error~~ ✔
(`verify_ask6.py`: estimator, safe split, truncation-vs-DB integrity, 6-turn auto-compaction
with tiny CONTEXT_TOKEN_LIMIT [0,0,0,1,2,3], guard determinism, manual stats, streaming ctx;
`verify_ask6_live.py`: one real Anthropic summarize call produced a faithful briefing.
Regression suites ask1/ask4/ask5 green)

## Ask 7 — Sessions, resume & history 🟢 ✅ DONE 2026-08-23

- [x] `--continue` / `-c` resumes the most recent session at launch (tail of the prior
      conversation printed); `--resume` / `-r <id-prefix>` resumes directly; bare
      `--resume` opens an interactive picker before the loop
- [x] `/sessions [n]` shows a numbered newest-first table (row #, id, title, msgs,
      updated); the row number doubles as a resume token — `/resume` and `/delete`
      accept either a row number or an id prefix, and bare `/resume` opens the picker.
      Disambiguation rule: row numbers are 1–2 digits, ids display as 8 hex chars, so
      an all-digit 8-char token resolves as an id
- [x] Auto-titles untitled sessions from their first user message
      (`derive_title`: first line collapsed, ≤40 chars, word-boundary cut) — the free,
      deterministic fallback rather than an LLM call per new session; set once, never
      overwritten by later turns
- [x] `/export [path]` writes the full transcript as markdown: header with title/id/
      timestamps, per-message role sections, every tool call with its arguments and
      result (results clipped at 1500 chars); defaults to the workspace root with an
      auto name `agent-session-<id8>-<stamp>.md`
- [x] `/rename <title>` renames the current session; `/delete [#|prefix]` deletes a
      session (default: the current one) behind a y/N confirmation that names what is
      about to go

**Files:** new `tui/commands.py` (table/picker/resolution/export helpers);
`tui/app.py`, `services/session_service.py` (`most_recent`, `set_title`,
`derive_title`), `chat_service.py` (`_ensure_title`), `main.py` (CLI flags)
**Done when:** ~~closing the terminal and returning picks up exactly where you left off~~ ✔
(`verify_ask7.py`: titling both paths, most_recent ordering, token resolution incl.
digit-prefix edge case, export content/clipping, rename+delete round-trip; piped smoke
of the real CLI: `-c` resumed a live dev.db session with its tail, then /export,
/sessions, /tokens all driven through stdin. ask1/4/5/6 suites still green)

## Ask 8 — Git integration & checkpoints 🟢 ✅ DONE 2026-08-23

- [x] Repo detection at startup (lazy GitPython probe); banner tip points at
      `/git init` when the workspace isn't tracked
- [x] Checkpoints: ChatService gained a `pre_tool_hook` (async observer fired after
      permission approval, before execution). The TUI wires it to a snapshot hook that
      stores target-file bytes before every write/edit into an in-memory LIFO
      `CheckpointStore` — declined calls are never recorded, agent-created files are
      marked "did not exist" so undo deletes them. Session-scoped by design (cleared by
      /clear; bash-made changes rely on git itself)
- [x] `/undo` pops the stack: restores previous content (or removes created files),
      reports what was reverted; works outside git repos too
- [x] `/diff`: branch + diff-stat + colored diff vs HEAD (empty-tree base for repos
      with no commits yet), plus "agent-touched this session" list; graceful fallback
      when not a repo
- [x] `/commit`: stages session-touched files (falls back to all working changes if no
      checkpoint records), one small LLM call drafts a conventional-commit subject from
      the diff stat/excerpt (`generate_commit_message`; deterministic `chore: …`
      fallback on failure), yellow confirm panel names message + files — declining
      unstages. Never pushes anywhere
- [x] `/git init` / bare `/git` for status

**Files:** new `services/git_service.py` (GitService facade, CheckpointStore,
snapshot hook, commit-message generator); `chat_service.py` (`pre_tool_hook`),
`tui/app.py` (/undo /diff /commit /git + wiring), `main.py` untouched this ask
**Done when:** ~~the agent refactors a module, you review /diff, /undo cleanly reverts
it, /commit lands a tidy commit~~ ✔ (`verify_ask8.py`: detect/init, modify+create undo
incl. LIFO ordering, full write-tool run captured through ChatService, diff/stage/commit
round-trip vs HEAD, .gitignore-aware changed_paths, message generation + failure
fallback; `verify_ask8_live.py`: real Anthropic subject
"feat: Implement checkpoint store for git changes". ask1/4/5/6/7 suites green)

## Ask 9 — Advanced (later, pick when ready) 🟣 ✅ DONE 2026-08-23 (MCP deferred)

- [x] **Plan mode**: `/plan <goal>` — read-only research turn (system prompt gets a
  PLANNING MODE suffix, tool set restricted to READ_ONLY_TOOL_NAMES = read/list/
  glob/grep/web_fetch/web_search), prints a numbered plan, y/N confirm, then
  executes it with all tools on approval (`tui/app.py`: generalized `_turn`
  returning final text; `_cmd_plan`; PLAN_SUFFIX)
- [x] **Subagents**: `task` tool spawns a child agent for focused read-only research —
  mini loop (cap 8) over READ_ONLY tools only, shares parent's provider/model and
  permission gate via the execution context, never touches the DB, returns a head-
  clipped report (6000 chars). Non-read-only requests get a structured denial the
  child must record in its report (`core/tools/task.py`, SUBAGENT_SYSTEM prompt)
- [x] **Hooks**: `.agent/settings.json` → `"hooks": {"pre_tool": [...], "post_tool":
  [...]}` with `{tools: [...] | "*", run: "<shell command>"}` rules. Commands run
  through the system shell with env TOOL_NAME / TOOL_ARGS_JSON / SESSION_ID (+ 
  TOOL_RESULT_JSON / TOOL_STATUS on post); a non-zero pre-hook exit VETOES the call
  (structured block result back to the model); post failures only log. Missing or
  broken config = no hooks, never a crash (`core/hooks.py` HookRunner, wired into
  ChatService before checkpoints so vetoed calls are never recorded)
- [x] **Custom slash commands**: `<workspace>/.agent/commands/*.md` → `/<stem>` sends
  the file body as the prompt with `$ARGS`/`$ARGUMENTS` substituted from your
  arguments; rescanned per invocation; listed under /help; built-ins keep priority.
  Unknown-command warning now points at .agent/commands when any exist
  (`tui/commands.py` loaders + dispatch fallback in `tui/app.py`)
- [ ] **MCP client support**: DEFERRED — no local MCP server available to validate
  against offline; protocol risk violates the verify-first rule. Revisit when a
  real server can be tested against
- [x] Verified offline: `verify_ask9.py` — hook load/veto/post-env payload, custom
  command load + $ARGS + help hint, task-tool child restricted to exactly the six
  READ_ONLY tools end-to-end through ChatService, gate-denial propagation inside
  the child, missing-prompt validation. Live: one Anthropic plan turn researched
  with 8 read-only calls and left README.md byte-identical. Piped REPL smoke:
  /plan usage line, custom command in /help, unknown-command hint. Regression
  suites ask1/4/5/6/7/8 all green; ruff clean on changed files. Smoke also caught
  a real bug: READ_ONLY_TOOL_NAMES was not re-exported from core/tools/__init__

## Ask 10 — Chat UI/UX polish ✅ DONE 2026-08-23

- [x] **Compact banner** — no boxed panel: `◆ coding-agent v0.1.0`, one dim line with
  provider · model · mode · ~-abbreviated workspace, one hint line (`/help commands ·
  \ continues a line · ↑ history · ctrl+c interrupts`) (`renderer.banner`)
- [x] **User echo** — REPL quotes each prompt back as `› message` (⏎ markers on
  continuation lines) so the transcript reads like a dialogue; markup escaped so
  `[brackets]` in user text survive (`renderer.echo_user`)
- [x] **Thinking spinner** — `thinking… Ns` appears only after 0.35 s of silence (fast
  tool-to-tool hops never flicker), ticks elapsed seconds, stops on every event and
  restarts while tools run; entirely disabled when stdout is not a TTY so pipes/smokes
  stay clean (`renderer.Spinner`, wired into `_turn` in app.py)
- [x] **Context pressure on the status line** — per-turn summary now ends with `ctx NN%`
  (dim <50%, yellow <50–80%, red ≥ auto-compact threshold) using CONTEXT_TOKEN_LIMIT /
  COMPACT_THRESHOLD from settings
- [x] **Silent empty turns & clean pipe streaming** — StreamingText starts its Live
  region lazily on the first visible character; non-interactive consoles skip Live
  snapshots entirely and paint the finished message exactly once (pipes used to show
  partial "Hi" fragments followed by the full text)
- [x] **Single echo** — LineReader now reports whether it rendered the input
  (`rendered_prompt`); the REPL echoes via `echo_user` only when the fallback input
  was used, killing the `› › message` double-glyph (fallback prompts went glyph-less)
- [x] **Help text survives rich markup** — /help printed with `markup=False` under a
  styled header; literal `[name]`, `[y]es / [n]o / [a]lways` brackets were being eaten
  as style tags (found by the piped smoke)
- [x] **Live-reported fixes (round 2)** — from a real session's paste:
  - **Spinner race**: stop→maybe_start churn around tool events reset the shared
    flag while a stale sleeper thread was mid-delay, so old+new spinners ran
    simultaneously → stacked `thinking… 0s` lines each restarting at zero. Rewritten
    with a lock + generation counter + per-cycle Event; a waking sleeper whose
    generation is stale exits without touching rich. Unit test drives real threads
    through the churn and asserts exactly one status (fails on the old code)
  - **Repeated tool calls de-noised**: models re-read files across agentic steps;
    duplicates of an identical call earlier in the same turn now render as a dim
    `↻ read(README.md)` instead of a full-weight `●` bullet (`tool_repeat`, seen_calls
    set in `_turn`)
  - **loguru off the chat**: default stderr sink printed raw timestamped warnings
    over the transcript, doubling every error the renderer already shows. run_repl
    redirects loguru to `~/.agent/agent.log` (1 MB rotation, keep 3); renderer output
    is now the single user-facing copy of any error
- [x] Verified offline by `verify_ui.py` (8 groups): banner abbreviation +
  middle-collapse + ≤80 cols, echo_user escaping, spinner gating under non-TTY,
  spinner race under real threads, status-line ctx % against real settings values,
  silent-empty-turn capture equality, single-paint pipe rendering, tool renderings
  (briefs, ↻ repeats, declined/error marks, stdout clipped at 12 lines, exit codes).
  verify_polish.py regression still green. Live Anthropic turn forcing three read
  calls showed ● → ↻ dedup, no raw log lines, and a clean one-line banner

---

## Hotfix — duplicated chat content & streaming repaints ✅ DONE 2026-08-23

The chat printed the same content several times per turn. Three stacked causes:

- [x] **User message sent twice to the LLM every turn** (`chat_service.py`, both
  `process_chat` and `stream_chat`): the prompt was persisted, the session
  re-fetched with `populate_existing` (history rebuild then already contained
  it), and the message was *also* appended by hand — every request carried the
  prompt twice, so the model echoed/printed the same context back. Fix: build +
  compact history from what is already persisted, then store the user message
  and append it exactly once; the redundant re-fetch is gone
- [x] **Segments replayed across a turn** (`renderer.StreamingText`): the REPL
  reuses one StreamingText for each text segment between tool calls, but
  `finish()` never reset its buffer — narration before a tool call was
  re-printed inside every later segment's final render. `finish()` is now
  segment-scoped (buffer reset) and idempotent
- [x] **Growing Live region repainted the whole document** (`renderer.py`):
  rich `Live` + growing `Markdown` with `vertical_overflow="visible"` re-prints
  the full text on every refresh; once output outgrew the terminal the overflow
  scrolled and the same paragraphs visibly stacked up (plus flicker on
  Windows). Streaming now shows a bounded dim tail preview (last 6 lines,
  `…` head marker, transient — leaves nothing behind) and `finish()` prints
  the complete rendered message exactly once, append-only
- [x] Cleanup: duplicate `_convert_tools` definition removed from
  `core/llm/anthropic.py`
- [x] Verified: new `verify_hotfix_chatdup.py` (provider records exactly one
  copy of each prompt — fresh + resumed, blocking + streaming);
  `verify_ui.py` +3 checks (no cross-segment replay, idempotent finish,
  bounded tail preview with newest-line-last); ask1/ask6/polish suites green;
  ruff clean; piped REPL boot smoke clean

**Files:** `services/chat_service.py`, `tui/renderer.py`, `core/llm/anthropic.py`,
`verify_hotfix_chatdup.py` (new), `verify_ui.py` (extended)

---

## Ask 11 — NEXUS: full-screen TUI console ✅ DONE 2026-08-24

A futuristic, event-driven Textual console over the existing backend. The UI
owns no agent logic: a thin `agent/` adapter layer translates the backend's
`stream_chat` dict events into immutable dataclass events, and `ui/` purely
reacts. The classic REPL stays the default; `coding-agent tui` launches NEXUS.

- [x] **agent/ adapter layer (headless, UI-free)**:
  - `agent/events.py` — frozen dataclasses: TurnStarted, SessionResolved,
    StreamDelta, ToolStarted(name,args,repeat), ToolFinished(result,ok,
    duration_s), Notice(level,text), TurnFinished(session,message,iterations,
    usage,context_tokens,elapsed_s,ttft_s), TurnFailed(kind,detail),
    TurnCancelled(elapsed_s)
  - `agent/controller.py` — AgentController drives ChatService.stream_chat,
    measures TTFT/turn duration/tool durations, dedupes repeated tool calls,
    translates every failure mode to a terminal event (TurnCancelled /
    TurnFailed(credits|too_long|provider|internal)); cooperative cancel
    (checked between events) + hard cancel of the worker task for hung
    streams; pending permission prompts resolve "no" on cancel
  - `agent/state_manager.py` — AgentStatus state machine + derive_status():
    [● ONLINE] [◉ ANALYZING/SEARCHING/READING/PLANNING/EXECUTING/EDITING]
    [✓ COMPLETE] [× ERROR]; tool names map to activity states
- [x] **ui/ package**: theme.py (nexus Theme: dark #060a12 base, cyan/violet
  neon accents), animations.py (braille/orbit frame cyclers, ▰▱ bars),
  metrics.py (TokenTracker usage/context/latency accounting; SystemMetrics
  psutil host sampling + one-shot cached nvidia-smi GPU probe)
- [x] **Layout (five regions)**: header (◆ NEXUS · model · connection glyph ·
  project · context bar · CPU/MEM · clock) | left PROJECT panel (.gitignore-
  aware lazy file tree, git branch + modified files, workspace stats) | center
  AI CORE (user/assistant blocks with throttled streaming markdown ~10fps,
  animated status pill, expandable tool cards ✓⟳× with diff/output detail) |
  right SYSTEM panel (context bar w/ compact-threshold coloring, tokens, last
  turn + TTFT, latency, tool count, API/MODEL, CPU/MEM/GPU) | bottom COMMAND
  CENTER (mode line, slash-command autocomplete dropdown, prompt with history,
  key hints). Panels auto-collapse on narrow terminals (F2/F3 toggle overrides)
- [x] **Interaction**: Enter send / Alt+Enter newline; Ctrl+C interrupt
  (cooperative+hard); Ctrl+L clear view; Ctrl+P searchable command palette;
  Tab cycles panels; ↑↓ history (JSON-persisted); Esc closes overlay/hides
  suggestions; `/` command mode; click/Enter expands tool cards
- [x] **Commands** (slash dropdown + palette + handlers): model, provider,
  permissions, context, tokens, files, search, run, git, diff, undo, commit
  (AI commit-msg via provider), compact, plan (read-only planning turn),
  memory, history (session picker + preview tail), export, rename, delete,
  tools, settings, clear, help, exit
- [x] **Permission flow**: risky calls push PermissionModal (y/a/n/Esc);
  preview via build_preview with DANGEROUS-pattern highlighting; wired through
  PermissionManager.prompt_fn so cancel() resolves a pending prompt
- [x] **CLI**: `coding-agent tui [--mode default|auto-edit|full-auto]
  [--continue] [--resume [prefix]]`; textual>=0.80 added to deps
- [x] Verified: new `verify_tui.py` headless Pilot harness — FakeChatService
  plays scripted scenarios through the REAL controller + permission gate and
  asserts 32 checks across 11 groups: boot/theme/focus, streamed turn (blocks,
  metrics 120/45, ctx 1650, session resolve, tool card success), approve flow
  (modal→y→COMPLETE), deny flow (modal→n→failed card), hard-interrupt of a
  hung stream→ONLINE, slash autocomplete (/mod→enter→"/model "), up-arrow
  history, ctrl+p palette, ctrl+l clear, diff overlay open/close, responsive
  collapse at width 70 / restore at 130 — all green; ruff clean on ui/, agent/,
  main.py, verify_tui.py; CLI smoke clean
- [x] Default model: `.env` DEFAULT_MODEL=stealth/ox-alpha (OpenRouter,
  already DEFAULT_PROVIDER); OpenAIProvider now takes via= so provider name
  "openrouter" always targets openrouter.ai/api/v1 regardless of key mix;
  live-probed: non-stream reply OK (reasoning model), streamed native
  tool-calling OK (tool_use_start fired end-to-end)
- [x] Fixes surfaced by the harness (first real execution of the UI):
  CommandDock composed no #hints row (aborted half of on_mount); nested
  AgentEventMsg was auto-namespaced by Textual so its handler never fired
  (moved to module level); StateBar used removed set_interval(first=) kwarg
  and leaked its animation timer after leaving active states; ToolCard could
  finish() before compose() ran AND re-add class "running" when finish beat
  on_mount (paint now deferred until mounted; mount won't resurrect running);
  derive_status referenced StreamDelta without importing it; host-sample timer
  crashed into a tearing-down DOM; /command dispatch passed arg as
  run_action's namespace (now direct action_cmd_* call)

**Files:** `agent/{__init__,events,controller,state_manager}.py`,
`ui/{__init__,app,theme,animations,metrics,commands}.py`,
`ui/widgets/{header,state_bar,ai_core,tool_card,project_panel,system_panel,
command_dock,modals}.py`, `main.py`, `pyproject.toml`, `requirements.txt`,
`verify_tui.py` (new)

---

## Hotfix — OpenRouter "Provider returned error" on every agent follow-up call ✅ DONE 2026-08-24

Symptom: the first LLM call of a turn worked, but every call that resent
history containing assistant tool_calls (iteration 2+, subagents, compactor
summaries) died with an opaque upstream 400 —
`'message': 'Provider returned error', 'provider_name': 'Stealth'`.

- [x] Root cause: `OpenAIProvider._convert_messages` passed our internal
      tool_calls dicts (`{id, name, arguments}`) through verbatim instead of
      reshaping to the OpenAI wire format
      (`{id, type: "function", function: {name, arguments-as-json-string}}`).
      First calls have no tool_calls yet, which is why only follow-ups broke.
- [x] Fix + empty assistant content omitted alongside tool_calls (some
      upstreams reject `content: ""` there too)
- [x] Any model usable: providers fetch their live catalog (`list_models()`,
      cached; static fallback offline) — 416 models on OpenRouter today.
      `/model <any-id>` was already free-form in both UIs; `/model` now lists
      the real catalog instead of a stale hardcoded trio
- [x] Credit-402 resilience: when OpenRouter says "...can only afford N",
      parse N and retry once with `max_tokens=N` (both complete and stream) —
      near-zero balances still complete short turns instead of hard-failing
- [x] Verified live end-to-end on stealth/ox-alpha: full loop (2 iters),
      streaming with tools, and parent→subagent delegation via the task tool;
      z-ai/glm-4.5-air completes turns under the adaptive credit retry;
      qwen3-coder blocked only by actual account balance (input-side credit
      floor). Offline regression: `verify_provider_fix.py`

**Files:** `core/llm/{openai,anthropic,base,factory}.py`,
`tui/app.py`, `ui/app.py`, `verify_provider_fix.py` (new)

---

## Ask 12 — Live token metering & the permissions console ✅ DONE 2026-08-24

Goal: see token usage move DURING a turn (not only at turn end), and give
permissions a real control surface instead of a one-line `/permissions` print.

Live metering pipeline:
- [x] `ChatService.stream_chat` yields a cumulative `usage` event after every
      LLM call (`message_done`), carrying totals + context estimate +
      iteration count — the UI no longer waits for `turn_done`
- [x] New `UsageUpdated` agent event (events.py) translated by the controller;
      `TokenTracker` now splits committed (finished turns) vs live
      (in-flight turn) accounting so mid-turn numbers are additive and honest
- [x] UI surfaces: SystemPanel TOKENS row shows `· turn ↑in ↓out` while busy,
      header context bar + label update per iteration, StateBar shows the live
      step counter and an elapsed-seconds ticker armed at turn start; LAST TURN
      row gains output tok/s

Permissions console (`/permissions` with no arg):
- [x] `PermissionManager` gains a decision history (last 60 records: verdict,
      reason, timestamp), `tool_policy()` describing what each tool does under
      the current mode, and `revoke()/revoke_all()` for session grants
- [x] `PermissionsOverlay` (Textual modal): keys `1/2/3` switch default /
      auto-edit / full-auto, `r` revokes session grants, plus the policy table,
      danger-list reference and recent-activity log; `/permissions <mode>`
      still sets the mode directly
- [x] **Critical fix found by verification:** Textual 8 changed `push_screen` —
      plain `await push_screen(s)` resolves None after MOUNT, not dismissal.
      Every approval/confirm dialog was silently auto-denying. Added
      `App._push_for_result` (callback→future wrapper) and migrated all four
      result-expecting call sites (permission gate, plan-execute, commit,
      delete-session); the y/n/a approval flow is genuinely blocking again
- [x] Verified headlessly: `verify_tui.py` extended to 44 checks including
      mid-flight token assertions (usage lands while the turn is blocked on
      the approval modal) and full overlay interaction (mode keys, revoke,
      activity log). All regression suites green; ruff clean

**Files:** `services/chat_service.py`, `agent/{events,controller}.py`,
`ui/metrics.py`, `ui/app.py`, `ui/commands.py`,
`ui/widgets/{modals,state_bar,system_panel}.py`, `core/permissions.py`,
`verify_tui.py`

---

## Hotfix — bash tool silently broken on Windows when WSL stub shadows Git Bash ✅ DONE 2026-08-24

Symptom: `bash` tool returned empty stdout / rc 1 for every command.
`shutil.which("bash")` resolves to `C:\Windows\System32\bash.exe` — the WSL
launcher stub — which exits with `execvpe(/bin/bash) failed` when no WSL distro
is installed. Real Git Bash (`C:\Program Files\Git\bin\bash.exe`) exists but its
`bin` directory is not on PATH, so `which` never finds it. Caught by the
regression battery (verify_ask1's real-bash step), not by user reports.

- [x] `_pick_shell()` now probes candidates once and caches: explicit
      `SHELL_EXECUTABLE` (trusted unprobed) → Program Files Git Bash ×2 →
      `which("bash")`. A candidate is used only if it answers a one-time
      `echo __shell_ok__` probe; otherwise fall back to COMSPEC shell as before
- [x] Verified: tool executes via real Git Bash again; verify_ask1 green

**Files:** `core/tools/shell_local.py`

---

## Ask 13 — Chat UI polish, project panel, session history ✅ DONE 2026-08-25

Goal: "optimize and improve chat UI and project section and add previous
session history to access them when the user wants." Three surfaces upgraded,
one new harness, two latent bugs fixed along the way.

- [x] **AiCore (chat)** — follow-mode is now honest: `_at_bottom()` re-checked
      per event, so scrolling up to read freezes the view and scrolling back
      down re-arms it. Stream repaints throttle adaptively with buffer size
      (markdown parsing is O(n)) and skip identical repaints. Each finished
      turn leaves a dim divider (`── 3 steps · ↑1,200 · ↓450 · 8.2s`).
- [x] **ProjectPanel** — subdirectories now populate lazily on expand (they
      used to open empty), empty dirs collapse shut instead of faking a
      chevron, entries are colored by suffix, file counts come from a pruned
      walk so `.venv`/`node_modules` never inflate them. Enter on a file opens
      a read-only syntax-highlighted preview; the new SESSIONS box shows four
      clickable recent sessions (current one marked) — click resumes.
- [x] **Session history** — ctrl+h or /history opens a richer picker: row
      numbers, relative ages (`5m`, `2h`, `Aug 24`), message counts, ● marker
      on the current session, `d` to delete (with confirm). Enter on a row
      resumes that session AND replays its recent transcript as real blocks:
      user lines, finished tool cards with ✓/× state, assistant markdown.
      Deleting the current session starts a fresh one automatically.
- [x] **f5** refreshes tree + git/stats + sessions in one stroke.
- [x] **Latent bug fixed:** `SessionsOverlay` never pre-highlighted an option,
      so Enter did nothing until you pressed an arrow key first. Found by the
      new harness; picker now highlights the newest row on open.
- [x] **Latent bug fixed:** `_restore_transcript` had a duplicated loop body
      (every message would have rendered twice). Removed.
- [x] **New harness `verify_tui_history.py`: 27 checks** — follow freeze/re-arm,
      tree pruning/lazy-expand/honest stats, seeded-DB picker flows (resume /
      delete-request / esc), transcript restore blocks, panel box markers,
      confirm-delete removes the DB row, file viewer open/close.
- [x] Regression battery green: verify_tui 44/44, verify_ui, verify_ask{1,4,
      5,6,7,8,9}, hotfixes, polish — all PASS.

**Files:** `ui/app.py`, `ui/widgets/ai_core.py`, `ui/widgets/project_panel.py`,
`ui/widgets/modals.py`, `ui/commands.py`, `verify_tui_history.py`

---

## Ask 14 — Terminal UI: single session surface, own-chat resume, dedup ✅ DONE 2026-08-25

Goal: "remove the session history [box]; a previous chat must open as its own
chat instead of continuing inside the current one; remove components repeating
multiple times."

- [x] **Inline SESSIONS box removed from the project panel.** Session history
  had two surfaces (panel box + ctrl+h picker) showing the same rows; the
  panel now shows only PROJECT/GIT/STATS and ctrl+h //history is the single
  way to reach previous chats (`ProjectPanel`: #psessions, apply_sessions,
  SessionLine, on_click dispatch all removed; app wiring gone)
- [x] **Own-chat resume**: `_adopt_session_row` clears the transcript FIRST,
  then restores that session's blocks — opening a previous chat replaces the
  view instead of appending under the current conversation. Guards: switching
  is refused while a turn runs ("ctrl+c interrupts before switching") and
  re-picking the open chat notes "already viewing this session" without
  duplicating anything. Per-chat state swaps too (checkpoints, token tracker,
  header ctx). Stale restore workers are dropped at render time via target-id
  check, so a slow load can never paint an abandoned chat into a new view
- [x] **Boot restore**: --continue / --resume now render the previous chat's
  transcript on mount (previously they adopted silently with an empty view)
- [x] **Restore header**: each opened chat opens with a dim divider naming it
  (── session <id8> - title - N message(s) ──, new `AiCore.system_divider`)
  replacing the trailing "restored from session history" meta line; hidden-
  older-messages note kept
- [x] **Dead/duplicated code removed**: unused `_load_preview_tail` /
  `_render_preview` ("recent turns:" leftovers), `refresh_sessions` worker +
  `_apply_recent_sessions` + TurnFinished/f5 refresh calls, stale HelpOverlay
  entry for the removed click-to-resume row, unused harness imports
- [x] Verified: `verify_tui_history.py` extended to 32 checks (7 new: clear-on-
  switch vs planted stale content, header divider present, re-open adds
  nothing, busy-switch refused, inline box absent, boot restore renders chat +
  adopts id); verify_tui 44/44; verify_ui/polish/hotfixes/ask1,4,5,6,7,8,9/
  provider suites all green; ruff clean

**Files:** `ui/app.py`, `ui/widgets/project_panel.py`, `ui/widgets/ai_core.py`
(`system_divider`), `ui/widgets/modals.py` (help keys), `verify_tui_history.py`,
`verify_tui.py` (unused imports)

---

## Ask 15 — Latency pass, ctrl+h/session-picker fix, bug sweep ✅ DONE 2026-08-25

Goal: "agent is taking more time to respond improve the latency and the
sessions are not showing when i use ctrl+h fix them and search for any bugs
if found then clear them."

- [x] **ctrl+h root-caused: physically unmappable on Windows consoles.** The
  Textual win32 driver feeds only `uChar.UnicodeChar` into the parser, so
  Ctrl+H arrives as `\x08`, which `_ansi_sequences.py` maps to Backspace —
  the binding can never fire there (DB was fine; 139 sessions intact).
  Fix: **f4 is now the reachable primary key** for the session picker
  (`Binding("f4", ...)` + ctrl+h kept for capable terminals); hint bar, help
  overlay and /history description all updated. Verified end-to-end via
  `verify_tui_history.py`
- [x] **Bare `--resume` crashed**: `app._load_sessions()` (a worker) ran
  before `App.run()`, when no message pump exists. The picker open is now
  deferred through a flag consumed in `on_mount`
- [x] **Latency — measured first**: raw provider TTFT is 3.5–6 s on
  stealth/ox-alpha (the floor we cannot touch). Our controllable cuts:
  - `build_system_prompt` (~321 ms/turn: workspace walk + git snapshot) moved
    off the UI loop with `asyncio.to_thread` in `AgentController.run`
  - Consecutive read-only tool calls now run concurrently via
    `asyncio.gather` (`_is_parallel_safe`: not in RISKY_TOOLS, no hooks);
    risky tools stay sequential so approval modals queue cleanly; all
    tool_start events emit first so card pairing/FIFO order is preserved
  - Per-tool-call SELECT eliminated: `_record_assistant_turn` returns a
    row_ids map (provider tool_use id → persisted ToolCall row) that
    `_update_tool_call_row` uses directly, with legacy fallback
- [x] **Bug sweep fixes**: OpenAIProvider had a duplicated dead `__init__`;
  removed, and the client now sets an explicit 300 s timeout (library default
  would hang a dead connection ~10 min; plain float avoids this env's
  httpx/httpx2 split)
- [x] Regression battery green: pytest, verify_ask1, verify_tui 44/44,
  verify_tui_history 32/32, verify_ui, verify_polish, verify_ask4–9,
  hotfix chatdup + provider_err, verify_provider_fix — all PASS; ruff clean
  on touched files

**Files:** `core/llm/openai.py`, `agent/controller.py`,
`services/chat_service.py`, `ui/app.py`, `ui/widgets/modals.py`,
`ui/commands.py`, `ui/widgets/project_panel.py`

---

## Ask 16 — TUI UX while working; slash commands fixed & tested ✅ DONE 2026-08-25

Goal: "optimize the TUI with better UX while agent editing and responding for
the prompt and all the commands are not working test the commands"

- [x] **Root cause of "commands not working": the dropdown was never wired.**
  `update_suggestions()` existed but nothing called it — typing `/` showed no
  menu and half-remembered completions failed silently. Fixed with
  `CommandDock.on_text_area_changed`; bare `/` now lists every command,
  prefix matches rank first, substring matches follow (`/ctx` → /context)
- [x] **Enter no longer swallowed by completion**: new
  `should_complete_on_enter()` — Enter completes only while the token is an
  incomplete prefix (`/mo` → `/model`); a full command name submits directly.
  (The wiring fix alone caused this regression; caught by the harness)
- [x] **Busy-state UX**: amber prompt border while a turn runs; hint bar swaps
  to "agent working - type + enter to queue · ctrl+c interrupt"; typed input
  during a turn is QUEUED (deque + QUEUE n in the mode line) instead of
  dropped or sent mid-turn — flushed on the next clean TurnFinished only,
  never on interrupt/failure (no surprise sends); plan-offer suppressed while
  a queue exists
- [x] **Streaming + tool UX**: AssistantBlock gets a cyan accent bar +
  trailing ▍ caret while streaming (forced repaint on finish so the caret
  never survives); ToolCard shows a braille spinner + live elapsed seconds
  while running (0.2s timer, self-stopping); git/project panel refreshes as
  write/edit/bash results land, not only at turn end
- [x] **ConfirmModal keyboard-first**: y/enter confirm, n cancels (bindings
  were missing; buttons still clickable)
- [x] **Tested all 24 commands end-to-end** with new `verify_commands.py`
  (49 checks): drives every command through the real path (load_text +
  enter → handle_slash) over a persisting fake ChatService; covers unknown
  command errors, usage errors, dropdown filtering/navigation, busy queueing,
  interrupt-keeps-queue, flush-on-next-finish, /exit, DB row assertions
- [x] Battery green: verify_commands 49/49, verify_tui 44/44,
  verify_tui_history 32/32, verify_ui, verify_polish, hotfix chatdup +
  provider_err, verify_ask1, verify_provider_fix — all PASS; ruff clean on
  touched files

**Files:** `ui/app.py`, `ui/widgets/command_dock.py`, `ui/widgets/ai_core.py`,
`ui/widgets/tool_card.py`, `ui/widgets/modals.py`, plus new
`verify_commands.py`

---

## Suggested order

```
Ask 0 → Ask 1 → Ask 2 → Ask 3   ← minimum viable Claude Code clone
→ Ask 4 → Ask 5                 ← safe & aware enough for daily use
→ Ask 6 → Ask 7 → Ask 8         ← polish: long sessions, resume, git
→ Ask 9                          ← advanced powers
```

After Ask 3 you will already have something that looks and feels like a mini Claude Code.
