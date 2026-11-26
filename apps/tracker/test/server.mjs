// The local page server for the tracker's tests and for trying the script by hand.
// A test fixture only, never deployed.
//
//   node test/server.mjs [--port <n>] [--src]
//
// GET /js/p.js       dist/p.js, or src/p.js with --src; 404 while the file is missing
// GET /<name>.html   test/fixtures/<name>.html
// any other GET      test/fixtures/page.html, like a single-page app's fallback
// POST /api/event    prints "event <body>" and answers 202
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { parseArgs } from "node:util";

const { values } = parseArgs({
  options: {
    port: { type: "string", default: "5180" },
    src: { type: "boolean", default: false },
  },
});

const port = Number(values.port);
const script = new URL(
  values.src ? "../src/p.js" : "../dist/p.js",
  import.meta.url,
);
const fixtures = new URL("fixtures/", import.meta.url);

async function serveFile(res, file, type) {
  let body;
  try {
    body = await readFile(file);
  } catch {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("not found\n");
    return;
  }
  res.writeHead(200, {
    "Content-Type": `${type}; charset=utf-8`,
    "Cache-Control": "no-store",
  });
  res.end(body);
}

function recordEvent(req, res) {
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    console.log(`event ${Buffer.concat(chunks).toString("utf8")}`);
    res.writeHead(202);
    res.end();
  });
}

const server = createServer((req, res) => {
  const { pathname } = new URL(req.url, "http://127.0.0.1");

  if (req.method === "POST" && pathname === "/api/event") {
    recordEvent(req, res);
    return;
  }
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405, { Allow: "GET, HEAD" });
    res.end();
    return;
  }
  if (pathname === "/js/p.js") {
    serveFile(res, script, "text/javascript");
    return;
  }
  // Only plain names, so a path can never leave the fixtures folder.
  const name = /^\/([\w-]+)\.html$/.exec(pathname)?.[1] ?? "page";
  serveFile(res, new URL(`${name}.html`, fixtures), "text/html");
});

server.listen(port, "127.0.0.1", () => {
  console.log(`http://127.0.0.1:${port}/page.html`);
});
