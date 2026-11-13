# Tracking script

> Part of the [system design](README.md).

```html
<script defer data-domain="example.com" src="https://in.mieru.example/js/p.js"></script>
```

| Aspect                    | Design                                                                                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Size budget**           | 1 KB gzipped or less, enforced in CI. For reference: Plausible's old base script is 1.3 KB, its current one about 2.5 KB, Google Analytics over 100 KB |
| **Transport**             | `fetch(url, {method: 'POST', keepalive: true, headers: {'Content-Type': 'text/plain'}})`. `keepalive` lets the request finish while the page unloads   |
| **Page views**            | One on load. For single-page apps, wrap `history.pushState` and listen for `popstate`; skip if the path did not change                                 |
| **Custom events**         | `window.mieru(name, {props})`. Calls made before the script loads are queued                                                                           |
| **Refuses to track**      | `file:` pages, automated browsers (`navigator.webdriver`), and browsers where the owner set an opt-out flag in local storage (read, never written)     |
| **Stores in the browser** | Nothing                                                                                                                                                |
| **Built with**            | Plain JavaScript, minified with terser; no framework, no dependencies                                                                                  |

Left out of the base script to keep the size: outbound links, file downloads, scroll depth, form tracking. Ad blockers block known analytics domains; a guide for proxying the script through the customer's own domain is a stretch item.
