
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { test, expect } from "@playwright/test";
import { DIST_SCRIPT } from "./helpers.js";

const TRACKER = fileURLToPath(new URL("..", import.meta.url));
const BUILD = fileURLToPath(new URL("../build.mjs", import.meta.url));
const STATIC_COPY = fileURLToPath(
  new URL("../../backend/src/mieru/ingest/static/p.js", import.meta.url),
);
const SIZE_LINE = /dist\/p\.js: (\d+) B, (\d+) B gzipped \(budget 100 B\)/;

function build(cwd, ...args) {
  return spawnSync(process.execPath, [BUILD, ...args], {
    cwd,
    encoding: "utf8",
  });
}

// undefined while the file doesn't exist, so a file that appears counts as a change.
function mtime(file) {
  return statSync(file, { throwIfNoEntry: false })?.mtimeMs;
}

test("the built script is at most 1024 B gzipped and ingest's copy is identical", () => {
  expect(existsSync(DIST_SCRIPT), "dist/p.js is built").toBe(true);
  expect(existsSync(STATIC_COPY), "ingest's static copy exists").toBe(true);

  const built = readFileSync(DIST_SCRIPT);
  expect(gzipSync(built, { level: 9 }).length).toBeLessThanOrEqual(1024);
  expect(
    readFileSync(STATIC_COPY).equals(built),
    "ingest's static p.js is byte-identical to dist/p.js",
  ).toBe(true);
});

test("over the budget, the build exits 1 and changes neither file", () => {
  const before = { dist: mtime(DIST_SCRIPT), copy: mtime(STATIC_COPY) };

  const result = build(TRACKER, "--budget", "100");

  expect(result.stdout).toMatch(SIZE_LINE);
  const gzipped = Number(SIZE_LINE.exec(result.stdout)[2]);
  expect(result.stderr).toContain(`over budget by ${gzipped - 100} B`);
  expect(result.status).toBe(1);
  expect({ dist: mtime(DIST_SCRIPT), copy: mtime(STATIC_COPY) }).toEqual(
    before,
  );
});

test("the build finds its files from any working directory", () => {
  // Over the budget, so the run measures src/p.js and writes nothing.
  const result = build(tmpdir(), "--budget", "100");

  expect(result.stdout).toMatch(SIZE_LINE);
  expect(result.status).toBe(1);
});
