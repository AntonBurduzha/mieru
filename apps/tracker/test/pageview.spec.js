// Criterion 1 (T1: the load page view) and criterion 5 (nothing stored, no error).
import { readFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";
import {
  DIST_SCRIPT,
  ORIGIN,
  calls,
  pageview,
  sent,
  track,
} from "./helpers.js";

const PAGE = `${ORIGIN}/page.html`;

test("loading the page sends one page view with the URL, the referrer and the domain", async ({
  page,
}) => {
  await track(page);
  await page.goto("/page.html?utm_source=hn", {
    referer: "https://news.ycombinator.com/",
  });

  expect(await sent(page)).toEqual([
    {
      n: "pageview",
      d: "example.com",
      u: `${PAGE}?utm_source=hn`,
      r: "https://news.ycombinator.com/",
      p: {},
    },
  ]);
});

test("without a referrer, r is null", async ({ page }) => {
  await track(page);
  await page.goto("/page.html");

  expect(await sent(page)).toEqual([pageview(PAGE)]);
});

test("the page view is a keepalive POST of text/plain to /api/event", async ({
  page,
}) => {
  await track(page);
  await page.goto("/page.html");

  const recorded = await calls(page);
  expect(recorded).toMatchObject([
    { url: `${ORIGIN}/api/event`, init: { method: "POST", keepalive: true } },
  ]);
  // Only Content-Type: any other header would make a cross-origin POST preflight.
  expect(recorded[0].init.headers).toEqual({ "content-type": "text/plain" });
});

test("on the wire, the page view is a POST of text/plain with the JSON body", async ({
  page,
}) => {
  await track(page);
  const request = page.waitForRequest(`${ORIGIN}/api/event`, {
    timeout: 5_000,
  });
  await page.goto("/page.html");

  const req = await request;
  expect(req.method()).toBe("POST");
  expect(req.headers()["content-type"]).toMatch(/^text\/plain/);
  expect(req.postDataJSON()).toEqual(pageview(PAGE));
});

test("the page view goes to /api/event on the script's origin, not the page's", async ({
  page,
}) => {
  await track(page);
  await page.route("http://site.test/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><link rel="icon" href="data:,">
        <script defer data-domain="example.com" src="http://in.mieru.test/js/p.js"></script>`,
    }),
  );
  await page.route("http://in.mieru.test/js/p.js", async (route) => {
    const script = await readFile(DIST_SCRIPT).catch(() => null);
    if (script === null) return route.fulfill({ status: 404 });
    return route.fulfill({ contentType: "text/javascript", body: script });
  });
  await page.route("http://in.mieru.test/api/event", (route) =>
    route.fulfill({
      status: 202,
      headers: { "Access-Control-Allow-Origin": "*" },
    }),
  );
  await page.goto("http://site.test/blog");

  expect(await calls(page)).toMatchObject([
    { url: "http://in.mieru.test/api/event" },
  ]);
  expect(await sent(page)).toEqual([pageview("http://site.test/blog")]);
});

test("without data-domain, d is the page's host name", async ({ page }) => {
  await track(page);
  await page.goto("/nodomain.html");

  expect(await sent(page)).toEqual([
    pageview(`${ORIGIN}/nodomain.html`, { d: "127.0.0.1" }),
  ]);
});

test("the script sets no cookie and writes nothing to localStorage or sessionStorage", async ({
  page,
}) => {
  await track(page);
  await page.goto("/page.html");
  // The script ran and sent its page view, so it had every chance to store something.
  expect(await sent(page)).toEqual([pageview(PAGE)]);

  await page.evaluate(() => {
    history.pushState({}, "", "/pricing");
    mieru("signup", { props: { plan: "pro" } });
  });
  expect(await sent(page)).toHaveLength(3);

  const storage = await page.evaluate(() => ({
    cookie: document.cookie,
    localStorage: localStorage.length,
    sessionStorage: sessionStorage.length,
  }));
  expect(storage).toEqual({ cookie: "", localStorage: 0, sessionStorage: 0 });
  expect(await page.context().cookies()).toEqual([]);
});

test("a failed request raises no error on the page", async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.addInitScript(() => {
    window.__rejections = [];
    window.addEventListener("unhandledrejection", (e) => {
      window.__rejections.push(String(e.reason));
      if (e.reason?.message === "sentinel") e.preventDefault();
    });
  });
  await track(page, { fail: true });
  await page.goto("/page.html");
  // The script tried to send its page view, and the request failed.
  expect(await calls(page)).toHaveLength(1);

  await page.evaluate(() => mieru("signup"));
  expect(await calls(page)).toHaveLength(2);

  // Unhandled rejections are reported in a later task, in the order they happened.
  // Once a sentinel rejected after both requests is reported, any rejection from the
  // requests has been reported too. The sentinel's preventDefault keeps it out of
  // pageerror.
  await page.evaluate(() => {
    Promise.reject(new Error("sentinel"));
  });
  await page.waitForFunction(
    () => window.__rejections.includes("Error: sentinel"),
    undefined,
    { timeout: 5_000 },
  );

  expect(await page.evaluate(() => window.__rejections)).toEqual([
    "Error: sentinel",
  ]);
  expect(pageErrors).toEqual([]);
});
