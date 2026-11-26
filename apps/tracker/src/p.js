// Mieru's tracking script: a page view on load and on single-page-app navigation,
// and custom events through window.mieru(name, {props}). It stores nothing in the
// browser, and sends nothing from file: pages, automated browsers, or a browser
// whose owner set localStorage.mieru_ignore to "true".
(() => {
  const script = document.currentScript;
  // Events go to the host the script came from, for example https://in.mieru.example/api/event
  const endpoint = new URL("/api/event", script.src);
  const domain = script.getAttribute("data-domain") || location.hostname;
  // Calls made before the script loaded, kept by the stub in the site's snippet
  const queue = window.mieru?.q || [];
  const refused =
    location.protocol === "file:" || navigator.webdriver || ignored();
  let lastPath;

  function ignored() {
    // Reading storage throws where the browser blocks it (sandboxed frames, some privacy settings)
    try {
      return localStorage.getItem("mieru_ignore") === "true";
    } catch {
      return false;
    }
  }

  function send(name, props) {
    if (refused) return;
    const p = {};
    // Ingest accepts only string values
    for (const key in props) p[key] = String(props[key]);
    fetch(endpoint, {
      method: "POST",
      // The request completes even when the page is being closed
      keepalive: true,
      // A "simple" request: no CORS preflight before every event
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({
        n: name,
        d: domain,
        u: location.href,
        r: document.referrer || null,
        p,
      }),
      // An error on the customer's page helps nobody, and the script never retries
    }).catch(() => {});
  }

  function pageview() {
    if (location.pathname === lastPath) return;
    lastPath = location.pathname;
    send("pageview");
  }

  window.mieru = (name, options) => send(name, options?.props);

  const pushState = history.pushState;
  history.pushState = function (...args) {
    pushState.apply(this, args);
    pageview();
  };
  addEventListener("popstate", pageview);

  pageview();
  for (const args of queue) window.mieru(...args);
})();
