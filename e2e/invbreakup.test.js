// Invoice from job charges: one line per charge with its container-wise breakup and a pop-up.
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
  const org = await j("POST", "/api/organizations", { code: "IB" + tag.slice(-6).toUpperCase(), name: "InvBu " + tag, admin_email: `ib${tag}@t.test`, admin_username: "ib" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ib" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);

  const no = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR" }, token)).body.job_number;
  const side = rate => ({ rate: String(rate), ccy: "USD", exch: "88.98", tax_type: "Taxable", tax_pct: "5", party: "Group Co", use_party: true });
  const cost = { rate: "0", ccy: "INR", exch: "1", tax_type: "Non-Taxable" };
  await j("PUT", `/api/shipments/by-number/${encodeURIComponent(no)}/charges`, { lines: [
    { charge_name: "OCEAN FEIGHT CHARGES", description: "OCEAN FEIGHT CHARGES — 40HC", uom: "Container", qty: "1", buy: cost, sell: side(2500) },
    { charge_name: "OCEAN FEIGHT CHARGES", description: "OCEAN FEIGHT CHARGES — 20GP", uom: "Container", qty: "2", buy: cost, sell: side(1600) }] }, token);
  const inv = (await j("POST", "/api/documents/from-job", { job_number: no, doc_type: "invoice", party: "Group Co" }, token)).body;
  w.BK.openDoc(inv.id); await sleep(3000);
  const rows = Array.from(d.querySelectorAll("#s-invoice-new tbody tr")).filter(r => r.querySelector('[data-f="charge_name"]'));
  check("the charge entered per container type is ONE line on the invoice", rows.length === 1, rows.length);
  const btn = d.querySelector("[data-bk-bu]");
  check("the line has a breakup button", !!btn && /2 types/.test(btn.textContent), btn && btn.textContent);
  check("its description holds the whole breakup", /40HC x1 @ 2500/.test(d.querySelector('[data-f="description"]').value) && /20GP x2 @ 1600/.test(d.querySelector('[data-f="description"]').value), d.querySelector('[data-f="description"]').value);
  btn.click(); await sleep(400);
  const modal = (d.getElementById("modalBox") || d.querySelector(".modal-box") || { textContent: "" }).textContent.replace(/\s+/g, " ");
  check("the pop-up shows each container type with quantity, rate and amount, and the total", /40HC/.test(modal) && /20GP/.test(modal) && /2,500\.00/.test(modal) && /3,200\.00/.test(modal) && /5,700\.00 USD/.test(modal), modal.slice(0, 260));
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
