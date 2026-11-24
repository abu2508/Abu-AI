# Abu AI mobile: screen specifications and endpoint mapping (plan)

Conventions
- Every screen defines: purpose, content, actions, states (loading / empty / error / offline / kill-switch), accessibility, endpoints.
- Endpoints are real (M1-M7). **GAP** = no backend support; the screen is degraded or deferred (see MILESTONE_8_PLAN section 4).
- Common states: `401` -> silent refresh then retry, else the session-expired flow; `503 kill_switch_active` -> the
  emergency-stop banner (agent features only); `429` -> "slow down" with retry; offline -> banner + stale data if in memory.
- Money values are strings. All destructive actions confirm. All lists are in-memory cached only.
- "Domain accents": Finance, Messaging, Coach each have an identity colour/glyph (DESIGN_SYSTEM_PLAN).

## 1. Launch and onboarding

| Screen | Purpose / content | Actions | Endpoints |
|---|---|---|---|
| Splash | Abu mark with a single settle animation; resolves session state | none | `GET /health` (liveness, to choose "offline" vs "enroll") |
| Welcome | 3 short cards: what Abu does, privacy stance, "synthetic data only" notice | Continue | none |
| Enroll | Enter or scan the operator-issued code; device name (default from device) | Enroll | `POST /auth/enroll` (errors: invalid/expired code, 429) ; QR scan is client-side (**GAP G4**: code issued via operator CLI only) |
| Security setup | Optional Face ID/passcode lock, privacy blur explained | Enable / Skip | none (device) |
| Appearance | System (default) / Light / Dark with live preview | Choose | none (device; **G11**) |
| Lock | Full-screen unlock | Biometric / passcode | none |
| Session expired | Explains and returns to Enroll | Re-enroll | none |

## 2. Home (tab)

Purpose: a calm, uncluttered "today" surface. Cards are independent queries (G3); one failing card shows its own error.

| Card | Content | Actions | Endpoints |
|---|---|---|---|
| Abu status | Orb state + one line ("All systems normal" / "Emergency stop is on") and a mock-model/synthetic chip | Open assistant | `GET /governance/status` |
| Today (Coach) | Next event, top 3 planned tasks, habits still open | Open Coach | `GET /coach/briefing` |
| Money snapshot | Month income/spend/net, savings rate, pending-review count | Open Finance | `GET /finance/summary`, `GET /finance/upcoming` |
| Insights | Top 3 insights (severity-ordered) | Open Finance > Insights | `GET /finance/insights` |
| Needs reply | Shared conversations awaiting a reply | Open conversation | `GET /messaging/conversations` + per-row `GET .../analysis` (**G5**) |
| Reminders due | Due reminders | Ack / dismiss | `GET /coach/reminders/due`, `POST /coach/reminders/{id}/{ack|dismiss}` |
| Quick actions | Ask Abu, Add expense, Log habit, Import chat | Navigate | none |
| Avatar | Opens Settings | | `GET /me` |

Empty states teach (e.g. "No accounts yet: add one"). Accessibility: cards are headings with ordered reading; orb is
decorative with a text status.

## 3. Assistant ("Ask Abu", modal)

Not a chat transcript: a command surface that returns **structured result cards**.

- Input (text; voice via native speech in P8), suggestion chips derived from `GET /orchestrator/workflows`.
- Messaging requests offer an optional conversation picker (`conversation_id`).
- Submit generates an `Idempotency-Key`; the orb shows `thinking` for the request lifetime (**G2**: no step events;
  indicator is honest, no fake step names). Cancel only aborts the client wait (the run may still complete; the run is
  visible in history).
- Result rendering by `status`:
  - `completed`: `summary` (templated, deterministic) + typed cards from `data` keys (e.g. budget bars, plan timeline,
    draft options, loan table); a small "mock model" chip; `reply` shown as secondary text.
  - `clarification_needed`: the question with quick-reply chips; `unsupported`: static refusal (never offers payment/sending).
  - memory proposals: inline "Abu suggests remembering this" card -> Approve / Edit / Reject (never auto-approved).
  - warnings (`memory_proposals_truncated`, ...) shown subtly.
- Errors: 503 kill switch (emergency-stop banner), 429/limit, 504 timeout (retry with same key), 403 permission (explain agent not permitted), 409 idempotency conflict.
- History sheet: `GET /orchestrator/runs`, `GET /orchestrator/runs/{id}` (reply available only while its encrypted copy has not expired; show "expired").
- Endpoints: `POST /orchestrator/run`, `GET /orchestrator/workflows`, `GET /orchestrator/runs[/{id}]`, memory approve/reject below.

## 4. Finance

### 4.1 Overview (tab root)
Month picker; summary (income, spend, net, savings rate), category breakdown (ring + list), cash-flow area, budgets
status bars, card utilisation, insights, upcoming dues, **review-queue banner** ("3 alerts need review", unreviewed items
are *not* counted in numbers; the screen says so).
Endpoints: `GET /finance/summary`, `/cashflow`, `/budgets`, `/cards`, `/insights`, `/upcoming`, `/review` (count). Params `year`, `month`.

### 4.2 Transactions
List (grouped by day, filter by status/category/account/date, paged via `limit/offset`), detail (amount, direction,
category, status, source, confidence, reconciled flag, duplicate link). Actions: change category (optionally "remember
this merchant"), reject, delete. **Edit amount/date/description: GAP G6** (read-only; delete and re-add).
Endpoints: `GET /finance/transactions`, `POST .../{id}/category`, `.../reject`, `DELETE .../{id}`, `GET /finance/categories`, `GET /finance/accounts`.

### 4.3 Add transaction
Manual form (account, date, amount, direction, description, category) and voice entry (transcript from on-device speech,
P8; typed fallback). Voice results always land in the review queue. Duplicate outcomes (`duplicate`, `duplicate_suspect`)
are explained.
Endpoints: `POST /finance/transactions`, `POST /finance/transactions/voice`.

### 4.4 Review queue
Two sections: pending alerts/voice entries and suspected duplicates (with the original shown side by side).
Actions: confirm (optional category), reject, bulk "confirm high-confidence" (>= 0.75, server-enforced).
Endpoints: `GET /finance/review`, `POST .../{id}/confirm`, `POST .../{id}/reject`, `POST /finance/transactions/confirm-pending`.

### 4.5 Import statement
Document picker (CSV/XLSX/PDF), size check client-side (<= 3 MB), read into memory -> base64, account picker, result
summary (parsed/imported/duplicates/reconciled/rejected + reject reasons). The file is not copied or kept.
Errors: 409 already imported, 413 too large, 422 unreadable/unsupported.
Endpoints: `POST /finance/import`.

### 4.6 Alerts ("Ingest")
Paste or share a bank SMS/email text -> parse result (inserted/duplicate/unparsed/needs_account). Explains plainly that
iOS cannot read other apps' notifications and shows the Shortcut/Share set-up guide (P8 enables handoff).
Endpoints: `POST /finance/ingest`.

### 4.7 Accounts
List, create (kind, label, institution, last4, limit/statement/due days), toggle `auto_confirm` (with a risk explainer),
archive. **Account delete/edit beyond these: GAP G6.**
Endpoints: `GET/POST /finance/accounts`, `PATCH /finance/accounts/{id}`.

### 4.8 Budgets
List with status, set/update/delete a monthly limit per category or overall (`*`).
Endpoints: `GET/PUT /finance/budgets`, `DELETE /finance/budgets/{category}`.

### 4.9 Loans and EMIs
List (outstanding principal, next EMI/due), detail with amortisation schedule, add loan. **Edit/delete: GAP G6.**
Endpoints: `GET/POST /finance/loans`, `GET /finance/loans/{id}/schedule`.

### 4.10 Subscriptions
Active list, suggested (detected) list, add, pause/cancel/resume. (Detected items are suggestions: "Add as subscription"
uses `POST /finance/subscriptions`; there is no server-side promotion endpoint.)
Endpoints: `GET/POST /finance/subscriptions`, `POST /finance/subscriptions/{id}/{pause|cancel|resume}`.

## 5. Messaging

### 5.1 Inbox
Imported conversations: platform glyph, title, message count, last message time, shared-with-Abu badge, needs-reply
badge (lazy per row, **G5**). Empty state explains import-only and that nothing is ever sent.
Endpoints: `GET /messaging/conversations` (+ `.../analysis`).

### 5.2 Conversation
Messages (decrypted for the owner, paged), analysis strip (needs reply, response times, last intent, tone), controls:
share-with-assistant toggle, tone preference, retention days, link contact, delete conversation. "Append more messages".
Instruction-like messages are marked as data.
Endpoints: `GET /messaging/conversations/{id}`, `/messages`, `/analysis`, `PATCH ...`, `DELETE ...`, `POST .../messages`.

### 5.3 Reply suggestions (sheet)
Tone selector; drafts as cards labelled **Draft - not sent**; actions: Copy, Edit then Copy, "Open <platform>" (deep link to
the other app, no prefill). There is **no send action** and the test suite asserts no messaging write other than import/append/patch/delete.
Endpoints: `GET /messaging/conversations/{id}/suggestions?tone=`.

### 5.4 Import conversation
Source choices: paste text (labelled or WhatsApp-style export), pick an export file, (P8) receive from the Share Extension.
Fields: platform, my name (for exports), title, contact, share toggle, retention (1-365), default tone. Result: imported/duplicates.
Endpoints: `POST /messaging/conversations/import` (<= ~1 MB), `POST /messaging/conversations/{id}/messages` (append).

### 5.5 Contacts
List, create, edit (name, relationship, notes), delete.
Endpoints: `GET/POST /messaging/contacts`, `PATCH/DELETE /messaging/contacts/{id}`.

## 6. Coach

### 6.1 Today
Time-blocked plan (events + planned tasks + overload notice + unscheduled/deferred), habits row, reminders, morning
check-in prompt, evening review entry. Date switcher (plan for another day).
Endpoints: `GET /coach/plan?day=`, `GET /coach/briefing`, `GET /coach/habits`.

### 6.2 Tasks
List by status, create (title, due, priority 1-4, estimate 5-480, goal), edit, complete, snooze (future date), delete.
Endpoints: `GET/POST /coach/tasks`, `PATCH /coach/tasks/{id}`, `POST .../complete`, `POST .../snooze`, `DELETE /coach/tasks/{id}`.

### 6.3 Habits
List with streak and 30-day rate, create (cadence, weekly target), log today or back-fill (<= 60 days), undo, delete.
Endpoints: `GET/POST /coach/habits`, `POST /coach/habits/{id}/log`, `DELETE /coach/habits/{id}`.

### 6.4 Goals
List with on-track/at-risk/overdue, create, update progress/status/target, delete.
Endpoints: `GET/POST /coach/goals`, `PATCH /coach/goals/{id}`, `DELETE /coach/goals/{id}`.

### 6.5 Calendar
Agenda (range up to 92 days), add event (timezone-aware, optional reminder minutes), delete, **import .ics**
(document picker -> base64, <= ~450 KB; recurring events are skipped and counted). EventKit sync (P8) produces an ICS
payload client-side and uses the same endpoint. **Edit event: GAP G6.**
Endpoints: `GET/POST /coach/events`, `POST /coach/calendar/import`, `DELETE /coach/events/{id}`.

### 6.6 Reminders
Due list, create, ack, dismiss. The app schedules **local** notifications for upcoming reminders it has fetched (G1).
**Reminder delete: GAP G6** (dismiss only).
Endpoints: `POST /coach/reminders`, `GET /coach/reminders/due`, `POST /coach/reminders/{id}/{ack|dismiss}`.

### 6.6b Check-in, Evening review, Progress
Mood/energy (1-5) + optional note; once per kind per day (409 handled). Review: done today, carry-over, habits, streaks,
tomorrow preview. Progress: window 1-90 days.
Endpoints: `POST /coach/checkins`, `GET /coach/review`, `GET /coach/progress`.

### 6.7 Coach settings
Timezone (IANA), work hours, buffer minutes. Endpoints: `GET/PUT /coach/settings`.

## 7. Settings (pushed from Home)

| Screen | Content | Endpoints |
|---|---|---|
| Appearance | System / Light / Dark | none (local, G11) |
| Security | App lock, auto-lock delay, privacy blur info | none |
| Devices & sessions | Sessions list (device, last used, current), revoke others, sign out | `GET /auth/sessions`, `DELETE /auth/sessions/{id}`, `POST /auth/logout` |
| Memory | Memories by namespace, search/filter, edit/delete, create a memory (user-authored) | `GET/POST /memory`, `GET/PUT/DELETE /memory/{id}` |
| Memory proposals | Pending proposals (agent-suggested) with provenance and flags; Approve (optionally with embedding text), Edit text, Reject | `GET /memory/proposals`, `POST /memory/{id}/approve|reject` |
| Memory access log | Who read/wrote what (no content) | `GET /memory/access-log` |
| Conversation threads | Working-memory threads, delete | `GET /memory/threads`, `DELETE /memory/threads/{id}` |
| Governance | Kill switch state, spend status (mock model: INR 0), **Emergency stop** (typed reason) ; resume instructions point to the operator CLI (deliberate 403) | `GET /governance/status`, `POST /governance/kill-switch` |
| Agents | Four agents, enabled state, active version; Disable (cannot re-enable: operator only) | `GET /agents[/{id}]`, `POST /agents/{id}/disable` |
| Activity | Executions with status/cost/calls; audit events | `GET /executions[/{id}]`, `GET /audit` |
| Data & privacy | **Erase everything** (typed confirmation `ERASE ALL MY MEMORY`; also clears finance/messaging/coach); **Export: GAP G7** shown as unavailable | `POST /memory/erase` |
| About | Version, backend URL, mock-model notice, the four gates listed as BLOCKED | `GET /health`, `GET /health/ready` |

## 8. Cross-cutting UI requirements

- **Persistent honesty chips:** "Synthetic data" (while gates are BLOCKED) and "Mock model" on any agent output.
- **Emergency-stop banner** appears on every agent-facing surface when `kill_switch.active`; own-data screens stay usable.
- **Confirmations** for delete/erase/disable/emergency stop; typed confirmation for erase.
- **Accessibility** (per screen): labelled controls, logical order, Dynamic Type to the largest accessibility size without truncating
  critical figures, charts with text/table alternatives, 44 pt minimum targets, Reduce Motion variants, Increase Contrast tokens.
- **Copy rules:** never imply the app sent or paid anything; use "Draft", "Suggested", "Awaiting your review".
