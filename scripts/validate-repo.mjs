import { access, readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const errors = [];

async function exists(target) {
  try {
    await access(target);
    return true;
  } catch {
    return false;
  }
}

async function childDirectories(parent) {
  const entries = await readdir(parent, { withFileTypes: true });
  return entries
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith("."))
    .map((entry) => path.join(parent, entry.name));
}

function readQuotedField(source, field) {
  return source.match(new RegExp(`^\\s*${field}:\\s*"([^"]*)"\\s*$`, "m"))?.[1];
}

async function validateSkill(skillDir) {
  const skillFile = path.join(skillDir, "SKILL.md");
  const relative = path.relative(repoRoot, skillDir);

  if (!(await exists(skillFile))) {
    errors.push(`${relative}: missing SKILL.md`);
    return;
  }

  const source = await readFile(skillFile, "utf8");
  const frontmatter = source.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)?.[1];
  if (!frontmatter) {
    errors.push(`${relative}: missing valid YAML frontmatter delimiters`);
    return;
  }

  const keys = [...frontmatter.matchAll(/^([a-z][a-z0-9-]*):/gm)].map((match) => match[1]);
  const unexpected = keys.filter((key) => !["name", "description"].includes(key));
  if (unexpected.length) {
    errors.push(`${relative}: unsupported frontmatter keys: ${unexpected.join(", ")}`);
  }

  const name = frontmatter.match(/^name:\s*([a-z0-9]+(?:-[a-z0-9]+)*)\s*$/m)?.[1];
  const description = frontmatter.match(/^description:\s*(\S.*)$/m)?.[1]?.trim();
  const directoryName = path.basename(skillDir);

  if (!name) errors.push(`${relative}: name must use lowercase letters, digits, and single hyphens`);
  if (name && name !== directoryName) {
    errors.push(`${relative}: name '${name}' must match directory '${directoryName}'`);
  }
  if (!description || description.length > 1024) {
    errors.push(`${relative}: description must contain 1-1024 characters on one line`);
  }
  if (source.split(/\r?\n/).length > 500) {
    errors.push(`${relative}: SKILL.md exceeds 500 lines`);
  }
  if (await exists(path.join(skillDir, "README.md"))) {
    errors.push(`${relative}: move skill-local README content to the repository README`);
  }

  const metadataFile = path.join(skillDir, "agents", "openai.yaml");
  if (!(await exists(metadataFile))) {
    errors.push(`${relative}: missing agents/openai.yaml`);
    return;
  }

  const metadata = await readFile(metadataFile, "utf8");
  const displayName = readQuotedField(metadata, "display_name");
  const shortDescription = readQuotedField(metadata, "short_description");
  const defaultPrompt = readQuotedField(metadata, "default_prompt");

  if (!displayName) errors.push(`${relative}: openai.yaml needs a quoted display_name`);
  if (!shortDescription || shortDescription.length < 25 || shortDescription.length > 64) {
    errors.push(`${relative}: short_description must be a quoted 25-64 character string`);
  }
  if (!defaultPrompt || (name && !defaultPrompt.includes(`$${name}`))) {
    errors.push(`${relative}: default_prompt must be quoted and mention $${name ?? directoryName}`);
  }
}

async function collectJsonFiles(parent) {
  const files = [];
  for (const entry of await readdir(parent, { withFileTypes: true })) {
    if (entry.name === ".git" || entry.name === "node_modules") continue;
    const target = path.join(parent, entry.name);
    if (entry.isDirectory()) files.push(...(await collectJsonFiles(target)));
    if (entry.isFile() && ["marketplace.json", "plugin.json", "hooks.json"].includes(entry.name)) {
      files.push(target);
    }
  }
  return files;
}

async function validateJsonFile(file) {
  const relative = path.relative(repoRoot, file);
  let value;
  try {
    value = JSON.parse(await readFile(file, "utf8"));
  } catch (error) {
    errors.push(`${relative}: invalid JSON (${error.message})`);
    return;
  }

  if (path.basename(file) === "marketplace.json") {
    if (!value.name || !Array.isArray(value.plugins)) {
      errors.push(`${relative}: marketplace needs name and plugins[]`);
      return;
    }
    for (const plugin of value.plugins) {
      const source = typeof plugin.source === "string" ? plugin.source : plugin.source?.path;
      if (!plugin.name || !source || !(await exists(path.resolve(repoRoot, source)))) {
        errors.push(`${relative}: plugin '${plugin.name ?? "unknown"}' has a missing local source`);
      }
    }
  }

  if (path.basename(file) === "plugin.json" && !file.includes(`${path.sep}templates${path.sep}`)) {
    const pluginRoot = path.dirname(path.dirname(file));
    if (value.name !== path.basename(pluginRoot)) {
      errors.push(`${relative}: plugin name must match its directory`);
    }
    if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(value.version ?? "")) {
      errors.push(`${relative}: version must be valid semantic versioning`);
    }
    if (!value.description || !value.author?.name || !value.license) {
      errors.push(`${relative}: plugin needs description, author.name, and license`);
    }
  }
}

const rootSkills = await childDirectories(path.join(repoRoot, "skills"));
const pluginSkills = await childDirectories(
  path.join(repoRoot, "plugins", "ponytail-review-gate", "skills"),
);

for (const skillDir of [...rootSkills, ...pluginSkills]) {
  await validateSkill(skillDir);
}

for (const file of await collectJsonFiles(repoRoot)) {
  await validateJsonFile(file);
}

if (errors.length) {
  console.error(errors.map((error) => `- ${error}`).join("\n"));
  process.exitCode = 1;
} else {
  console.log(`Validated ${rootSkills.length + pluginSkills.length} skills and repository manifests.`);
}
