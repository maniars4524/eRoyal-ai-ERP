// Headless UI check #4: Job charge grid autosaves to PostgreSQL, reloads, and drives Job Detail totals.
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
  const org = await j("POST", "/api/organizations", { code: "CH" + tag.slice(-6).toUpperCase(), name: "Charges " + tag, admin_email: `ch${tag}@t.test`, admin_username: "ch" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ch" }, dev.body.access_token);
  const login = await j("POST", "/api/auth/login", { identifier: "ch" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number });
  const token = login.body.access_token;
  await j("POST", "/api/masters/hsn", { code: "996512", name: "Water transport", data: { gst_rate: "18" } }, token);
  await j("POST", "/api/masters/charge", { code: "OFR", name: "Ocean Freight", data: { hsn_code: "996512", gst_percent: "18" } }, token);
  await j("POST", "/api/masters/charge", { code: "DOC", name: "Documentation Charges", data: { hsn_code: "998399", gst_percent: "18" } }, token);
  const job = await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR" }, token);
  const jobNo = job.body.job_number;

  const dom = await JSDOM.fromURL(BASE + "/erp", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE).href, o);
      w.sessionStorage.setItem("esway_access_token", token);
      w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {};
    },
  });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const toastsNow = () => Array.from(d.querySelectorAll("#toastStack .toast")).map(x => x.textContent.replace(/\s+/g, " ").trim());
  const toasts = { _clear() { d.querySelectorAll("#toastStack .toast").forEach(x => x.remove()); }, some(f) { return toastsNow().some(f); }, join(s) { return toastsNow().join(s); } };
  const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
  const setf = (rowId, f, v, ev) => { const el = d.querySelector(`[data-jc-id="${rowId}"][data-jc-f="${f}"]`); el.value = v; fire(el, ev || "input"); };
  const rowIds = () => Array.from(d.querySelectorAll("#jc_tbody tr[data-jc-row]")).map(r => Number(r.dataset.jcRow));

  w.go("job-detail", { jobNo }); await sleep(1500);
  check("charge grid starts empty (no dummy default rows)", rowIds().length === 0, rowIds().length);
  const emptyMsg = d.querySelector("#jc_tbody").textContent;
  check("empty-state message shown", /No charges added yet/.test(emptyMsg), emptyMsg);

  d.getElementById("jc_addRowBtn").click(); await sleep(200);
  let id = rowIds()[0];
  const opts = Array.from(d.querySelector(`[data-jc-id="${id}"][data-jc-f="chargeName"]`).options).map(o => o.text).join(",");
  check("charge dropdown is fed by the Charge Master", opts.split(",").sort().join(",") === "Documentation Charges,Ocean Freight,— Select —", opts);
  setf(id, "chargeName", "Ocean Freight", "change"); await sleep(200); id = rowIds()[0];
  check("choosing a charge auto-fills HSN/SAC and tax % from the master", d.querySelector(`[data-jc-id="${id}"][data-jc-f="hsn"]`).value === "996512" && d.querySelector(`[data-jc-id="${id}"][data-jc-f="sell.taxPct"]`).value === "18");
  setf(id, "qty", "2"); setf(id, "buy.ccy", "USD", "change"); await sleep(150); id = rowIds()[0];
  setf(id, "buy.exch", "83.1"); setf(id, "buy.rate", "680"); setf(id, "buy.taxType", "Non-Taxable", "change"); await sleep(150); id = rowIds()[0];
  setf(id, "sell.ccy", "USD", "change"); await sleep(150); id = rowIds()[0];
  setf(id, "sell.exch", "83.5"); setf(id, "sell.rate", "850");
  await sleep(2200);

  const saved = await j("GET", `/api/shipments/by-number/${jobNo}/charges`, null, token);
  check("autosave persisted the row in PostgreSQL", saved.body.lines.length === 1 && saved.body.lines[0].charge_name === "Ocean Freight", JSON.stringify(saved.body).slice(0, 300));
  check("server-calculated totals are correct (Decimal)", saved.body.totals.ar === "141950.00" && saved.body.totals.ap === "113016.00" && saved.body.totals.gross_profit === "28934.00", JSON.stringify(saved.body.totals));
  check("save indicator says Saved", /Saved/.test((d.getElementById("jc_saveState") || {}).textContent || ""), (d.getElementById("jc_saveState") || {}).textContent);
  check("Job Detail finance summary shows the server totals", /1,41,950\.00/.test(d.getElementById("jd_ar").textContent) && /1,13,016\.00/.test(d.getElementById("jd_ap").textContent) && /28,934\.00/.test(d.getElementById("jd_gp").textContent), d.getElementById("jd_ar").textContent + d.getElementById("jd_gp").textContent);
  check("Job P&L tab is built from real charges", /Ocean Freight/.test(d.getElementById("plRevenue").textContent) && /20\.38%/.test(d.getElementById("plProfit").textContent), d.getElementById("plProfit").textContent);
  const jobs = (await j("GET", "/api/shipments", null, token)).body;
  check("job list revenue reflects the sell total", Number(jobs.find(x => x.job_number === jobNo).revenue) === 141950, JSON.stringify(jobs[0]));

  // reload from the database
  w.go("dashboard"); await sleep(300);
  w.go("job-detail", { jobNo }); await sleep(1800);
  check("re-opening the job reloads the saved charges", rowIds().length === 1 && d.querySelector(`[data-jc-id="${rowIds()[0]}"][data-jc-f="chargeName"]`).value === "Ocean Freight" && d.querySelector(`[data-jc-id="${rowIds()[0]}"][data-jc-f="buy.rate"]`).value === "680", d.querySelector("#jc_tbody").textContent.slice(0, 120));

  // invalid input is rejected by the server and reported
  id = rowIds()[0]; setf(id, "buy.exch", "0"); await sleep(1500);
  check("server rejects zero exchange rate and the UI says so", /Not saved/.test(d.getElementById("jc_saveState").textContent), d.getElementById("jc_saveState").textContent);
  const still = await j("GET", `/api/shipments/by-number/${jobNo}/charges`, null, token);
  check("rejected edit did not overwrite saved data", still.body.lines[0].buy.exch === "83.100000", JSON.stringify(still.body.lines[0].buy));
  setf(id, "buy.exch", "83.1"); await sleep(1500);

  // document generation is real now: it creates a draft invoice from the saved charges
  toasts._clear(); d.getElementById("jc_genInvoiceBtn").click(); await sleep(2500);
  const drafts = (await j("GET", `/api/documents?doc_type=invoice&job_number=${jobNo}`, null, token)).body;
  check("Generate Invoice creates a real draft invoice from the charges", drafts.length === 1 && drafts[0].status === "DRAFT" && drafts[0].grand_total === "167501.00", JSON.stringify(drafts));
  w.go("job-detail", { jobNo }); await sleep(1800);

  // container-wise break-up of a charge
  d.getElementById("jc_addRowBtn").click(); await sleep(300);
  const id2 = rowIds()[rowIds().length - 1];
  setf(id2, "chargeName", "Documentation Charges", "change"); setf(id2, "uom", "Per Container", "change"); await sleep(300);
  const bu = d.querySelector(`[data-jc-bu="${id2}"]`);
  check("a Per Container charge offers a Break-up button", !!bu);
  bu.click(); await sleep(800);
  const bt = d.querySelector('#modalBody [data-bu="type:0"]'); bt.insertAdjacentHTML("beforeend", "<option>20GP</option><option>40HC</option>"); bt.value = "20GP"; fire(bt, "change");
  const bset = (f, v) => { const e = d.querySelector(`#modalBody [data-bu="${f}"]`); e.value = v; fire(e, "input"); };
  bset("qty:0", "2"); bset("sell:0", "1500"); bset("buy:0", "1000");
  d.getElementById("buAdd").click(); await sleep(100);
  const bt2 = d.querySelector('#modalBody [data-bu="type:1"]'); bt2.insertAdjacentHTML("beforeend", "<option>40HC</option>"); bt2.value = "40HC"; fire(bt2, "change");
  bset("qty:1", "1"); bset("sell:1", "2000"); bset("buy:1", "1500");
  check("the break-up dialog totals sell and buy", /5,000\.00/.test(d.getElementById("buSell").textContent) && /3,500\.00/.test(d.getElementById("buBuy").textContent), d.getElementById("buSell").textContent + "|" + d.getElementById("buBuy").textContent);
  d.getElementById("buApply").click(); await sleep(600);
  check("applying makes it one head: quantity 1 and the totals as rates", d.querySelector(`[data-jc-id="${id2}"][data-jc-f="qty"]`).value === "1" && Number(d.querySelector(`[data-jc-id="${id2}"][data-jc-f="sell.rate"]`).value) === 5000 && Number(d.querySelector(`[data-jc-id="${id2}"][data-jc-f="buy.rate"]`).value) === 3500 && /40HC x1/.test(d.querySelector(`tr[data-jc-row="${id2}"]`).textContent));
  d.getElementById("jc_saveBtn").click(); await sleep(1800);
  const bsaved = (await j("GET", `/api/shipments/by-number/${jobNo}/charges`, null, token)).body.lines.find(l => l.charge_name === "Documentation Charges");
  check("the break-up is saved with the charge", bsaved && bsaved.breakup.length === 2 && bsaved.qty === "1.000" && Number(bsaved.sell.rate) === 5000, JSON.stringify(bsaved));

  // manual save + clear
  toasts._clear(); d.getElementById("jc_saveBtn").click(); await sleep(1200);
  check("manual Save confirms only after the server accepts", toasts.some(t => /Job cost saved/.test(t)), toasts.join(";"));
  d.getElementById("jc_deleteBtn").click(); await sleep(2200);
  check("clearing the sheet persists an empty charge list", (await j("GET", `/api/shipments/by-number/${jobNo}/charges`, null, token)).body.lines.length === 0);

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
