// Operator-approved development backends are injected at start/build time, never committed and never typed by the user:
//   ABU_DEV_BACKENDS='[{"id":"dev","label":"Dev backend","origin":"https://example-8000.app.github.dev","kind":"development"}]'
// Entries are validated again at runtime (src/config/backendAllowlist.ts); invalid entries are dropped.
module.exports = ({ config }) => {
  let backends = [];
  try {
    const parsed = JSON.parse(process.env.ABU_DEV_BACKENDS ?? '[]');
    if (Array.isArray(parsed)) backends = parsed;
  } catch {
    backends = [];
  }
  return { ...config, extra: { ...(config.extra ?? {}), abuDevBackends: backends } };
};
