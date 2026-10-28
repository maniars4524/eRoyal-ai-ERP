// Dashboard: Active Jobs -> Freight / CHA / Transport -> breakup -> filtered list.
// CHA screen: Back + Create Job (Export / Import x Sea / Air), dashboard cards (Draft, Pending, Filed, Delayed, Completed ...), register, job window with stages.
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
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "DR" + tag.slice(-6).toUpperCase(), name: "Drill " + tag, admin_email: `dr${tag}@t.test`, admin_username: "dr" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "dr" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  const mk = async services => { const b = (await j("POST", "/api/bookings", { mode: "Sea", direction: "Export", shipment_type: "FCL", booking_type: "Direct", party_name: "Drill Co", carrier: "MSC", pol: "INMAA", pod: "BEANR", data: { services, pcs: "5", consignee: "Buyer" } }, token)).body; await j("POST", `/api/bookings/${b.id}/confirm`, null, token); return b; };
  const b1 = await mk("Freight, CHA"), b2 = await mk("Freight");
  await j("POST", `/api/bookings/${b1.id}/jobs`, { service: "Freight" }, token); await j("POST", `/api/bookings/${b1.id}/jobs`, { service: "CHA" }, token); await j("POST", `/api/bookings/${b2.id}/jobs`, { service: "Freight" }, token);
  const sum = (await j("GET", "/api/dashboard/summary", null, token)).body.jobs;
  check("the server counts freight 2, CHA 1, transport 0 with no double counting", sum.categories.freight.active === 2 && sum.categories.cha.active === 1 && sum.categories.transport.active === 0 && sum.active === 3, JSON.stringify(sum.categories));
  w.go("dashboard"); await sleep(3500);
  const panel = () => d.getElementById("kpiDetailPanel");
  const cat = k => panel().querySelector(`[data-dash-cat="${k}"]`);
  check("the Active Jobs panel first shows Freight, CHA and Transport cards with their counts", ["freight", "cha", "transport"].every(k => !!cat(k)) && /2/.test(cat("freight").textContent) && /1/.test(cat("cha").textContent) && /0/.test(cat("transport").textContent), panel().textContent.slice(0, 200));
  cat("freight").click(); await sleep(500);
  check("Freight Jobs shows the shipment-type breakup and keeps the three cards visible", /Shipment Type Wise Breakup/.test(panel().textContent) && /Sea Export/.test(panel().textContent) && !!cat("cha") && cat("freight").getAttribute("aria-pressed") === "true");
  cat("cha").click(); await sleep(500);
  check("CHA Jobs replaces it with the CHA operational breakup", /CHA Jobs — Operational Breakup/.test(panel().textContent) && /Total CHA jobs/.test(panel().textContent) && /BE \/ SB sent/.test(panel().textContent) && /Created \/ documentation pending/.test(panel().textContent) && !/Shipment Type Wise/.test(panel().textContent));
  const link = Array.from(panel().querySelectorAll("[data-dash-open='cha']")).find(a => a.closest("tr").textContent.includes("Created / documentation pending") && a.textContent === "1");
  check("a CHA count is a link", !!link);
  link.click(); await sleep(2500);
  check("clicking it opens the CHA screen filtered to that group", d.getElementById("s-svc-cha").classList.contains("on") && /Created/.test(d.getElementById("svcChaRoot").textContent) && w.ChaKit.state.group === "draft", w.ChaKit.state.group);
  w.navBack(); await sleep(2500);
  cat("transport").click(); await sleep(500);
  check("Transport Jobs shows its own breakup or the empty state", /No transport jobs yet|Transport Jobs — Operational Breakup/.test(panel().textContent));
  panel().querySelector('[data-dash-cat=""]').click(); await sleep(500);
  check("Back to Active Jobs returns to the three cards", /Select Job Category/.test(panel().textContent) && !/Breakup/.test(panel().textContent));
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
