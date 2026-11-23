"""Templated, deterministic summaries of tool results. Fixed wording filled with computed figures: nothing here
can invent a number, and it works with no model at all."""

from typing import Any


def _inr(v: Any) -> str:
    return f"₹{v}"


def summarise(results: dict[str, Any]) -> str | None:
    lines: list[str] = []
    for name, r in results.items():
        if not isinstance(r, dict):
            continue
        if err := r.get("error"):
            lines.append(f"{name}: unavailable ({err}).")
        elif name == "finance.summary":
            lines.append(
                f"Income {_inr(r['income'])}, spending {_inr(r['expense'])}, net {_inr(r['net'])}."
                + (
                    f" {r['pending_review_count']} transaction(s) await your review and are not counted."
                    if r.get("pending_review_count")
                    else ""
                )
            )
        elif name == "finance.insights":
            lines += [i["text"] for i in r.get("insights", [])] or [
                "No notable insights this period."
            ]
        elif name == "finance.budget_status":
            lines += [
                f"{b['category']}: {_inr(b['spent'])} of {_inr(b['limit'])} ({b['status']})."
                for b in r.get("budgets", [])
            ] or ["No budgets are set."]
        elif name == "finance.upcoming":
            lines += [
                f"{u['kind'].upper()} {u['label']}: {_inr(u['amount'])} due {u['due']}."
                for u in r.get("upcoming", [])
            ]
        elif name == "finance.loans":
            lines += [
                f"{x['label']}: outstanding {_inr(x['outstanding_principal'])}, EMI {_inr(x['emi'])}, next due {x['next_due']}."
                for x in r.get("loans", [])
            ] or ["No loans recorded."]
        elif name == "finance.cards":
            lines += [
                f"{c['label']}: {_inr(c['owed'])} owed, utilisation {c['utilisation_pct']}% ({c['band']})."
                for c in r.get("cards", [])
            ] or ["No credit cards recorded."]
        elif name == "finance.subscriptions":
            lines += [
                f"{s['label']}: {_inr(s['amount'])} {s['period']}, next {s['next_due']}."
                for s in r.get("active", [])
            ]
            if r.get("suggested"):
                lines.append(
                    f"{len(r['suggested'])} possible subscription(s) detected from your history."
                )
        elif name == "finance.pending_count":
            lines.append(f"{r['pending_review']} transaction(s) await your review.")
        elif name == "finance.cashflow":
            lines.append(
                f"Closing balance change this month: {_inr(r['closing'])}."
                + (
                    f" Projected month spending (pace estimate): {_inr(r['projected_month_expense'])}."
                    if r.get("projected_month_expense")
                    else ""
                )
            )
        elif name == "messaging.analyse":
            lines.append(
                f"{r.get('message_count', 0)} messages; reply needed: {'yes' if r.get('needs_reply') else 'no'}; last message intent: {r.get('last_message_intent') or 'n/a'}."
            )
        elif name == "messaging.reply_suggestions":
            lines.append("Draft options (nothing is sent - edit and send them yourself):")
            lines += [f"  {i}. {d['text']}" for i, d in enumerate(r.get("drafts", []), 1)]
        elif name == "messaging.summary":
            lines.append(r.get("summary", ""))
        elif name == "coach.plan":
            lines += [
                f"{b['start'][11:16]}-{b['end'][11:16]} {b['title']}" for b in r.get("blocks", [])
            ] or ["Nothing is scheduled."]
            lines += [
                f"Not scheduled: {u['title']} ({u['reason']})." for u in r.get("unscheduled", [])
            ]
        elif name == "coach.briefing":
            lines.append(
                f"{len(r.get('events', []))} event(s), {len(r['plan']['blocks'])} planned task(s), {len(r.get('habits_open', []))} habit(s) still open."
            )
            lines += [
                f"Goal needs attention: {g['title']} ({g['state']})."
                for g in r.get("goals_needing_attention", [])
            ]
            lines += [f"Streak at risk: {t}." for t in r.get("streaks_at_risk", [])]
        elif name == "coach.review":
            lines.append(
                f"Done today: {r['tasks_completed_today']} task(s); habits {r['habits_done']}/{r['habits_due']}; {len(r.get('carry_over', []))} task(s) carry over."
            )
        elif name == "coach.progress":
            lines.append(
                f"Last {r['window_days']} days: {r['tasks_completed']} task(s) completed, {r['tasks_open']} open."
            )
        elif name == "coach.habits":
            lines += [
                f"{h['title']}: streak {h['current_streak']}." for h in r.get("habits", [])
            ] or ["No habits yet."]
        elif name == "coach.goals":
            lines += [
                f"{g['title']}: {g['progress_pct']}% ({g['progress_state']})."
                for g in r.get("goals", [])
            ] or ["No goals yet."]
    return "\n".join(x for x in lines if x) or None
