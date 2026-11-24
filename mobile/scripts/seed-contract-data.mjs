// Seeds SYNTHETIC finance, coach and messaging data for the local contract tests, through the backend's own HTTP API.
//   node scripts/seed-contract-data.mjs <backend-url> <enrollment-code>
// Every name is invented and marked "(demo)"; every amount is round; dates are relative to today. No real data, no model call.
const [, , base, code] = process.argv;
if (!base || !code) {
  console.error('usage: node scripts/seed-contract-data.mjs <backend-url> <enrollment-code>');
  process.exit(2);
}
if (!/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(base)) {
  console.error('refusing: the seeder only talks to a local synthetic backend (http://127.0.0.1:<port>)');
  process.exit(2);
}

const call = async (method, path, token, body) => {
  const res = await fetch(base + path, {
    method,
    headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status} ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : null;
};

// The backend's Coach clock is frozen for the contract run (mobile/scripts/contract-api.py): ABU_CONTRACT_DATE is the frozen UTC
// date and ABU_CONTRACT_NOW the frozen instant, both set once by contract-backend.sh. Without them the real clock is used.
const frozenNow = process.env.ABU_CONTRACT_NOW ? new Date(process.env.ABU_CONTRACT_NOW) : null;
const day = (offset) => {
  const d = process.env.ABU_CONTRACT_DATE ? new Date(`${process.env.ABU_CONTRACT_DATE}T00:00:00Z`) : new Date();
  d.setUTCDate(d.getUTCDate() + offset);
  return d.toISOString().slice(0, 10);
};
const prevMonthDay = (n) => {
  const d = new Date();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() - 1);
  d.setUTCDate(n);
  return d.toISOString().slice(0, 10);
};

const session = await call('POST', '/v1/auth/enroll', null, {
  enrollment_code: code,
  device_name: 'Seeder (synthetic)',
  platform: 'ios',
});
const t = session.access_token;
const post = (path, body) => call('POST', path, t, body);

const bank = await post('/v1/finance/accounts', {
  kind: 'bank',
  label: 'Demo salary account (demo)',
  institution: 'hdfc',
  last4: '1234',
});
const card = await post('/v1/finance/accounts', {
  kind: 'credit_card',
  label: 'Demo card (demo)',
  last4: '5678',
  credit_limit: '10000',
});
const txn = (account_id, date, amount, direction, description) =>
  post('/v1/finance/transactions', { account_id, date, amount, direction, description });
const thisMonth = [
  [bank.id, day(-9), '50000.00', 'credit', 'ACME SALARY'],
  [bank.id, day(-8), '18000.00', 'debit', 'RENT TRANSFER'],
  [bank.id, day(-7), '1200.50', 'debit', 'BIGBASKET'],
  [bank.id, day(-6), '799.50', 'debit', 'SWIGGY'],
  [bank.id, day(-5), '300.00', 'debit', 'SWIGGY'],
  [bank.id, day(-4), '320.00', 'debit', 'BIGBASKET'],
  [bank.id, day(-3), '280.00', 'debit', 'SWIGGY'],
  [bank.id, day(-2), '310.00', 'debit', 'BIGBASKET'],
  [bank.id, day(-1), '12000.00', 'debit', 'FURNITURE STORE'],
  [card.id, day(-1), '3000.00', 'debit', 'SWIGGY'],
  [bank.id, prevMonthDay(1), '50000.00', 'credit', 'ACME SALARY'],
  [bank.id, prevMonthDay(3), '18000.00', 'debit', 'RENT TRANSFER'],
];
for (const r of thisMonth) await txn(...r);
await call('PUT', '/v1/finance/budgets', t, { category: 'groceries', monthly_limit: '1000' });
await post('/v1/finance/subscriptions', {
  label: 'Demo streaming (demo)',
  amount: '499.00',
  period: 'monthly',
  next_due: day(6),
});
await post('/v1/finance/loans', {
  label: 'Demo car loan (demo)',
  principal: '100000',
  annual_rate_pct: '12',
  tenor_months: 12,
  start_date: prevMonthDay(5),
});

const goal = await post('/v1/coach/goals', {
  title: 'Run a demo 10k (demo)',
  category: 'health',
  target_date: '2099-01-01',
});
await post('/v1/coach/goals', { title: 'Read demo books (demo)', category: 'learning' });
await post('/v1/coach/tasks', {
  title: 'Write demo report (demo)',
  priority: 1,
  estimate_minutes: 60,
  due_date: day(0),
  goal_id: goal.id,
});
await post('/v1/coach/tasks', { title: 'Review demo budget (demo)', priority: 2, estimate_minutes: 30 });
// A task longer than any free window of the day (the longest estimate the API allows is 480 minutes; the frozen window is
// 12:00-18:00): it is ALWAYS reported in plan.unscheduled, so the nested fields of unscheduled[] are exercised on every run.
await post('/v1/coach/tasks', { title: 'Plan a demo offsite (demo)', priority: 3, estimate_minutes: 480 });
await post('/v1/coach/tasks', {
  title: 'Reply to demo emails (demo)',
  priority: 3,
  estimate_minutes: 20,
  due_date: day(-1),
});
const daily = await post('/v1/coach/habits', { title: 'Meditate (demo)', cadence: 'daily' });
const weekly = await post('/v1/coach/habits', { title: 'Long walk (demo)', cadence: 'weekly', target_per_week: 3 });
for (const d of [-3, -2, -1]) await post(`/v1/coach/habits/${daily.id}/log`, { date: day(d), done: true });
await post(`/v1/coach/habits/${weekly.id}/log`, { date: day(-1), done: true });
await post('/v1/coach/events', {
  title: 'Demo stand-up (demo)',
  start: `${day(0)}T23:00:00+05:30`,
  end: `${day(0)}T23:30:00+05:30`,
  reminder_minutes: 10,
});
const earlier = new Date((frozenNow ? frozenNow.getTime() : Date.now()) - 3600_000).toISOString(); // due relative to the Coach clock
await post('/v1/coach/reminders', { text: 'Demo reminder (demo)', remind_at: earlier });
await post('/v1/coach/checkins', { kind: 'morning', mood: 4, energy: 3 });

const stamp = (hoursAgo) => new Date(Date.now() - hoursAgo * 3600_000).toISOString();
const messages = (title) => [
  { sender: 'them', text: 'Hey! Are you free tomorrow evening for dinner?', sent_at: stamp(50) },
  { sender: 'me', text: 'Maybe, let me check.', sent_at: stamp(49.8) },
  { sender: 'them', text: 'Could you let me know today? Thanks!', sent_at: stamp(20) },
];
await post('/v1/messaging/conversations/import', {
  platform: 'whatsapp',
  format: 'json',
  text: JSON.stringify(messages()),
  title: 'Asha (demo)',
});
await post('/v1/messaging/conversations/import', {
  platform: 'instagram',
  format: 'json',
  text: JSON.stringify(messages()),
  title: 'Ravi (demo)',
  share_with_assistant: false,
});

console.log('seeded synthetic finance, coach and messaging data');
