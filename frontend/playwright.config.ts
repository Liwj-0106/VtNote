import { defineConfig } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(frontendRoot, "..");
const python = "D:\\ProgramData\\Anaconda3\\envs\\vtnote\\python.exe";
const qaRoot = "D:\\Workspace\\Codex\\cache\\VtNote-playwright";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      executablePath:
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    },
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn vtnote.api:create_app --factory --host 127.0.0.1 --port 8765`,
      cwd: repositoryRoot,
      env: {
        PYTHONPATH: "src",
        VTNOTE_DATA_ROOT: `${qaRoot}\\data`,
        VTNOTE_RUNTIME_CACHE_ROOT: `${qaRoot}\\runtime`,
      },
      url: "http://127.0.0.1:8765/api/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5173",
      cwd: frontendRoot,
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
