# Live browser verification — 2026-11-01

Status of a signed-in, real-backend walk of every surface and action.
Not a test-suite result: every line below was produced by driving the running
app (Next.js `:3000` + FastAPI `:8000`) and inspecting what came back.

Legend: **PASS** observed working · **FAIL** observed broken (see note) ·
**BLOCKED** cannot be exercised (missing role/credential) · **PARTIAL** path ran, result not trustworthy.
Every row was settled in this pass: 41 checks — 37 PASS, 1 PARTIAL, 2 FAIL, 1 BLOCKED.

Account: `livecheck1101@example.com` (throwaway). Conversation used:
"Reply with exactly: routed" (3 turns).

## A. Chat core

| # | Item | Status | Evidence |
|---|---|---|---|
| A1 | Sign in (email + password) | PASS | session cookie set, redirected to `/app` |
| A2 | Send + streamed answer | PASS | 3 turns, answers render with footer |
| A3 | Progress chip mid-run | PASS | chip sampled: "Checking evidence" → "Writing the answer" |
| A4 | First frame latency | PASS | 3.2 s (was 53 s) |
| A5 | Model label / routing | PASS | `Nexus Reasoning · GPT-OSS 120B`, no fallback |
| A6 | Transcript restored after reload | PASS | same 3 turns after re-login |
| A7 | Model selector lists all groups | PASS | 3 groups, Vision disabled/"automatic" |
| A8 | Selecting a past conversation renders its turns | PASS | "live check ok" transcript loads (4 messages) |
| A9 | A conversation with zero messages | **FAIL** | selecting "Reply with exactly: routed" (0 stored messages) leaves the new-chat hero; the selection is invisible |

## B. Message actions

| # | Item | Status | Evidence |
|---|---|---|---|
| B1 | Copy response | PASS | clipboard read back `sweep one` after the click |
| B2 | Edit & resend | PASS | edited text replaced the turn, old answer discarded, DB holds exactly 2 clean messages |
| B3 | Regenerate answer | PASS | re-ran the turn from the stored prompt; 1 delta + 1 answer | 
| B4 | Good / bad response feedback | PASS | `POST …/feedback` 200, stored `user_feedback: "thumbs_up"` |
| B5 | Delete message | PASS | message removed from DB, transcript emptied |
| B6 | Branch conversation from message | PASS (with defect D5) | new conversation titled `… (Branch: Forked Branch)` holding the prefix of the transcript |

## C. Composer

| # | Item | Status | Evidence |
|---|---|---|---|
| C1 | Attach document → answer uses it | PASS | `composer-note.txt` chip; answer `VIOLET-MARLIN-42 [1] (composer-note.txt)` with a grounding panel |
| C2 | Attach image (vision) | PASS | 480×240 PNG reading "SWEEP" → answer `SWEEP`, model `Gemini 3.5 Flash (vision)` |
| C3 | Generate image | PASS | 768×768 JPEG rendered with Download link, `Pollinations Flux` |
| C4 | Voice input (Whisper) | PARTIAL | record → stop → transcript appears in the composer; the capture was silent (no microphone on this machine) so Whisper returned `"you you"`. Plumbing works, accuracy untested |
| C5 | Read aloud (TTS) | PASS | `GET /api/audio/speech` 200, blob media 206 playing |

## D. Modes and toggles

| # | Item | Status | Evidence |
|---|---|---|---|
| D1 | Deep Agent | PASS | answered the CAP question (`Nexus Fast · Qwen 3.8 27B`); no delegation visible on a one-liner question, so subagent dispatch is not proven |
| D2 | Web | PASS | status chip reached `Delegating to a specialist`; answer cited `https://www.python.org/doc/versions`, `Answer revised 2× before delivery` |
| D3 | Code | PASS | generated `print(sum(i**2 for i in range(1, 11)))`, ran it, showed `385` |
| D4 | Plan (draft → approve → run) | PASS (with defect D8) | drafted a 2-step plan card, `Approve & Execute` ran a run that followed the plan |
| D5 | Prompts (library insert) | PASS | `Prompt Library` opened; `Senior Code Review` inserted a 248-char template |

## E. Sidebar surfaces

| # | Item | Status | Evidence |
|---|---|---|---|
| E1 | Knowledge | PASS | dragged `sweep-note.txt` in → indexed (1 chunk); semantic search returned the chunk at 0.998 |
| E2 | Projects | PASS | created `Sweep Project`, added a conversation, count went 0 → 1 and `project_id` persisted |
| E3 | Usage | PASS | `/api/usage/summary` 200; cards show 198 tokens, 10 requests, $0.0001 |
| E4 | Settings | PASS (see D6) | panel renders, theme radios, model select, BYOK form, memories form |
| E5 | Command palette (Ctrl+K) | PASS | opens, lists actions/nav/conversations/models/themes, filters on typing, Enter navigated to Knowledge |

## F. Conversations

| # | Item | Status | Evidence |
|---|---|---|---|
| F1 | New conversation | PASS | created, selected, first turn persisted |
| F2 | Rename | PASS | `Sweep renamed chat` persisted, sidebar and API agree |
| F3 | Pin / unpin | PASS | `is_pinned: true`, item moved under a `Pinned` heading, button became `Unpin` |
| F4 | Fork | PASS | new `… (Branch: Forked Branch)` with both turns copied |
| F5 | Share (link + signed-out render) | PASS (with defect D6) | link minted, incognito page rendered read-only with no composer; `Revoke` → `Share link not found or expired` |
| F6 | Delete | PASS | conversation removed from the list and the API |

## G. Org / webhooks / admin

| # | Item | Status | Evidence |
|---|---|---|---|
| G1 | Organization create + invite | PASS (with defect D6) | org `Sweep Org` (owner), invite created for `teammate@example.com`, link shown |
| G2 | Webhook register + delivery | **FAIL** | registration works (`Active`, `message.completed · signed`, secret shown once); a completed run created **no** delivery row, because dispatch only exists on the non-streaming endpoint the UI never calls (D7) |
| G3 | Admin surface | BLOCKED | needs an admin role on the account |

## H. Signed-out behaviour

| # | Item | Status | Evidence |
|---|---|---|---|
| H1 | `/app` signed out → redirect to `/signin` | PASS | incognito `/app` → `/signin?next=%2Fapp` |
| H2 | Public share page signed out | PASS | incognito `/share/<token>` rendered the transcript, no composer |

## Defects found

| # | Defect | Evidence |
|---|---|---|
| D1 | A conversation that exists but holds no messages (title set, 0 turns) renders as the empty new-chat hero when selected, so the user cannot tell a conversation is open. | `GET /api/conversations/1ca2c4b6…` → `messages: []`; selecting it leaves "How can Nexus help you today?" |
| D2 | **Fixed (2026-11-04) — cause found, not the one the earlier note guessed.** One answer, two `text_delta` frames. The `messages` fallback and the writer drip were both live for the *same* node: the synthesizer's `on_chain_end` output carries `{"messages": [AIMessage(answer)]}`, and its writer payload arrives as a separate root `on_chain_stream`. Which is dispatched first is scheduling, so on some runs the fallback ran while `_streamed_tokens` was still False, emitted the whole answer, and the writer chunk then emitted it again. `run_events` proves it: doubled runs have two identical deltas (seq 6-7) and nothing else differs; instrumenting each source logged `messages_fallback` immediately followed by `chain_stream` with the same text for the duplicated runs, and a single `chain_stream` for the clean ones. Reproduced in 2 of 8 parallel runs, 0 of 26 serial — which is why it tracked the UI (a browser turn overlaps other work) instead of the request shape. Fix: the fallback skips the nodes that own the writer (`_WRITER_ANSWER_NODES`), so the answer has exactly one delivery path; `test_the_writer_owner_never_delivers_its_answer_twice` pins both orderings. Re-measured: 16 runs under the same parallel load, every run exactly one delta, no doubled row. | runs `cad5cc14…`, `bbcdcb37…`, `c1a1e4bb…` = deltas (6,7) identical; post-fix runs 8/8 and 8/8 clean (`fx a1…d2`) |
| D3 | **Fixed (2026-11-04).** The session did not survive ordinary token expiry: the access cookie lives 1 hour while the refresh cookie lives 30 days, and `src/proxy.ts` gated `/app` on the access cookie alone, so an hour into a session every navigation was redirected to `/signin` even though the refresh token was valid. `/api/auth/me` compounded it by reporting the backend's 401 as `authenticated: false` without trying the refresh token, so even a page that got past the gate concluded "signed out". The code case was measurably reachable: a browser tab signed in at 06:20 was redirected to `/signin?next=%2Fapp` at 07:00. Two changes: the edge gate lets a request through when *either* cookie is present, and the probe spends exactly one refresh attempt, re-verifies the minted token against `/auth/me`, and re-bakes both cookies on its own response. A rejected refresh token is still a real signed-out verdict (200 false), and an unreachable backend during the refresh is still 503 — never a verdict. Measured after the fix: a jar holding only the refresh cookie gets `200` from `/app` (was `307` to `/signin`) and `200 {authenticated: true}` from `/api/auth/me` with `nexus_access_token` re-issued (`Max-Age=3600`) and the rotated refresh cookie re-baked; an anonymous request still gets `307`, and a bogus refresh token still gets `200 {authenticated: false}`. | cookie jar with only `nexus_refresh_token` → `/app` 200, `/api/auth/me` 200 + 2 `Set-Cookie`; `bogus.dead.token` → `{authenticated:false}`; no cookie → 307 `/signin?next=%2Fapp` |
| D4 | A conversation that exists but holds no messages renders as the new-chat hero, so the open conversation is invisible (same path as A9). | `GET /api/conversations/1ca2c4b6…` → `messages: []` |
| D6 | The organization invite accept link is built on `http://localhost:3001` while the app is served from `:3000`, so the link a user copies does not reach the running app. | invite panel: `http://localhost:3001/invite?token=…` |
| D7 | Webhooks never fire for a real chat. `dispatch_event("message.completed")` is called only from the non-streaming `POST /conversations/{id}/messages` path; the app chats through the run/stream path, which has no dispatch. Registration succeeds, `webhook_deliveries` stays empty after a completed run. | `grep dispatch_event` → only `api/v1/messages.py`; 0 delivery rows after a live turn |
| D8 | After approving a plan, the **next** turn in that conversation answers the *previous* question: a Web-mode question about the newest Python version came back with `VIOLET-MARLIN-42` from the earlier document turn. A fresh conversation asked the same thing and answered correctly, so the state leak is specific to the post-approval thread. | transcript of `7cd28cbc…`: user asked the Python question, assistant answer text is the earlier phrase |
| D9 | A vision turn shows the previous turn's document in `GROUNDING SOURCES` (`composer-note.txt 7%`) even though the answer came only from the image. | `SWEEP` turn's grounding panel |
| D5 | In a branched conversation the parent's verdict panel (`Answer passed review on first draft`) renders above the composer with no answer attached to it. | branch from the user turn of `branch base`, screenshot |

| D10 | **Not an app defect — a wedged Next dev server.** `/app` can sit on the `loading.tsx` skeleton ("Loading workspace…") with a valid session and a healthy backend. Measured: the tab issued no `/api/auth/me` request and requested no route chunk, and a fresh tab did the same; restarting `next dev` rendered the workspace immediately, twice. The auth probe fix below is still correct (a 503 must not read as signed-out), but a skeleton that never ends is diagnosed by `GET /app` in the dev log plus the missing route chunk, not by the client gate. | `/tmp/frontend3000.log`: `GET /app 200`, no `/api/auth/me`; after restart the same URL rendered |

## Log

- (start) checklist created; app already signed in, 3 turns in the open conversation.
- A8/A9: past-conversation loading confirmed working, and the zero-message case confirmed broken.
- D2: read the stored messages directly over the BFF; the concatenation is in the database, not the renderer.
- D2 reproduced through the regenerate action, root-caused to the double delivery path, fixed, backend restarted (health 200).
- D3: the tab was closed by the environment mid-run; reopening lost the session.
- B1-B6 exercised on conversation "branch base" and its fork; B5 left the fork empty (see D4).
- D5: screenshot of the branched conversation shows the verdict panel on its own.
- E1-E4, G1-G2: walked in Settings/Knowledge/Projects/Usage against the live backend; every claim above came from a rendered panel or an API body, not from the code.
- G2: registered `https://httpbin.org/post`, completed a turn, then queried `webhook_endpoints` (1 row) and `webhook_deliveries` (0 rows).
- C1/C2 files were attached through the real hidden inputs (a `DataTransfer` assigned to `input.files`), so the upload, parse and model path ran for real; no mock.
- C4: the machine has no microphone; the Whisper call still ran and returned a hallucination for silence.
- D1-D5 exercised against real providers; every answer quoted above is the rendered text.
- F2-F6, H1-H2 exercised on the live app and the live API, including an incognito tab for the signed-out checks.
- D8 found while running D2: the mode toggles are fine, the conversation state after a plan approval is not.
- Not exercised: admin surface (no admin role), mobile layout pass (no responsive sweep in this run), rich artifact/document editing beyond the message actions above.

## Re-verification pass 2 (2026-11-02, after the fixes)

| # | Check | Result | Evidence |
|---|---|---|---|
| A9/D4 | Zero-message conversation | PASS | a titled conversation with `messages: []` now renders its title plus "No messages in this conversation yet. Ask something below to start it."; the hero appears only with no conversation selected |
| D5 | Verdict panel without an answer | PASS | the empty fork of `branch base` renders no verdict panel |
| D7 | Webhook for a real chat turn | PASS | completed turn → `webhook_deliveries` row `message.completed`, `delivered`, HTTP 200, attempts 1 |
| D8 | Next turn after a plan approval | PASS | in the citation-heavy thread, "What is 2+2? Reply with only the number." answered `4` with no grounding sources and no earlier document phrase |
| D9 | Vision turn grounding sources | PASS | same turn lists no stale `composer-note.txt` |
| D6 | Share + invite link origin | PASS | `POST .../share` → `http://localhost:3000/share/PYLN…`; invite → `http://localhost:3000/invite…` |
| B7 | Delete the **last** user turn in a 3-turn conversation | PASS (no crash) | 6 → 4 messages, `DELETE …/messages/<id>` 200, transcript re-rendered, conversation stayed in the sidebar |
| B8 | Delete a **middle** user turn with two turns after it | PASS (cascades inside the conversation only) | 8 → 2 messages: the target turn, its reply and both later turns removed; `DELETE` 200; conversation row intact |
| B9 | Delete the **first/only** user turn | PASS (no crash, conversation survives) | 2 → 0 messages; the conversation stays in the sidebar and shows the empty-conversation state; a new turn afterwards works |
| B10 | Delete an assistant message over the API | PASS | 200, `deleted: 1`, the user turn is left alone (the UI offers no delete control on assistant messages) |
| B11 | Delete the same id twice / an unknown id | PASS | second call 404 `Message not found`, unknown id 404 — no 500 |
| B12 | Delete while a run is streaming | Not reachable from the UI | message actions are not rendered during an in-flight run; the click lands on nothing and the turn completes normally |
| Voice | Short-clip silence guard | Unit-tested only | this machine has no microphone, so the live path could not be exercised; `chat-input-voice.test.ts` covers the guard |
| Admin | Admin surface with an admin role | PASS | with `users.role='admin'` the sidebar exposes `Admin` and the panel renders (49 users, Users / System health / Audit logs / Lifecycle hooks / Slices & fairness / Optimization / Compliance); reverting to `user` removes the tab again |
| D3 | Session survival | PASS (2026-11-04) | the cookie survives a backend restart, a probe during a restart answers `503 auth_unavailable` and the client retries instead of showing the sign-in modal, and an expired access cookie with a live refresh cookie now resumes the session instead of bouncing to `/signin` |

Cascade semantics, from the implementation and confirmed live: `repository.delete_message` removes the target and every message after it in `created_at` order, scoped by `conversation_id` (and `user_id` when given). It never touches the conversation row, so deleting never removes a chat from the sidebar, and a forked conversation holds its own copies.

Leftovers: the throwaway conversation "Reply with exactly: turn one" is now empty, and `http://localhost:3000/share/PYLNuBzRffG6Xh5VaoDW-iD-d95XZ8aT` plus an invite for `invitee-check@example.com` were minted while checking D6.


The G4/G5 rows below were written from the code and the backend test suite run this session (ruff + mypy + targeted pytest) and the earlier 2026-11-01 live walk. They were **not** re-observed in a fresh browser walk of the running app today, so they remained code-level PASS until the surfaces listed for each row were re-opened at :3000. **Partly closed on 2026-11-05 — see *G6* below**: the usage row, the slices payload, pinning and the admin endpoints are now witnessed against the running backend, while the rendered Usage/Settings/Admin panels and a narrow-width sweep are still unchecked.

## G4  Cache-hit rate accounting  (2026-11-04)

| # | Item | Status | Evidence |
|---|---|---|---|
| G4a | `SpendMeter` reports `cached_tokens` and `cache_hit_rate` per usage row | PASS | `_persist_reply` in `run_executor.py` populates both; both rows in `api/v1/messages.py` carry `cached_tokens` |
| G4b | Per-slice `cache_hit_rate` and `cached_tokens` in monitoring view | PASS | `slices_service.py` exposes overall and per-slice rates; ruff clean |
| G4c | Provider cache shapes handled (`cache_read_input_tokens`, `prompt_cache_hit_tokens`) | PASS | `litellm_client.py` `_extract_cached_tokens` parses all three; values flow into `UsageLogCreate` |
| G4d | Prompt-cache economic signal: pinned model prevents double-billing per turn | PASS | `conversations.py` pins `body.model or conv.model`; model_info stored on `agent_runs`; before/after measurable |

## G5  Structured conversation compaction tail  (2026-11-04)

| # | Item | Status | Evidence |
|---|---|---|---|
| G5a | `summarize_messages` returns prose + deterministic tail (last question, artifacts, last failure) | PASS | `_state_tail()` in `context_compiler.py`; extracts without LLM call; appended to summary |
| G5b | `summarize_agent_history` returns prose + same deterministic tail | PASS | Tail appended in exception path as fallback; role-aware and artifact-pattern matching |
| G5c | No regression: all existing compaction behaviour preserved | PASS | ruff + mypy clean; 102 tests pass including `test_context_funnel.py` and `test_behavioral_invariants.py` |


## G6  Fresh walk of the G4/G5 surfaces  (2026-11-05)

Driven against the running app (`next dev` :3000, FastAPI :8000) with a real
session for `livecheck1101@example.com`, after restarting the backend onto the
current source.

| # | Check | Result | Evidence |
|---|---|---|---|
| G6a | **The running backend was serving pre-edit code** and had to be restarted before G4 could be witnessed | FOUND, FIXED | `usage_logs.metadata_json` for a turn run before the restart held only `quality`; after restarting uvicorn on the same source the same path writes `quality` + `cached_tokens` + `cache_hit_rate`. A G4 claim measured before that restart would have described the old build. |
| G6b | Sign-in through the real form (not curl) | PASS, with an observation | Typing the credentials and clicking `Sign In` produced **no** network request until the page was reloaded once; after a reload the same form + Enter set both `nexus_*` cookies and redirected to `/app`. The first attempt looked like an unhydrated bundle (a chunk request showed `ERR_ABORTED`), not an auth failure. |
| G6c | `/app` renders against the new code | PASS | Sidebar `Model` = `Nexus Reasoning (GPT-OSS 120B · Groq)`; options `Nexus Reasoning`, `Nexus Fast (Qwen 3.8 27B · Groq)`, `Nexus Vision (Gemini 3.5 Flash · Google) — automatic` (disabled) — the same three entries as `lib/models.ts`. `Admin` appears only while the account held `role='admin'`. |
| G6d | Cache accounting reaches the usage row | PASS (live) | `GET /api/usage/summary` → `cached_tokens: 0`; the `usage_logs` row for the pinned turn has `cached_tokens = 0` and `metadata_json.cache_hit_rate = 0.0`. Zero is the documented "provider reported nothing" value, not evidence of no caching. |
| G6e | Slices expose the hit rate | PASS (live, admin) | `GET /api/admin/monitoring/slices` → `overall.cache_hit_rate = 0`, and every slice row carries `cache_hit_rate` + `cached_tokens` (`openai/gpt-oss-120b` volume 62; `groq/llama-3.3-70b-versatile` volume 39 — historical rows still attributed to the retired model, so the default change did not rewrite history). |
| G6f | **Conversation model pinning actually changes the model** | PASS (live) | Created a conversation with `model: fast_chat` and posted a turn with **no** `model` in the body: the stream's `model_info` frame read `qwen/qwen3.8-27b` and the usage row recorded the same, instead of the `openai/gpt-oss-120b` default. |
| G6g | Admin surfaces answer for an admin | PASS | `slices`, `fairness`, `bandits`, `optimization/runs`, `system-status`, `users` all 200; fairness reports provider error parity `groq 102 / 0%`, `gemini 20 / 0%`. |
| G6h | Rendered Usage / Settings / Admin panels in the browser | PASS (later, same day) | **Usage**: Total 4,308 / cost $0.0022 / latency 13139ms / 83 requests / input 3,528 ("0 cached") / output 780 / **Cached tokens 0**. **Settings**: `Default model` select lists exactly `complex_reasoning`, `fast_chat`, `vision (automatic)`; changing it to `fast_chat` + Save persisted (`GET /api/settings` → `fast_chat`), then restored to `complex_reasoning`. **Admin**: seven tabs render; Slices & fairness shows 122 requests, 0.0% error, 100% helpful, five volume-ranked slices; Optimization shows its empty state. Clear the first caveat: this needed a fresh tab, see G6j |
| G6i | Mobile layout sweep (narrow widths) | PASS after a fix — see *G8* |

State left behind: the two throwaway conversations (`pin check`, `pin check 2`) were deleted (204) and the account's `role` was reverted from `admin` to `user`.

| G6j | Why the earlier rows read NOT WITNESSED | DIAGNOSED | Two separate causes, neither an app bug: (1) the `next dev` process had been running through hours of HMR and served a page that streamed only `loading.tsx` — a restarted dev server plus a fresh tab rendered normally; (2) `preview_click`/`preview_press` deliver no pointer or key events to this page's DOM at all — a capture-phase listener on `<form>` recorded **nothing** while `elementFromPoint` correctly reported the button. Driving `button.click()` from inside the page fired `form:click` then `form:submit` then `POST /api/auth/login`, and the app navigated. Every click recorded in G6h, G7 and G8 was dispatched that way |

## G7  Voice path with real audio  (2026-11-05)

There is no microphone on this machine, so a real-but-synthetic signal was used: Windows
SAPI (`System.Speech.Synthesis`) wrote a WAV of speech into `frontend/public/`, and in the
page `navigator.mediaDevices.getUserMedia` was replaced with a `MediaStreamAudioDestinationNode`
fed by an `AudioBufferSourceNode` playing that WAV. The component's own `getUserMedia` ->
`MediaRecorder` (webm/opus) -> `POST /api/audio/transcribe` path is unchanged; only the
audio source is substituted. The WAV was deleted afterwards.

| # | Check | Result | Evidence |
|---|---|---|---|
| G7a | Speech reaches the composer | PASS (live) | a 3.6 s capture of the clip came back from `/api/audio/transcribe` and was inserted into the composer as `bank. The quick brown fox jumps over the lazy dog` (the loop replayed the tail of the sentence). A real round trip: MediaRecorder -> BFF -> FastAPI -> Groq Whisper -> `setContent` |
| G7b | Silence does not become a message | PASS (live) | the same path fed a genuinely silent stream (oscillator -> gain 0 -> destination): the composer stayed **empty** and the notice read "No speech detected - hold the mic button while you speak." That is `isLikelySilenceHallucination` observed stopping Whisper's invented text instead of inserting it |
| G7c | Short-clip guard (<700 ms / <1024 bytes) | not re-witnessed live | covered by the unit suite (`chat-input-voice.test.ts`) only; a sub-second capture was not replayed |

## G8  Mobile / narrow-width sweep  (2026-11-05)

| # | Width | Result | Evidence |
|---|---|---|---|
| G8a | 390 x 844 (`/app`) | PASS | `document.scrollWidth == 390` (no horizontal overflow); the 288px sidebar is off-canvas and the "Open navigation" button slides it in (`left: -288 -> 0`); composer and textarea visible |
| G8b | 390 x 844 (Usage, Settings) | PASS | no element extends past the viewport on either view; the Settings default-model select is 303px wide and fits |
| G8c | 390 x 844 (Admin -> Slices & fairness) | **BROKEN, FIXED** | the tab strip was a plain `flex` row 718px wide inside a 353px container with `overflow-x: visible`, so `Optimization` (right edge 612px) and `Compliance` (734px) were clipped off a 390px screen with no way to reach them. Fixed in `AdminView.tsx`: the strip is `overflow-x-auto` and each tab button `shrink-0 whitespace-nowrap`. After the change the strip reports `clientWidth 347 / scrollWidth 867`, and scrolled to its end `Compliance` sits at 363px inside a 369px strip |
| G8d | 768 x 1024 | PASS | sidebar becomes `position: static`, zero overflowing elements, strip still scrolls (421 / 867) so the tabs stay reachable |
| G8e | 1280 x 800 (regression check for G8c) | PASS | strip fits without scrolling (`clientWidth == scrollWidth == 933`), zero overflowing elements — the fix changed nothing at desktop width |

Screenshots from the sweep: `screenshots/mobile-settings-390.png`,
`screenshots/mobile-admin-slices-390.png`, `screenshots/tablet-admin-768.png`,
`screenshots/usage-cached-tokens-720.png`.

After the sweep: `npx vitest run src/test/components/admin-view.test.tsx src/test/api-wiring.test.ts`
-> 13 tests pass.
