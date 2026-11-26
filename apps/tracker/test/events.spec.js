import { test, expect } from "@playwright/test";
import { ORIGIN, event, pageview, sent, track } from "./helpers.js";

const PAGE = `${ORIGIN}/page.html`;
const QUEUE = `${ORIGIN}/queue.html`;

test("mieru() with props sends one event with every prop value as a string", async ({
  page,
}) => {
  await track(page);
  await page.goto("/page.html");
  await page.evaluate(() =>
    mieru("signup", { props: { plan: "pro", seats: 3 } }),
  );

  expect(await sent(page)).toEqual([
    pageview(PAGE),
    event("signup", PAGE, { plan: "pro", seats: "3" }),
  ]);
});

test("mieru() with only a name sends an empty p", async ({ page }) => {
  await track(page);
  await page.goto("/page.html");
  await page.evaluate(() => mieru("download"));

  expect(await sent(page)).toEqual([pageview(PAGE), event("download", PAGE)]);
});

test("calls queued before the script loaded are sent once each, in order, after the page view", async ({
  page,
}) => {
  await track(page);
  await page.goto("/queue.html");

  expect(await sent(page)).toEqual([
    pageview(QUEUE),
    event("signup", QUEUE, { plan: "pro" }),
    event("download", QUEUE),
  ]);
});

test("after the queue is drained, mieru() sends at once and the queue is not sent again", async ({
  page,
}) => {
  await track(page);
  await page.goto("/queue.html");
  await page.evaluate(() => mieru("later"));

  expect(await page.evaluate(() => Object.hasOwn(window.mieru, "q"))).toBe(
    false,
  );
  expect(await sent(page)).toEqual([
    pageview(QUEUE),
    event("signup", QUEUE, { plan: "pro" }),
    event("download", QUEUE),
    event("later", QUEUE),
  ]);
});
