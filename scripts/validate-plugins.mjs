import { access, readFile, realpath } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const canonicalRepoRoot = await realpath(repoRoot);
const npmCli = process.env.npm_execpath;
const [skillsPackage, claudePackage] = process.argv.slice(2);
if (!npmCli || !/^skills@\d+\.\d+\.\d+$/.test(skillsPackage ?? "") ||
    !/^@anthropic-ai\/claude-code@\d+\.\d+\.\d+$/.test(claudePackage ?? "")) {
  throw new Error("Provide pinned skills and Claude Code packages; use npm run validate:plugins.");
}

const sources = new Map();
for (const [marketplacePath, manifestPath] of [
  [".agents/plugins/marketplace.json", ".codex-plugin/plugin.json"],
  [".claude-plugin/marketplace.json", ".claude-plugin/plugin.json"],
]) {
  const marketplace = JSON.parse(await readFile(path.join(repoRoot, marketplacePath), "utf8"));
  for (const plugin of marketplace.plugins) {
    const source = typeof plugin.source === "string" ? plugin.source : plugin.source?.path;
    if (!source || (!source.startsWith("./") && !source.startsWith("../"))) {
      throw new Error(`${marketplacePath}: '${plugin.name}' must have a relative local source.`);
    }
    const pluginRoot = await realpath(path.resolve(repoRoot, source));
    const relative = path.relative(canonicalRepoRoot, pluginRoot);
    if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      throw new Error(`${marketplacePath}: '${plugin.name}' resolves outside the repository.`);
    }
    await access(path.join(pluginRoot, manifestPath));
    const manifests = sources.get(pluginRoot) ?? new Set();
    manifests.add(manifestPath);
    sources.set(pluginRoot, manifests);
  }
}

function run(packageName, command, args) {
  const result = spawnSync(process.execPath, [npmCli, "exec", "--yes", "--package", packageName, "--", command, ...args], {
    cwd: repoRoot,
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${packageName} ${args.join(" ")} failed (${result.signal ?? result.status}).`);
  }
}

for (const [pluginRoot, manifests] of sources) {
  console.log(`Validating distributed plugin: ${path.relative(repoRoot, pluginRoot)}`);
  run(skillsPackage, "skills", ["add", pluginRoot, "--list"]);
  if (manifests.has(".claude-plugin/plugin.json")) {
    run(claudePackage, "claude", ["plugin", "validate", pluginRoot]);
  }
}
console.log(`Validated discovery at ${sources.size} distributed plugin sources and their Claude manifests.`);
