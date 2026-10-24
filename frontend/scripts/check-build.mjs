// FE0-S01, FE7-S02 and FE0-F03 on the built files (dist/): no secret is ever in the bundle, the words GROQ and SECRET
// do not appear at all, "password" only as a label, field name or message (never with a value written after it), and
// the JavaScript and CSS stay within the size budget (FE7-L01), and the API address in the bundle is the one from the
// env file (FE0-F03). Usage: npm run build && node scripts/check-build.mjs [expected-api-url]
// (default: VITE_API_URL from the environment or .env, else the documented default).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const DIST = new URL("../dist/", import.meta.url).pathname;
const SECRET_PATTERNS = [
  /gsk_[A-Za-z0-9]{8,}/, // Groq keys
  /GROQ_API_KEY/,
  /JWT_SECRET/,
  /DATABASE_URL/,
  /postgres(ql)?:\/\/[^"'\s]*:[^"'\s]*@/, // a database address with a password
  /SMTP_PASSWORD/,
  /sk-[A-Za-z0-9]{20,}/, // other API key shapes
];

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

const all = files(DIST).filter((path) => /\.(js|css|html|map|json|txt|svg)$/.test(path));
const problems = [];
let text = "";
for (const path of all) {
  const content = readFileSync(path, "utf8");
  text += content;
  for (const pattern of SECRET_PATTERNS) {
    if (pattern.test(content)) problems.push(`${path.replace(DIST, "dist/")}: matches ${pattern}`);
  }
}
function envFileValue() {
  try {
    const line = readFileSync(new URL("../.env", import.meta.url), "utf8").split("\n")
      .find((l) => l.trim().startsWith("VITE_API_URL="));
    return line ? line.split("=", 2)[1].trim() : "";
  } catch {
    return "";
  }
}
// FE7-S02: GROQ and SECRET never; "password" never followed by a written value such as password:"hunter2".
for (const word of [/groq/gi, /secret/gi]) {
  const found = text.match(word);
  if (found) problems.push(`the word ${word.source.toUpperCase()} is in the bundle ${found.length} times`);
}
const passwordValues = text.match(/(?:[{,]\s*|["'])password["']?\s*[:=]\s*["'`][^"'`]+["'`]/gi);
if (passwordValues) problems.push(`a password value is written in the bundle: ${passwordValues.join(", ")}`);
const passwordWords = (text.match(/password/gi) || []).length;

// FE7-L01: the code the browser loads first (the JavaScript and CSS), gzipped, stays within the budget.
const BUDGET_KB = 300;
const zipped = all.filter((path) => /\.(js|css)$/.test(path))
  .reduce((sum, path) => sum + gzipSync(readFileSync(path)).length, 0) / 1024;
if (zipped > BUDGET_KB) problems.push(`the JavaScript and CSS are ${zipped.toFixed(0)} KB gzipped (budget ${BUDGET_KB} KB)`);

const expected = (process.argv[2] || process.env.VITE_API_URL || envFileValue() || "http://localhost:8000/api/v1")
  .replace(/\/+$/, "");
if (expected && !text.includes(expected)) problems.push(`the API address ${expected} is not in the bundle`);

if (problems.length) {
  console.error("Build check FAILED:\n  " + problems.join("\n  "));
  process.exit(1);
}
console.log(`Build check passed: ${all.length} files, no secrets, no GROQ or SECRET, "password" ${passwordWords} times ` +
  `only as labels, field names and messages, JavaScript and CSS ${zipped.toFixed(0)} KB gzipped (budget ${BUDGET_KB} KB)${expected ? `, API address ${expected}` : ""}.`);
