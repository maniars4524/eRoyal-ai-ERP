// Static navigation audit of esway-erp.html: every go("x") / data-go="x" / menu go:"x" must have a screen (#s-x) and a SCREEN_META entry.
const fs = require("fs");
const h = fs.readFileSync("../esway-erp.html", "utf8");
const screens = new Set([...h.matchAll(/<div id="s-([a-z0-9-]+)"/g)].map(m => m[1]));
// screens created at run time by kits
for (const m of h.matchAll(/sec\.id\s*=\s*"s-(?:net-)?([a-z-]+)"|sec\.id = "s-([a-z-]+)"/g)) if (m[2]) screens.add(m[2]);
["net-airlines", "net-sea-lines", "net-iata", "net-cfs", "net-stock-lines", "vendor-master"].forEach(s => screens.add(s));
const metaBlock = h.slice(h.indexOf("const SCREEN_META = {"), h.indexOf("};", h.indexOf("const SCREEN_META = {")));
const meta = new Set([...metaBlock.matchAll(/^\s*"([a-z0-9-]+)":\{/gm)].map(m => m[1]));
["net-airlines", "net-sea-lines", "net-iata", "net-cfs", "net-stock-lines", "vendor-master"].forEach(s => meta.add(s));
const targets = new Map();
const add = (t, where) => { if (!t || /[${}+]/.test(t)) return; if (!targets.has(t)) targets.set(t, new Set()); targets.get(t).add(where); };
for (const m of h.matchAll(/data-go="([^"]+)"/g)) add(m[1], "data-go");
for (const m of h.matchAll(/\bgo\(\s*["']([a-z0-9-]+)["']/g)) add(m[1], "go()");
for (const m of h.matchAll(/\bgo:\s*"([a-z0-9-]+)"/g)) add(m[1], "menu go:");
const alias = new Set(["org-detail", "debit-note-detail", "purchase-invoice-detail"]);
const dead = [], noMeta = [];
for (const [t, w] of targets) { if (!screens.has(t) && !alias.has(t)) dead.push(t + " <- " + [...w].join(",")); else if (!meta.has(t) && !alias.has(t)) noMeta.push(t); }
console.log("screens:", screens.size, "targets:", targets.size);
console.log("DEAD destinations (no screen):", dead.length ? "\n  " + dead.join("\n  ") : "none");
console.log("destinations without a SCREEN_META title/breadcrumb:", noMeta.length ? "\n  " + noMeta.join("\n  ") : "none");
const unreachable = [...screens].filter(s => !targets.has(s) && !/-new$|-detail$|print$|^org-new/.test(s));
console.log("screens nothing navigates to (check):", unreachable.join(", ") || "none");
