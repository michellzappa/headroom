import { communityPage } from "./community_page.js";

const ALLOWED_PROVIDERS = new Set([
  "ai-gateway", "antigravity", "axiom", "claude", "codex", "copilot", "cursor", "datadog",
  "gemini", "git", "github", "grok", "jetbrains", "local", "openrouter",
  "plausible", "posthog", "sentry", "supabase", "vercel", "windsurf", "zed",
]);

const ALLOWED_MODEL_FAMILIES = new Set([
  "sonnet", "opus", "haiku", "gpt", "codex", "gemini", "cursor", "other",
]);

const ALLOWED_FEATURES = new Set([
  "phone_paired", "agent_gateway_enabled", "multi_mac_enabled",
]);

// Week-over-week retention, without a cross-week identifier. The Mac derives
// this from its own last submitted period, so the intake learns that *a* Mac
// came back, never which one. Columns, not dimensions: these outlive the raw
// retention window because they are the growth series.
const ALLOWED_COHORTS = new Set(["new", "returning", "reactivated"]);
const COHORT_COLUMNS = {
  new: "new_macs",
  returning: "returning_macs",
  reactivated: "reactivated_macs",
};

const COMMUNITY_MINIMUM_GROUP_SIZE = 1;
// Raw batches and per-week dimension rollups expire on this window.
// `telemetry_periods` does not — see the `scheduled` handler. Six months so a
// breakdown can be compared against the same week two quarters back; the rows
// it keeps are already whitelisted aggregates with no IP and no install id.
const COMMUNITY_WINDOW_DAYS = 180;
// How many weekly bars the public payload carries. Weekly counts are kept
// indefinitely, so this is a display limit rather than a retention one.
const COMMUNITY_HISTORY_WEEKS = 52;

function json(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...extraHeaders,
    },
  });
}

function boundedString(value, max = 80) {
  return typeof value === "string" && value.length > 0 && value.length <= max
    ? value
    : null;
}

function providerList(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item) =>
    typeof item === "string" && ALLOWED_PROVIDERS.has(item)))]
    .sort();
}

function safeModels(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const result = {};
  for (const [provider, families] of Object.entries(value)) {
    if (!ALLOWED_PROVIDERS.has(provider) || !families || typeof families !== "object") {
      continue;
    }
    const safe = {};
    for (const [family, share] of Object.entries(families)) {
      if (ALLOWED_MODEL_FAMILIES.has(family)
          && Number.isInteger(share) && share >= 0 && share <= 100) {
        safe[family] = share;
      }
    }
    if (Object.keys(safe).length > 0) result[provider] = safe;
  }
  return result;
}

function sanitize(payload) {
  if (!payload || typeof payload !== "object") return null;
  const schema = payload.schema;
  const batchID = boundedString(payload.batch_id, 80);
  const period = boundedString(payload.period, 12);
  const dedupeKey = typeof payload.dedupe_key === "string"
      && /^[0-9a-f]{64}$/.test(payload.dedupe_key)
    ? payload.dedupe_key
    : null;
  const cohort = typeof payload.cohort === "string"
      && ALLOWED_COHORTS.has(payload.cohort)
    ? payload.cohort
    : null;
  const app = payload.app;
  const providers = payload.providers;
  // Schema 2 requires a week-scoped HMAC dedupe key so one install cannot
  // inflate the week by minting fresh batch ids.
  if (schema !== 2 || !batchID || !period || !dedupeKey
      || !/^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$/.test(period)) {
    return null;
  }
  if (!app || typeof app !== "object" || !providers || typeof providers !== "object") {
    return null;
  }
  const version = boundedString(app.version, 32);
  const build = boundedString(app.build, 64);
  const architecture = ["arm64", "x86_64", "unknown"].includes(app.architecture)
    ? app.architecture : "unknown";
  const macosMajor = Number.isInteger(app.macos_major) && app.macos_major >= 10 && app.macos_major <= 99
    ? app.macos_major : null;
  if (!version || !build || macosMajor === null) return null;

  const features = {};
  if (payload.features && typeof payload.features === "object") {
    for (const [key, value] of Object.entries(payload.features)) {
      if (ALLOWED_FEATURES.has(key) && typeof value === "boolean") features[key] = value;
    }
  }
  const safe = {
    schema: 2,
    batch_id: batchID,
    dedupe_key: dedupeKey,
    period,
    cohort,
    app: {
      version,
      build,
      host_version: boundedString(app.host_version, 32),
      macos_major: macosMajor,
      architecture,
    },
    providers: {
      enabled: providerList(providers.enabled),
      used: providerList(providers.used),
      healthy: providerList(providers.healthy),
    },
    models: safeModels(payload.models),
    features,
  };
  return JSON.stringify(safe).length <= 16384 ? safe : null;
}

function publicCount(count) {
  return count >= COMMUNITY_MINIMUM_GROUP_SIZE ? count : null;
}

/** Marketing versions are always X.Y.Z; drop junk like "1" or "9.9.9". */
function compareAppVersions(left, right) {
  const parts = (value) => String(value).split(".").map((part) => {
    const number = Number.parseInt(part, 10);
    return Number.isFinite(number) ? number : 0;
  });
  const a = parts(left);
  const b = parts(right);
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    const delta = (a[index] ?? 0) - (b[index] ?? 0);
    if (delta !== 0) return delta;
  }
  return 0;
}

function publishableAppVersions(versions, maxVersion) {
  return versions.filter((row) => {
    if (!/^\d+\.\d+\.\d+$/.test(row.name)) return false;
    if (maxVersion && compareAppVersions(row.name, maxVersion) > 0) return false;
    return true;
  });
}

function dimensionRows(rows, dimension, period) {
  return rows
    .filter((row) => row.dimension === dimension && row.period === period)
    .map((row) => ({
      name: row.item,
      count: Number(row.sample_count),
      value: Number(row.value_total),
    }));
}

function sortByCount(rows) {
  return rows
    .filter((row) => row.count >= COMMUNITY_MINIMUM_GROUP_SIZE)
    .sort((lhs, rhs) => {
      if (lhs.count !== rhs.count) return rhs.count - lhs.count;
      return lhs.name.localeCompare(rhs.name);
    });
}

function countedItems(rows) {
  return sortByCount(rows).map((row) => ({ name: row.name, count: row.count }));
}

/** ISO week label for a UTC date, matching the Mac client's period format. */
function isoWeekPeriod(date) {
  const utc = new Date(Date.UTC(
    date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  // Thursday in current week decides the year.
  utc.setUTCDate(utc.getUTCDate() + 4 - (utc.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
  const week = Math.ceil((((utc - yearStart) / 86400000) + 1) / 7);
  return `${utc.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

/**
 * Every ISO week from the first reporting week to now, oldest first. Weeks
 * with no batches, or batches below the privacy floor, publish as count: null
 * so the growth chart keeps a continuous axis.
 *
 * The newest entry carries `in_progress: true`. A Mac reports once per ISO
 * week, at its first launch inside that week, so the newest bar fills over
 * seven days. Read against a finished week it looks like a collapse every
 * Monday; clients use the flag to compare complete weeks instead.
 */
function paddedWeeklyActive(observed, weeks = COMMUNITY_HISTORY_WEEKS) {
  const byPeriod = new Map(observed.map((row) => [row.period, row]));
  const end = new Date();
  const start = new Date(end.getTime() - weeks * 7 * 86400000);
  const periods = [];
  const cursor = new Date(Date.UTC(
    start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
  const last = new Date(Date.UTC(
    end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate()));
  while (cursor <= last) {
    const period = isoWeekPeriod(cursor);
    if (periods.length === 0 || periods[periods.length - 1] !== period) {
      periods.push(period);
    }
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  // Weeks before the first report are not a decline to zero, they are the time
  // before anything reported. Trim that head so the axis starts where the data
  // does and the first real week is not read as growth from nothing.
  const first = periods.findIndex((period) => byPeriod.has(period));
  const axis = first < 0 ? periods.slice(-1) : periods.slice(first);
  const current = isoWeekPeriod(end);
  return axis.map((period) => {
    const row = byPeriod.get(period) ?? null;
    return {
      period,
      count: row ? publicCount(row.count) : null,
      in_progress: period === current,
      new_macs: row ? publicCount(row.new) : null,
      returning_macs: row ? publicCount(row.returning) : null,
      reactivated_macs: row ? publicCount(row.reactivated) : null,
    };
  });
}

async function batchColumnCounts(env, period, column) {
  if (column !== "architecture" && column !== "macos_major" && column !== "country") {
    return [];
  }
  const result = await env.DB.prepare(
    `SELECT CAST(${column} AS TEXT) AS item, COUNT(*) AS sample_count
     FROM telemetry_batches
     WHERE period = ?
       AND ${column} IS NOT NULL
       AND ${column} != ''
     GROUP BY ${column}`
  ).bind(period).all();
  return countedItems((result.results ?? []).map((row) => ({
    name: String(row.item),
    count: Number(row.sample_count),
  })));
}

/** ISO-3166 alpha-2 from Cloudflare's edge geo. Never stores the IP. */
function requestCountry(request) {
  const raw = request.cf && typeof request.cf.country === "string"
    ? request.cf.country.toUpperCase()
    : null;
  if (!raw || raw === "XX" || raw === "T1") return null;
  return /^[A-Z]{2}$/.test(raw) ? raw : null;
}

async function latestRelease() {
  try {
    const response = await fetch("https://updates.centaur-labs.io/latest.json", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return null;
    const doc = await response.json();
    if (!doc || typeof doc.version !== "string" || !doc.version) return null;
    return {
      version: doc.version.slice(0, 32),
      published: typeof doc.published === "string" ? doc.published.slice(0, 40) : null,
    };
  } catch {
    return null;
  }
}

async function communityStats(env) {
  const periodsResult = await env.DB.prepare(
    `SELECT period, batch_count, new_macs, returning_macs, reactivated_macs
     FROM telemetry_periods
     ORDER BY period DESC
     LIMIT ?`
  ).bind(COMMUNITY_HISTORY_WEEKS).all();
  const periods = (periodsResult.results ?? []).map((row) => ({
    period: row.period,
    count: Number(row.batch_count),
    new: Number(row.new_macs ?? 0),
    returning: Number(row.returning_macs ?? 0),
    reactivated: Number(row.reactivated_macs ?? 0),
  }));
  const weeklyActiveMacs = paddedWeeklyActive(periods);
  const release = await latestRelease();
  const latest = periods[0];
  if (!latest) {
    return {
      schema: 1,
      generated_on: new Date().toISOString().slice(0, 10),
      privacy: { minimum_group_size: COMMUNITY_MINIMUM_GROUP_SIZE },
      weekly_active_macs: weeklyActiveMacs,
      latest_release: release,
      latest: null,
    };
  }

  // Only the newest week is published as a breakdown, so only that week is
  // read. Older dimension rows expire with the raw batches anyway.
  const dimensionsResult = await env.DB.prepare(
    `SELECT period, dimension, item, sample_count, value_total
     FROM telemetry_dimensions
     WHERE period = ?`
  ).bind(latest.period).all();
  const rows = dimensionsResult.results ?? [];
  const versions = publishableAppVersions(
    countedItems(dimensionRows(rows, "version", latest.period)),
    release?.version ?? null,
  );
  const enabled = countedItems(
    dimensionRows(rows, "provider_enabled", latest.period));
  const used = countedItems(
    dimensionRows(rows, "provider_used", latest.period));
  const healthy = countedItems(
    dimensionRows(rows, "provider_healthy", latest.period));
  // Prefer live batch columns for platform mix so weeks that predate the
  // macos_major dimension rollup still publish, and architecture stays aligned
  // with the same source.
  const [architectures, macosMajors, countries] = await Promise.all([
    batchColumnCounts(env, latest.period, "architecture"),
    batchColumnCounts(env, latest.period, "macos_major"),
    batchColumnCounts(env, latest.period, "country"),
  ]);
  const modelShares = dimensionRows(rows, "model_share", latest.period)
    .filter((row) => row.count >= COMMUNITY_MINIMUM_GROUP_SIZE)
    .map((row) => ({
      name: row.name,
      share: Math.round(row.value / row.count),
      count: row.count,
    }))
    .sort((lhs, rhs) => {
      if (lhs.share !== rhs.share) return rhs.share - lhs.share;
      return lhs.name.localeCompare(rhs.name);
    });
  const features = dimensionRows(rows, "feature", latest.period)
    .filter((row) => row.count >= COMMUNITY_MINIMUM_GROUP_SIZE)
    .map((row) => ({
      name: row.name,
      adoption: Math.round(row.value / row.count * 100),
      count: row.count,
    }))
    .sort((lhs, rhs) => lhs.name.localeCompare(rhs.name));

  return {
    schema: 1,
    generated_on: new Date().toISOString().slice(0, 10),
    privacy: { minimum_group_size: COMMUNITY_MINIMUM_GROUP_SIZE },
    weekly_active_macs: weeklyActiveMacs,
    latest_release: release,
    latest: {
      period: latest.period,
      // Everything under `latest` is week to date while this is true.
      in_progress: latest.period === isoWeekPeriod(new Date()),
      reporting_macs: publicCount(latest.count),
      versions,
      architectures,
      macos_majors: macosMajors,
      countries,
      services: {
        enabled,
        used,
        healthy,
      },
      model_shares: modelShares.map((row) => ({
        name: row.name,
        share: row.share,
      })),
      features: features.map((row) => ({
        name: row.name,
        adoption: row.adoption,
      })),
    },
  };
}

function upsertDimension(env, period, dimension, item, valueTotal = 0) {
  return env.DB.prepare(
    `INSERT INTO telemetry_dimensions
       (period, dimension, item, sample_count, value_total)
     VALUES (?, ?, ?, 1, ?)
     ON CONFLICT(period, dimension, item) DO UPDATE SET
       sample_count = sample_count + 1,
       value_total = value_total + excluded.value_total`
  ).bind(period, dimension, item, valueTotal);
}

async function recordRollups(env, safe, receivedAt, country) {
  // Fixed map, never the client string, so the column name cannot be steered.
  const cohortColumn = safe.cohort ? COHORT_COLUMNS[safe.cohort] : null;
  const statements = [
    env.DB.prepare(
      `INSERT INTO telemetry_periods
         (period, batch_count, last_received_at${
           cohortColumn ? `, ${cohortColumn}` : ""})
       VALUES (?, 1, ?${cohortColumn ? ", 1" : ""})
       ON CONFLICT(period) DO UPDATE SET
         batch_count = batch_count + 1,
         last_received_at = excluded.last_received_at${
           cohortColumn ? `, ${cohortColumn} = ${cohortColumn} + 1` : ""}`
    ).bind(safe.period, receivedAt),
    upsertDimension(env, safe.period, "version", safe.app.version),
    upsertDimension(env, safe.period, "architecture", safe.app.architecture),
    upsertDimension(
      env, safe.period, "macos_major", String(safe.app.macos_major)),
  ];
  if (country) {
    statements.push(upsertDimension(env, safe.period, "country", country));
  }
  for (const provider of safe.providers.enabled) {
    statements.push(upsertDimension(
      env, safe.period, "provider_enabled", provider));
  }
  for (const provider of safe.providers.used) {
    statements.push(upsertDimension(
      env, safe.period, "provider_used", provider));
  }
  for (const provider of safe.providers.healthy) {
    statements.push(upsertDimension(
      env, safe.period, "provider_healthy", provider));
  }
  for (const [provider, families] of Object.entries(safe.models)) {
    for (const [family, share] of Object.entries(families)) {
      statements.push(upsertDimension(
        env, safe.period, "model_share", `${provider}:${family}`, share));
    }
  }
  for (const [feature, enabled] of Object.entries(safe.features)) {
    statements.push(upsertDimension(
      env, safe.period, "feature", feature, enabled ? 1 : 0));
  }
  await env.DB.batch(statements);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/v1/community") {
      return json(await communityStats(env), 200, {
        "access-control-allow-origin": "*",
        "cache-control": "public, max-age=300",
      });
    }
    if (request.method === "GET" &&
        (url.pathname === "/" || url.pathname === "/community")) {
      return new Response(communityPage(), {
        headers: {
          "content-type": "text/html; charset=utf-8",
          // Inline page JS ships with the Worker; don't cache across deploys.
          "cache-control": "no-store",
        },
      });
    }
    if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
    if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
      return json({ error: "content_type_required" }, 415);
    }
    let payload;
    try {
      payload = await request.json();
    } catch {
      return json({ error: "invalid_json" }, 400);
    }
    const safe = sanitize(payload);
    if (!safe) return json({ error: "invalid_payload" }, 400);
    const receivedAt = new Date().toISOString();
    const country = requestCountry(request);
    const result = await env.DB.prepare(
      `INSERT OR IGNORE INTO telemetry_batches
       (batch_id, received_at, period, dedupe_key, app_version, host_version, macos_major, architecture, country, payload)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    ).bind(
      safe.batch_id,
      receivedAt,
      safe.period,
      safe.dedupe_key,
      safe.app.version,
      safe.app.host_version,
      safe.app.macos_major,
      safe.app.architecture,
      country,
      JSON.stringify(safe),
    ).run();
    const inserted = (result.meta?.changes ?? result.changes ?? 0) > 0;
    if (inserted) await recordRollups(env, safe, receivedAt, country);
    return json({ ok: true });
  },

  /**
   * Raw batches and per-week breakdowns expire on the retention window.
   * `telemetry_periods` does not, and that is the point: it holds a period, a
   * count of Macs, and three cohort counts. Nothing in it describes a Mac, so
   * keeping it costs no privacy and is the only way growth stays visible past
   * a month. Deleting it was why the chart could never show more than five
   * weeks.
   */
  async scheduled(_event, env) {
    const expired = await env.DB.prepare(
      `SELECT period FROM telemetry_periods
       WHERE last_received_at < datetime('now', ?)`
    ).bind(`-${COMMUNITY_WINDOW_DAYS} days`).all();
    const periods = (expired.results ?? []).map((row) => row.period);
    if (periods.length > 0) {
      await env.DB.prepare(
        `DELETE FROM telemetry_dimensions
         WHERE period IN (${periods.map(() => "?").join(",")})`
      ).bind(...periods).run();
    }
    await env.DB.prepare(
      "DELETE FROM telemetry_batches WHERE received_at < datetime('now', ?)"
    ).bind(`-${COMMUNITY_WINDOW_DAYS} days`).run();
  },
};
