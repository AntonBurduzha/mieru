// Builds the tracker: minifies src/p.js into dist/p.js and copies it to the folder ingest
// serves it from. Over the size budget it fails and writes nothing, so CI fails too.
// Usage: node build.mjs [--budget <bytes>]
import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import { gzipSync } from "node:zlib";
import { minify } from "terser";

const BUDGET = 1024; // bytes, gzipped

// Paths relative to this file, so the build works from any directory
const source = new URL("src/p.js", import.meta.url);
const output = new URL("dist/p.js", import.meta.url);
const ingestCopy = new URL(
  "../backend/src/mieru/ingest/static/p.js",
  import.meta.url,
);

const { values } = parseArgs({ options: { budget: { type: "string" } } });
const budget = values.budget === undefined ? BUDGET : Number(values.budget);
if (!Number.isInteger(budget) || budget <= 0) {
  console.error(
    `--budget must be a positive whole number of bytes, got ${values.budget}`,
  );
  process.exit(2);
}

const { code } = await minify(await readFile(source, "utf8"), {
  ecma: 2020,
  compress: { passes: 2 },
  format: { comments: false },
});
const gzipped = gzipSync(code, { level: 9 }).length;
console.log(
  `dist/p.js: ${Buffer.byteLength(code)} B, ${gzipped} B gzipped (budget ${budget} B)`,
);
if (gzipped > budget) {
  console.error(`over budget by ${gzipped - budget} B`);
  process.exit(1);
}

await mkdir(new URL(".", output), { recursive: true });
await writeFile(output, code);
await mkdir(new URL(".", ingestCopy), { recursive: true });
await copyFile(output, ingestCopy);
