// Shared helpers for the tracker's browser tests.
import { fileURLToPath } from "node:url";

export const ORIGIN = "http://127.0.0.1:5180";

// The built script, the file every browser test loads.
export const DIST_SCRIPT = fileURLToPath(
  new URL("../dist/p.js", import.meta.url),
);

// Records every fetch() call in window.__sent before any page script runs, and sets
// navigator.webdriver explicitly: Playwright's own default is true, which would make
// the script refuse. With `fail`, the recorded call returns a rejected promise
// instead of reaching the network, as a request does when it fails.
export async function track(page, { webdriver = false, fail = false } = {}) {
  await page.addInitScript(
    ({ webdriver, fail }) => {
      window.__sent = [];
      const realFetch = window.fetch.bind(window);
      window.fetch = (url, init = {}) => {
        window.__sent.push({
          url: String(url),
          init: {
            method: init.method,
            keepalive: init.keepalive,
            // Lower-cased names, whether the script passed an object or Headers.
            headers: Object.fromEntries(new Headers(init.headers)),
            body: init.body,
          },
        });
        if (fail) return Promise.reject(new TypeError("Failed to fetch"));
        return realFetch(url, init);
      };
      Object.defineProperty(Navigator.prototype, "webdriver", {
        configurable: true,
        get: () => webdriver,
      });
    },
    { webdriver, fail },
  );
}

// Every recorded fetch() call: { url, init: { method, keepalive, headers, body } }.
export async function calls(page) {
  return page.evaluate(() => window.__sent);
}

// The parsed body of every recorded fetch() call, in order.
export async function sent(page) {
  return (await calls(page)).map((call) => JSON.parse(call.init.body));
}

// The body the script sends for a page view of `url` on example.com, with no referrer.
export function pageview(url, { d = "example.com", r = null } = {}) {
  return { n: "pageview", d, u: url, r, p: {} };
}

// The body the script sends for a custom event on example.com, with no referrer.
export function event(name, url, p = {}) {
  return { n: name, d: "example.com", u: url, r: null, p };
}
