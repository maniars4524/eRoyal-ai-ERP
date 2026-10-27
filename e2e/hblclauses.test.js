// HBL Clause Master screen + HBL CLAUSES on the job's BL tab + the printed HBL carries the saved clauses in order.
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
  const org = await j("POST", "/api/organizations", { code: "HC" + tag.slice(-6).toUpperCase(), name: "Clauses " + tag, admin_email: `hc${tag}@t.test`, admin_username: "hc" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Hana" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "hc" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "USLAX" }, token)).body.job_number;
  await j("POST", `/api/jobs/${job}/hbl`, { hbl_type: "MTO" }, token);
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.prompt = () => "test reason"; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(7000);
  const menu = Array.from(d.querySelectorAll(".tn-row")).find(x => x.dataset.go === "hbl-clauses");
  check("Masters > Common Masters > HBL Clause Master is in the menu", !!menu && menu.closest(".tn-panel") && Array.from(d.querySelectorAll(".tn-label")).some(x => x.textContent.trim() === "Common Masters"));
  menu.dispatchEvent(new w.MouseEvent("click", { bubbles: true })); await sleep(1500);
  check("the master screen opens with its cards and an empty-state note", d.getElementById("s-hbl-clauses").classList.contains("on") && /No clauses yet/.test(d.getElementById("hbcBody").textContent));
  d.getElementById("hbcSample").click(); await sleep(2500);
  check("Load sample clauses adds the standard list (nothing mandatory or default)", d.querySelectorAll("#hbcBody tr").length >= 25 && /Active/.test(d.getElementById("hbcKpi").textContent) && /Mandatory\s*0/.test(d.getElementById("hbcKpi").textContent.replace(/\s+/g, " ")), d.getElementById("hbcKpi").textContent);
  d.getElementById("hbcNew").click(); await sleep(400);
  d.getElementById("hf_title").value = "Own clause"; d.getElementById("hf_text").value = "OUR OWN CLAUSE " + tag; d.getElementById("hf_man").value = "Yes"; d.getElementById("hbcSave").click(); await sleep(2000);
  const mine = (await j("GET", "/api/hbl-clauses?q=OUR OWN CLAUSE " + tag, null, token)).body.items[0];
  check("a new clause is added from the screen with an automatic code and can be mandatory", !!mine && /^HBLC-\d{4}$/.test(mine.code) && mine.is_mandatory === true, JSON.stringify(mine));
  // the job's BL tab
  w.go("job-detail", { jobNo: job }); await sleep(3500);
  const wrap = d.getElementById("hblClausesWrap");
  check("the BL tab has the HBL CLAUSES section with the job context and the mandatory clause preselected", /HBL CLAUSES/.test(wrap.textContent) && wrap.textContent.includes(job) && /OUR OWN CLAUSE/.test(wrap.textContent), wrap.textContent.slice(0, 200));
  const pickBy = txt => Array.from(d.querySelectorAll("[data-hbs-pick]")).find(x => x.closest("label").textContent.includes(txt));
  const fcl = pickBy("FCL/FCL"); fcl.checked = true; fcl.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  const lcl = pickBy("LCL/LCL");
  check("clauses that do not fit an FCL shipment are not offered", !lcl);
  const free = pickBy("STANDARD UNLOADING FREE TIME"); free.checked = true; free.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  d.querySelector("[data-hbs-up]") && d.querySelectorAll("[data-hbs-up]")[2].click(); await sleep(200);
  d.getElementById("hbsSave").click(); await sleep(2500);
  const saved = (await j("GET", `/api/jobs/${job}/hbl-clauses`, null, token)).body;
  check("Save stores the selection (mandatory clause included) as a snapshot", saved.saved && saved.selected.length === 3 && saved.selected.some(s => s.code === mine.code), JSON.stringify(saved.selected.map(s => s.code)));
  const rd = await w.BLDocKit.data(job);
  const html = w.BLDocKit.html(rd);
  const up = html.toUpperCase(); const order = saved.selected.map(s => up.indexOf(s.text.toUpperCase()));
  check("the printed HBL has exactly the saved clauses in the saved order and no clause codes", order.every(i => i > 0) && order.slice().sort((a, b) => a - b).join() === order.join() && !/HBLC-\d{4}/.test(html), JSON.stringify(order));
  // choices are not lost when the user goes to another tab and comes back, and the list does not jump to the top when a clause is ticked
  w.go("job-detail", { jobNo: job }); await sleep(3500);
  const extra = pickBy("DEMURRAGE AND DETENTION"); extra.checked = true; extra.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  d.getElementById("hbsAvail").scrollTop = 140; const another = pickBy("PALLETIZED"); another.checked = true; another.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  check("ticking a clause keeps the list where the user was scrolled", d.getElementById("hbsAvail").scrollTop === 140, d.getElementById("hbsAvail").scrollTop);
  d.querySelector('[data-tab="jt1"]').click(); await sleep(2500);
  d.querySelector('[data-tab="jt3"]').click(); await sleep(1500);
  const after = (await j("GET", `/api/jobs/${job}/hbl-clauses`, null, token)).body.selected.map(x => x.text);
  check("selected clauses are still there after going to another tab and back (an unsaved change is saved when leaving the BL tab)", /DEMURRAGE AND DETENTION/.test(after.join("|")) && /PALLETIZED/.test(after.join("|")) && /DEMURRAGE AND DETENTION/.test(d.getElementById("hblClausesWrap").textContent) && /PALLETIZED/.test(d.getElementById("hblClausesWrap").textContent), after.length + " saved");
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
