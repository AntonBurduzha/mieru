import { test, expect } from "@playwright/test";
import { ORIGIN, calls, event, pageview, sent, track } from "./helpers.js";

const PAGE = `${ORIGIN}/page.html`;
const FILE_PAGE = new URL("fixtures/file.html", import.meta.url).href;

// On the queue page, the stub keeps its calls in mieru.q. A mieru without q is the
// real script's, so the script ran and drained the queue.
async function expectRealMieru(page) {
  expect(
    await page.evaluate(
      () =>
        typeof window.mieru === "function" && !Object.hasOwn(window.mieru, "q"),
    ),
  ).toBe(true);
}

test("with navigator.webdriver true, nothing is sent from load, the queue, pushState or mieru()", async ({
  page,
}) => {
  await track(page, { webdriver: true });
  await page.goto("/queue.html");
  await expectRealMieru(page);

  await page.evaluate(() => {
    history.pushState({}, "", "/pricing");
    mieru("signup", { props: { plan: "pro" } });
  });

  expect(await calls(page)).toEqual([]);
});

test("on a file: page, nothing is sent from load or mieru()", async ({
  page,
}) => {
  await track(page);
  await page.goto(FILE_PAGE);
  // No stub on this page: window.mieru exists only if the script ran.
  expect(await page.evaluate(() => typeof window.mieru)).toBe("function");

  await page.evaluate(() => mieru("signup", { props: { plan: "pro" } }));

  expect(await calls(page)).toEqual([]);
});

test("with mieru_ignore set to true in localStorage, nothing is sent and the flag is left as it was", async ({
  page,
}) => {
  await track(page);
  await page.addInitScript(() => localStorage.setItem("mieru_ignore", "true"));
  await page.goto("/queue.html");
  await expectRealMieru(page);

  await page.evaluate(() => {
    history.pushState({}, "", "/pricing");
    mieru("signup", { props: { plan: "pro" } });
  });

  expect(await calls(page)).toEqual([]);
  expect(await page.evaluate(() => ({ ...localStorage }))).toEqual({
    mieru_ignore: "true",
  });
});

test("mieru_ignore set to anything but true does not stop tracking", async ({
  page,
}) => {
  await track(page);
  await page.addInitScript(() => localStorage.setItem("mieru_ignore", "false"));
  await page.goto("/page.html");

  expect(await sent(page)).toEqual([pageview(PAGE)]);
});

test("when localStorage throws, the script still sends the page view and events", async ({
  page,
}) => {
  await track(page);
  // As in a browser that blocks site data: reading window.localStorage throws.
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new DOMException("The operation is insecure.", "SecurityError");
      },
    });
  });
  await page.goto("/page.html");
  expect(await sent(page)).toEqual([pageview(PAGE)]);

  await page.evaluate(() => mieru("signup"));

  expect(await sent(page)).toEqual([pageview(PAGE), event("signup", PAGE)]);
});
