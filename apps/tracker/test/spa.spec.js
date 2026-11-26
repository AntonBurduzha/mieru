import { test, expect } from "@playwright/test";
import { ORIGIN, pageview, sent, track } from "./helpers.js";

const PAGE = `${ORIGIN}/page.html`;

// Resolves after the next popstate has reached every listener, the script's included:
// the script listens from load, so its listener runs before this one.
function afterPopstate(page, move) {
  return page.evaluate(
    (move) =>
      new Promise((resolve) => {
        window.addEventListener("popstate", () => resolve(), { once: true });
        if (move === "back") history.back();
        else location.hash = move;
      }),
    move,
  );
}

test.beforeEach(async ({ page }) => {
  await track(page);
  await page.goto("/page.html");
});

test("pushState to a new path sends one page view with the new URL", async ({
  page,
}) => {
  await page.evaluate(() => history.pushState({}, "", "/pricing?plan=pro"));

  expect(await sent(page)).toEqual([
    pageview(PAGE),
    pageview(`${ORIGIN}/pricing?plan=pro`),
  ]);
});

test("pushState that changes only the query string sends no page view", async ({
  page,
}) => {
  await page.evaluate(() => history.pushState({}, "", "?tab=2"));

  expect(await sent(page)).toEqual([pageview(PAGE)]);
});

test("pushState that changes only the hash sends no page view", async ({
  page,
}) => {
  await page.evaluate(() => history.pushState({}, "", "#plans"));

  expect(await sent(page)).toEqual([pageview(PAGE)]);
});

test("following a hash link fires popstate on the same path and sends no page view", async ({
  page,
}) => {
  await afterPopstate(page, "faq");

  expect(page.url()).toBe(`${PAGE}#faq`);
  expect(await sent(page)).toEqual([pageview(PAGE)]);
});

test("popstate back to another path sends one page view", async ({ page }) => {
  await page.evaluate(() => history.pushState({}, "", "/pricing"));
  expect(await sent(page)).toEqual([
    pageview(PAGE),
    pageview(`${ORIGIN}/pricing`),
  ]);

  await page.evaluate(() => history.back());
  await page.waitForFunction(() => window.__sent.length >= 3, undefined, {
    timeout: 5_000,
  });

  expect(await sent(page)).toEqual([
    pageview(PAGE),
    pageview(`${ORIGIN}/pricing`),
    pageview(PAGE),
  ]);
});

test("popstate back over a query-only change sends no page view", async ({
  page,
}) => {
  await page.evaluate(() => history.pushState({}, "", "?tab=2"));
  await afterPopstate(page, "back");

  expect(page.url()).toBe(PAGE);
  expect(await sent(page)).toEqual([pageview(PAGE)]);
});
