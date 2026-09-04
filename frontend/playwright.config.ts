import { defineConfig } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(frontendRoot, "..");
const python = process.env.VTNOTE_PYTHON || "python";
const frontendPort = process.env.VTNOTE_FRONTEND_E2E_PORT || "5173";
const frontendUrl = `http://127.0.0.1:${frontendPort}`;
const qaRoot = resolve(
  repositoryRoot,
  ".vtnote",
  "Cache",
  "test",
  "playwright",
);
process.env.PLAYWRIGHT_BROWSERS_PATH ||= resolve(qaRoot, "browsers");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: frontendUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  outputDir: resolve(qaRoot, "results"),
  webServer: [
    {
      command: `"${python}" -m uvicorn vtnote.api:create_app --factory --host 127.0.0.1 --port 8766`,
      cwd: repositoryRoot,
      env: {
        PYTHONPATH: "src",
        VTNOTE_DATA_ROOT: `${qaRoot}\\data`,
        VTNOTE_RUNTIME_CACHE_ROOT: `${qaRoot}\\runtime`,
      },
      url: "http://127.0.0.1:8766/api/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${frontendPort}`,
      cwd: frontendRoot,
      url: frontendUrl,
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
