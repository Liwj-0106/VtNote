import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { readFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptRoot = dirname(fileURLToPath(import.meta.url));
const repoRoot = dirname(scriptRoot);
const argumentsList = process.argv.slice(2);
const outputIndex = argumentsList.indexOf("--output");
if (outputIndex < 0 || !argumentsList[outputIndex + 1]) {
  throw new Error("--output is required");
}
const outputDirectory = resolve(argumentsList[outputIndex + 1]);
if (!isAbsolute(outputDirectory) || !/^D:\\/iu.test(outputDirectory)) {
  throw new Error("release evidence output must be an absolute D-drive directory");
}
mkdirSync(outputDirectory, { recursive: true });
const outputFile = join(outputDirectory, "release-evidence.json");
if (existsSync(outputFile)) {
  throw new Error("release evidence already exists");
}

const python =
  process.env.VTNOTE_PYTHON ??
  "D:\\ProgramData\\Anaconda3\\envs\\vtnote\\python.exe";
const ffmpeg =
  process.env.VTNOTE_FFMPEG ??
  "D:\\ProgramData\\Anaconda3\\envs\\vtnote\\Library\\bin\\ffmpeg.exe";
const deno =
  process.env.VTNOTE_DENO ??
  "D:\\Workspace\\Codex\\cache\\VtNote-runtime\\youtube-runtime\\deno\\2.8.1\\deno.exe";

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function controlledEnvironment(extra = {}) {
  const environment = {};
  for (const name of ["SystemRoot", "WINDIR", "PATH"]) {
    if (process.env[name]) environment[name] = process.env[name];
  }
  return { ...environment, ...extra };
}

function run(executable, args, extraEnvironment = {}) {
  const completed = spawnSync(executable, args, {
    cwd: repoRoot,
    encoding: "utf8",
    maxBuffer: 4 * 1024 * 1024,
    windowsHide: true,
    env: controlledEnvironment(extraEnvironment),
  });
  if (completed.status !== 0 || completed.error) {
    throw new Error("release evidence command failed");
  }
  return `${completed.stdout ?? ""}${completed.stderr ?? ""}`.replaceAll(
    "\r\n",
    "\n",
  );
}

const ffmpegVersionOutput = run(ffmpeg, ["-version"]);
const ffmpegBuildOutput = run(ffmpeg, ["-buildconf"]);
const versionLine = ffmpegVersionOutput.split("\n")[0].trim();
const configurationLine =
  ffmpegBuildOutput
    .split("\n")
    .filter((line) => line.trim().startsWith("configuration:"))
    .at(-1) ?? "";
const buildFlags = configurationLine
  .replace(/^\s*configuration:\s*/u, "")
  .split(/\s+/u)
  .filter((value) => value.startsWith("--"))
  .map((value) =>
    /^(--prefix|--pkg-config)=/u.test(value)
      ? `${value.split("=")[0]}=[path]`
      : value,
  )
  .sort();
const gplEnabled =
  buildFlags.includes("--enable-gpl") ||
  buildFlags.includes("--enable-nonfree");

const packageNames = [
  "fastapi",
  "sqlalchemy",
  "uvicorn",
  "httpx",
  "keyring",
  "yt-dlp",
  "yt-dlp-ejs",
  "faster-whisper",
  "ctranslate2",
  "cos-python-sdk-v5",
];
const pythonCode = [
  "import importlib.metadata as m, json",
  `names=${JSON.stringify(packageNames)}`,
  "print(json.dumps({name:m.version(name) for name in names},sort_keys=True))",
].join(";");
const pythonPackages = JSON.parse(run(python, ["-c", pythonCode]));
const ejsHashCode = [
  "from vtnote.youtube_runtime import SystemYoutubeRuntimeInventory",
  "print(SystemYoutubeRuntimeInventory().package_hash('yt-dlp-ejs') or '')",
].join(";");
const ejsPackageSha256 = run(
  python,
  ["-c", ejsHashCode],
  { PYTHONPATH: join(repoRoot, "src") },
).trim();

const denoVersion = run(
  deno,
  ["--version"],
  {
    DENO_DIR:
      "D:\\Workspace\\Codex\\cache\\VtNote-runtime\\youtube-runtime\\deno-cache\\2.8.1",
  },
)
  .split("\n")[0]
  .trim();
const modelManifestPath = join(
  repoRoot,
  "assets",
  "models",
  "large-v3-turbo.manifest.json",
);
const modelManifest = JSON.parse(readFileSync(modelManifestPath, "utf8"));
const frontendLockPath = join(repoRoot, "frontend", "package-lock.json");
const frontendLock = JSON.parse(readFileSync(frontendLockPath, "utf8"));

const evidence = {
  schema_version: 1,
  source_commit: run("git", ["rev-parse", "HEAD"]).trim(),
  python: {
    version: run(python, ["--version"]).trim(),
    packages: pythonPackages,
    requirements_lock_sha256: sha256(join(repoRoot, "requirements.lock")),
    environment_yml_sha256: sha256(join(repoRoot, "environment.yml")),
  },
  frontend: {
    lockfile_version: frontendLock.lockfileVersion,
    package_lock_sha256: sha256(frontendLockPath),
  },
  ffmpeg: {
    version: versionLine,
    executable_sha256: sha256(ffmpeg),
    build_configuration: buildFlags,
    distribution_classification: gplEnabled
      ? "development_gpl_only"
      : "lgpl_candidate_requires_review",
  },
  youtube_runtime: {
    deno_version: denoVersion,
    deno_executable_sha256: sha256(deno),
    ejs_package_sha256: ejsPackageSha256,
    remote_components_enabled: false,
  },
  local_model: {
    name: modelManifest.model_name,
    revision: modelManifest.revision,
    manifest_sha256: sha256(modelManifestPath),
  },
};

const rendered = `${JSON.stringify(evidence, null, 2)}\n`;
if (/[A-Za-z]:\\/u.test(rendered)) {
  throw new Error("absolute path escaped into release evidence");
}
writeFileSync(outputFile, rendered, { encoding: "utf8", flag: "wx" });
process.stdout.write("release-evidence.json\n");
