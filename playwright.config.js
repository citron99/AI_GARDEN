const { defineConfig, devices } = require("@playwright/test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const localNoProxy = new Set(
  `${process.env.NO_PROXY || ""},${process.env.no_proxy || ""}`
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);
localNoProxy.add("127.0.0.1");
localNoProxy.add("localhost");
process.env.NO_PROXY = [...localNoProxy].join(",");
process.env.no_proxy = process.env.NO_PROXY;

const e2eStateDir = path.join(os.tmpdir(), `ai-garden-e2e-${process.pid}`);
fs.mkdirSync(e2eStateDir, { recursive: true });
const e2eDatabasePath = path.join(e2eStateDir, "gardener.db").replaceAll("\\", "/");

module.exports = defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  use: {
    baseURL: "http://127.0.0.1:18001",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop-chrome", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chrome", use: { ...devices["Pixel 5"] } },
  ],
  webServer: {
    command: "python -m alembic upgrade head && python -m uvicorn app.main:app --host 127.0.0.1 --port 18001",
    url: "http://127.0.0.1:18001/health",
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
    env: {
      DATABASE_URL: `sqlite:///${e2eDatabasePath}`,
      UPLOAD_DIR: path.join(e2eStateDir, "uploads"),
      ENVIRONMENT: "test",
      AI_PROVIDER: "mock",
      PARTNER_COMMERCE_ENABLED: "true",
      B2B_INVOICING_ENABLED: "false",
      JWT_SECRET: "e2e-jwt-secret-that-is-longer-than-thirty-two-characters",
      AI_SAFETY_SECRET: "e2e-safety-secret-that-is-different-and-long-enough",
      PARTNER_ATTRIBUTION_SECRET: "e2e-partner-secret-that-is-separate-and-long-enough",
      WEATHER_TIMEOUT_SECONDS: "0.2",
    },
  },
});
