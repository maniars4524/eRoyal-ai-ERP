// Headless UI check: eight themes on shared tokens, instant application, selection indicator, persistence (browser + account), readable contrast, charts.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => {
  const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: r.status === 204 ? null : await r.json().catch(() => null) };
};
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const lum = h => { const c = [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255).map(v => v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4); return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]; };
const cr = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
const NAMES = ["Navy Blue & White", "Brown & Cream", "Charcoal & Silver", "Forest Green & Ivory", "Royal Purple & White", "Teal & Soft White", "Burgundy & Champagne", "Midnight Black & Gold"];

(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "TH" + tag.slice(-6).toUpperCase(), name: "Themes " + tag, admin_email: `th${tag}@t.test`, admin_username: "th" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "T" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "th" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const open = (store) => JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); if (store) Object.entries(store).forEach(([k, v]) => w.localStorage.setItem(k, v)); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  let dom = await open(); let w = dom.window, d = w.document;
  await sleep(2500);
  const q$ = s => d.querySelector(s);
  const text = s => ((q$(s) || {}).textContent || "").replace(/\s+/g, " ");
  const css = Array.from(d.querySelectorAll("style")).map(s => s.textContent).join("\n");

  w.go("themes"); await sleep(600);
  const cards = Array.from(d.querySelectorAll("#themeGrid [data-theme-pick]"));
  check("the Themes screen shows eight preview cards with their names", cards.length === 8 && NAMES.every(n => cards.some(c => c.textContent.includes(n))), cards.map(c => c.textContent.slice(0, 20)).join("|"));
  check("every card has a colour palette strip and a mini preview", cards.every(c => c.querySelectorAll(".theme-sw span").length === 7 && !!c.querySelector("[data-theme-preview] div")));
  check("the default theme is marked Selected", cards[0].classList.contains("sel") && /Selected/.test(cards[0].textContent) && cards.filter(c => c.classList.contains("sel")).length === 1);

  // design tokens: no hard-coded white surfaces left in the stylesheet, one variable block per theme
  check("the stylesheet defines the eight themes as variable sets", ["navy", "brown", "charcoal", "forest", "purple", "teal", "burgundy", "midnight"].every(t => css.includes(`:root[data-theme="${t}"]{`)));
  check("surfaces and text use shared variables (no hard-coded white backgrounds)", !/background(-color)?:\s*#fff\b/i.test(css), (css.match(/[^{};]*background(-color)?:\s*#fff\b[^;}]*/i) || [""])[0]);
  check("every theme sets every token the screens read", ["navy", "brown", "charcoal", "forest", "purple", "teal", "burgundy", "midnight"].every(t => { const m = css.match(new RegExp(':root\\[data-theme="' + t + '"\\]\\{([^}]*)\\}')); return m && ["--n:", "--n2:", "--n3:", "--bl:", "--bl2:", "--blt:", "--on-bl:", "--bl-ink:", "--g0:", "--g1:", "--g2:", "--g3:", "--g4:", "--g5:", "--g6:", "--g7:", "--g8:", "--wh:"].every(v => m[1].includes(v)); }));

  // readable on every theme
  const bad = [];
  for (const t of w.ThemeKit.THEMES) {
    const p = t.palette;
    for (const [label, f, b, need] of [["body text", p.g8, p.wh, 7], ["text on page", p.g8, p.g0, 7], ["secondary text", p.g6, p.wh, 4.5], ["muted text", p.g5, p.wh, 4.5], ["header text", "#FFFFFF", p.n, 7], ["header sub-text", p.g4, p.n, 3], ["button text", p.on, p.bl, 4.5], ["link", p.bl, p.wh, 4.5], ["badge text", p.ink, p.blt, 7]]) {
      const v = cr(f, b); if (v < need) bad.push(`${t.id}: ${label} ${v.toFixed(2)} < ${need}`);
    }
  }
  check("text contrast is readable on all eight themes (WCAG AA or better)", bad.length === 0, bad.join("; "));

  // choosing a theme: instant, whole app, indicator, toast, persistence
  for (const [i, t] of w.ThemeKit.THEMES.entries()) {
    d.querySelectorAll("#toastStack .toast").forEach(x => x.remove());
    cards[i].click(); await sleep(250);
    const ok = d.documentElement.getAttribute("data-theme") === t.id && cards[i].classList.contains("sel") && cards.filter(c => c.classList.contains("sel")).length === 1 && /Selected/.test(cards[i].textContent) && w.localStorage.getItem("esway_theme") === t.id;
    if (!ok) check("theme " + t.name + " applies and is marked", false, d.documentElement.getAttribute("data-theme"));
  }
  check("each of the eight themes applies instantly and shows a single clear selection", d.documentElement.getAttribute("data-theme") === "midnight");
  d.querySelectorAll("#toastStack .toast").forEach(x => x.remove());
  cards[1].click(); await sleep(800);
  check("a green success toast confirms the theme", Array.from(d.querySelectorAll("#toastStack .toast")).some(x => x.classList.contains("success") && /Theme Applied Successfully!/.test(x.textContent)));
  check("the current theme is named at the top of the screen", /Brown & Cream/.test(text("#themeNow")));
  check("the choice is saved on the user's account", (await j("GET", "/api/auth/me", null, token)).body.theme === "brown");
  // it stays while moving around the app, and new screens read the same tokens
  w.go("dashboard"); await sleep(500); w.go("job-list"); await sleep(500); w.go("quotation"); await sleep(500);
  check("the theme stays while navigating between screens", d.documentElement.getAttribute("data-theme") === "brown");

  // after a refresh / reopening
  dom.window.close();
  dom = await open({ esway_theme: "teal" }); w = dom.window; d = w.document; await sleep(2500);
  check("after reopening, the browser's saved theme is applied before the screen is drawn", d.documentElement.getAttribute("data-theme") === "teal");
  w.ThemeKit.fromAccount({ theme: "forest" }); await sleep(100);
  check("after signing in on another computer the account's theme is applied", d.documentElement.getAttribute("data-theme") === "forest" && w.localStorage.getItem("esway_theme") === "forest");
  w.go("themes"); await sleep(500);
  check("and the Themes screen marks it", Array.from(d.querySelectorAll("#themeGrid .sel")).length === 1 && /Forest Green/.test(Array.from(d.querySelectorAll("#themeGrid .sel"))[0].textContent));
  // a bad stored value falls back safely
  w.ThemeKit.apply("hot-pink"); check("an unknown theme name falls back to the default", d.documentElement.getAttribute("data-theme") === "navy");
  // paper documents keep a white page whatever the theme
  check("printed documents stay on white paper in every theme", /\[id\$="PrintZone"\][^{]*\{[^}]*--wh:#fff/.test(css) && /@media print\{:root,:root\[data-theme\]\{--wh:#fff/.test(css));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
