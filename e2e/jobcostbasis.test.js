// Job cost: the Basis options fill the quantity from the job's own measurements and percentage lines come from the calculation service.
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
  const org = await j("POST", "/api/organizations", { code: "JB" + tag.slice(-6).toUpperCase(), name: "JobCost " + tag, admin_email: `jb${tag}@t.test`, admin_username: "jb" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "jb" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);

  const made = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR" }, token)).body;
  const no = made.job_number;
  await j("PUT", `/api/jobs/${encodeURIComponent(no)}/details/cargo`, { data: { pkgs: "10", gwt: "2000", nwt: "1900", vol: "3" } }, token);
  const ln = (name, uom, buy, sell) => ({ charge_name: name, uom, qty: "1", buy: { rate: String(buy), ccy: "INR", exch: "1", tax_type: "Non-Taxable" }, sell: { rate: String(sell), ccy: "INR", exch: "1", tax_type: "Non-Taxable" } });
  await j("PUT", `/api/shipments/by-number/${encodeURIComponent(no)}/charges`, { lines: [ln("Ocean Freight", "Per Shipment", 1000, 1400), ln("Handling", "Per Shipment", 100, 150), ln("BAF", "Per Shipment", 0, 0)] }, token);
  w.go("job-detail", { jobNo: no }); await sleep(3000);
  const sel = (i) => Array.from(d.querySelectorAll('select[data-jc-f="uom"]'))[i];
  const num = (i) => Array.from(d.querySelectorAll('input[data-jc-f="qty"]'))[i];
  const opts = Array.from(sel(0).options).map(o => o.value);
  check("the job cost Basis list keeps its values and adds the Basis options", ["Per Container", "Per Chargeable KG", "Per KG"].every(x => opts.includes(x)) && ["Per Package", "By Gross Wt", "By Chg Wt", "By Volume", "% of Freight", "% of Other Charges", "Per Vehicle", "By Gross Wt/Volume", "By Net Wt"].every(x => opts.includes(x)), opts.join());
  const pick = async (i, v) => { sel(i).value = v; sel(i).dispatchEvent(new w.Event("input", { bubbles: true })); sel(i).dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(1300); };
  await pick(1, "Per Package");
  check("Per Package takes the packages of the job into the quantity", num(1).value === "10", num(1).value);
  await pick(1, "By Gross Wt"); check("By Gross Wt takes the job's gross weight", num(1).value === "2000", num(1).value);
  await pick(1, "By Net Wt"); check("By Net Wt takes the job's net weight", num(1).value === "1900", num(1).value);
  await pick(1, "By Volume"); check("By Volume takes the job's CBM", num(1).value === "3", num(1).value);
  await pick(1, "Per Package");
  // BAF as a percentage of the freight
  await pick(2, "% of Freight");
  const rate = (i, side) => Array.from(d.querySelectorAll(`input[data-jc-f="${side}.rate"]`))[i];
  rate(2, "buy").value = "10"; rate(2, "buy").dispatchEvent(new w.Event("input", { bubbles: true })); rate(2, "sell").value = "10"; rate(2, "sell").dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(1300);
  d.getElementById("jc_saveBtn").click(); await sleep(2500);
  const saved = (await j("GET", `/api/shipments/by-number/${encodeURIComponent(no)}/charges`, null, token)).body.lines;
  const by = Object.fromEntries(saved.map(l => [l.charge_name, l]));
  check("saved: the percentage line holds 10% of the cost freight and 10% of the sell freight", by["BAF"].buy.amount === "100.00" && by["BAF"].sell.amount === "140.00" && by["BAF"].buy.pct === "10.000", JSON.stringify(by["BAF"]));
  check("saved: Per Package quantity 10 and the amounts follow", by["Handling"].qty === "10.000" && by["Handling"].sell.amount === "1500.00", JSON.stringify(by["Handling"]));
  w.go("dashboard"); await sleep(800); w.go("job-detail", { jobNo: no }); await sleep(3000);
  check("reopened: the percentage line shows 10 as its rate and keeps its Basis", rate(2, "buy").value === "10" && sel(2).value === "% of Freight" && num(1).value === "10", rate(2, "buy").value + " " + sel(2).value);
  const again = (await j("PUT", `/api/shipments/by-number/${encodeURIComponent(no)}/charges`, { lines: saved.map(l => ({ charge_name: l.charge_name, uom: l.uom, qty: l.qty, buy: { rate: l.buy.pct || l.buy.rate, ccy: "INR", exch: "1", tax_type: "Non-Taxable" }, sell: { rate: l.sell.pct || l.sell.rate, ccy: "INR", exch: "1", tax_type: "Non-Taxable" } })) }, token)).body.lines;
  check("saving again never duplicates charges", again.length === 3, again.length);
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
