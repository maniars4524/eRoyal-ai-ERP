// Dashboard slides: Freight / CHA (Customs) / Transport for Admin, only CHA for a CHA user, only Transport for a Transport user.
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
  const org = await j("POST", "/api/organizations", { code: "DS" + tag.slice(-6).toUpperCase(), name: "DashSl " + tag, admin_email: `ds${tag}@t.test`, admin_username: "ds" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ds" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  const bk = (await j("POST", "/api/bookings", { mode: "Sea", direction: "Export", shipment_type: "FCL", booking_type: "Direct", party_name: "Acme", carrier: "MSC", pol: "INMAA", pod: "BEANR", data: { services: "Freight, CHA", pcs: "10", consignee: "X" } }, token)).body;
  await j("POST", `/api/bookings/${bk.id}/confirm`, null, token);
  w.go("dashboard"); await sleep(1500);
  const dots = () => Array.from(d.querySelectorAll(".dash-dot")).filter(x => x.style.display !== "none").map(x => x.title);
  const active = () => (d.querySelector(".dash-slide.active .ct") || {}).textContent || "";
  const go = async i => { d.querySelector(`.dash-dot[data-idx="${i}"]`).click(); await sleep(1200); };
  check("Admin has the Freight, CHA (Customs) and Transport slides", ["Freight", "CHA (Customs)", "Transport"].every(t => dots().includes(t)), dots().join());
  const idx = t => Number(Array.from(d.querySelectorAll(".dash-dot")).find(x => x.title === t).dataset.idx);
  await go(idx("CHA (Customs)"));
  const cha = d.querySelector(".dash-slide.active");
  check("the CHA slide shows Draft / Pending / Filed / Delayed / Completed with this company's figures", /CHA/.test(active()) && ["Draft", "Pending", "Filed", "Delayed", "Completed"].every(t => cha.textContent.includes(t)) && /Sea Export/.test(cha.textContent) && Array.from(cha.querySelectorAll(".kpi")).some(k => /Draft/i.test(k.textContent) && /1/.test(k.textContent)), cha.textContent.replace(/\s+/g, " ").slice(0, 200));
  await go(idx("Freight"));
  check("the Freight slide counts the freight jobs", /Freight jobs/i.test(d.querySelector(".dash-slide.active").textContent) && /Sea Export/.test(d.querySelector(".dash-slide.active").textContent));
  await go(idx("Transport"));
  check("the Transport slide is shown", /Transport jobs/i.test(d.querySelector(".dash-slide.active").textContent));
  const view = r => { d.querySelector(`.hdr-menu-item[data-role="${r}"]`).click(); return sleep(1500); };
  await view("CHA");
  check("a CHA user sees only the CHA slide, first", dots().join() === "CHA (Customs)" && /CHA/.test(active()), dots().join() + " / " + active());
  await view("Transport");
  check("a Transport user sees only the Transport slide", dots().join() === "Transport" && /Transport/.test(active()), dots().join() + " / " + active());
  await view("Operations");
  check("Operations sees the chart slides and Freight, not CHA or Transport", dots().includes("Freight") && !dots().includes("CHA (Customs)") && !dots().includes("Transport"), dots().join());
  await view("Admin");
  check("Admin gets all of them back", dots().includes("CHA (Customs)") && dots().includes("Transport"));
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
