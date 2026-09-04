import { spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const frontendRoot = join(projectRoot, "frontend");
const browserRoot = join(
  projectRoot,
  ".vtnote",
  "Cache",
  "test",
  "playwright",
  "browsers",
);
mkdirSync(browserRoot, { recursive: true });

const playwrightCli = join(frontendRoot, "node_modules", "playwright", "cli.js");
const completed = spawnSync(process.execPath, [playwrightCli, "install", "chromium"], {
  cwd: frontendRoot,
  env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: browserRoot },
  stdio: "inherit",
  windowsHide: true,
});
if (completed.error) throw completed.error;
process.exitCode = completed.status ?? 1;
