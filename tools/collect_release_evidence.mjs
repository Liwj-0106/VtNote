import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { readFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import {
  delimiter,
  dirname,
  extname,
  isAbsolute,
  join,
  relative,
  resolve,
} from "node:path";
import { fileURLToPath } from "node:url";

const scriptRoot = dirname(fileURLToPath(import.meta.url));
const repoRoot = dirname(scriptRoot);
const argumentsList = process.argv.slice(2);
const outputIndex = argumentsList.indexOf("--output");
const outputDirectory = resolve(
  outputIndex >= 0 && argumentsList[outputIndex + 1]
    ? argumentsList[outputIndex + 1]
    : join(repoRoot, "dist", "release-evidence"),
);
const outputRelative = relative(repoRoot, outputDirectory);
if (
  !isAbsolute(outputDirectory) ||
  outputRelative.startsWith("..") ||
  isAbsolute(outputRelative)
) {
  throw new Error("release evidence output must remain inside the project");
}
mkdirSync(outputDirectory, { recursive: true });
const outputFile = join(outputDirectory, "release-evidence.json");
if (existsSync(outputFile)) {
  throw new Error("release evidence already exists");
}

const python = process.env.VTNOTE_PYTHON ?? "python";
const ffmpeg = process.env.VTNOTE_FFMPEG ?? "ffmpeg";
const deno =
  process.env.VTNOTE_DENO ??
  join(
    repoRoot,
    ".vtnote",
    "runtime",
    "youtube-runtime",
    "deno",
    "2.8.1",
    "deno.exe",
  );

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function resolveExecutable(executable) {
  if (isAbsolute(executable) && existsSync(executable)) return executable;
  const extensions = process.platform === "win32"
    ? (process.env.PATHEXT ?? ".EXE;.CMD;.BAT;.COM").split(";")
    : [""];
  const candidates = extname(executable) ? [""] : extensions;
  for (const directory of (process.env.PATH ?? "").split(delimiter)) {
    if (!directory) continue;
    for (const extension of candidates) {
      const candidate = join(directory, `${executable}${extension}`);
      if (existsSync(candidate)) return candidate;
    }
  }
  throw new Error("release evidence executable is unavailable");
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

const pythonExecutable = resolveExecutable(python);
const ffmpegExecutable = resolveExecutable(ffmpeg);
const denoExecutable = resolveExecutable(deno);
const ffmpegVersionOutput = run(ffmpegExecutable, ["-version"]);
const ffmpegBuildOutput = run(ffmpegExecutable, ["-buildconf"]);
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
const pythonPackages = JSON.parse(run(pythonExecutable, ["-c", pythonCode]));
const ejsHashCode = [
  "from vtnote.youtube_runtime import SystemYoutubeRuntimeInventory",
  "print(SystemYoutubeRuntimeInventory().package_hash('yt-dlp-ejs') or '')",
].join(";");
const ejsPackageSha256 = run(
  pythonExecutable,
  ["-c", ejsHashCode],
  { PYTHONPATH: join(repoRoot, "src") },
).trim();

const denoVersion = run(
  denoExecutable,
  ["--version"],
  {
    DENO_DIR:
      join(
        repoRoot,
        ".vtnote",
        "runtime",
        "youtube-runtime",
        "deno-cache",
        "2.8.1",
      ),
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
    version: run(pythonExecutable, ["--version"]).trim(),
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
    executable_sha256: sha256(ffmpegExecutable),
    build_configuration: buildFlags,
    distribution_classification: gplEnabled
      ? "development_gpl_only"
      : "lgpl_candidate_requires_review",
  },
  youtube_runtime: {
    deno_version: denoVersion,
    deno_executable_sha256: sha256(denoExecutable),
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
