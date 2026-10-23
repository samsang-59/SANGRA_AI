// FE0-S01 and FE0-F03 on the built files (dist/): no secret is ever in the bundle, and the API address in the bundle
// is the one from the env file. Usage: npm run build && node scripts/check-build.mjs [expected-api-url]
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

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
const expected = process.argv[2];
if (expected && !text.includes(expected)) problems.push(`the API address ${expected} is not in the bundle`);

if (problems.length) {
  console.error("Build check FAILED:\n  " + problems.join("\n  "));
  process.exit(1);
}
console.log(`Build check passed: ${all.length} files, no secrets${expected ? `, API address ${expected}` : ""}.`);
