// Headless UI check #7: dashboard shows only real data; organization form explains missing fields.
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
const today = new Date(); const iso = d => d.toISOString().slice(0, 10);

async function company(devToken, prefix) {
  const tag = Date.now().toString(36) + prefix;
  const org = await j("POST", "/api/organizations", { code: prefix + tag.slice(-6).toUpperCase(), name: "Dash " + tag, admin_email: `${prefix}${tag}@t.test`, admin_username: prefix + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "D" }, devToken);
  const login = await j("POST", "/api/auth/login", { identifier: prefix + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number });
  return login.body.access_token;
}
async function openPage(token) {
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  await sleep(2500); return dom.window;
}

(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const devToken = dev.body.access_token;

  // ---------- the served page itself must contain no dummy dashboard figures (stale tabs / failed loads can't show them)
  const html = await (await fetch(BASE + "/erp")).text();
  const seg = html.slice(html.indexOf('id="s-dashboard"'), html.indexOf('id="s-meetings"'));
  check("page source has no dummy Shipment Mix / lanes / matrix numbers", !/Chennai → Hamburg|Chennai → Jebel Ali|Halifax → Chennai|>47<|>23<|>14<|Active Jobs<\/text>|>15<\/div><div style="font-size:9.5px/.test(seg), (seg.match(/Chennai → Hamburg|>47<|>23<|>15<\/div>/g) || []).join());

  // ---------- empty company
  let token = await company(devToken, "de");
  let w = await openPage(token), d = w.document;
  w.go("dashboard"); await sleep(1500);
  const dashText = () => d.getElementById("s-dashboard").textContent.replace(/\s+/g, " ");
  check("empty company: no dummy figures anywhere on the dashboard", !/Chennai → Hamburg|Halifax|Jebel Ali|RG Cargo|Tata|TVS|Infosys|Arun Kumar|Priya S|Rahul M/.test(dashText()), dashText().match(/Chennai → Hamburg|Halifax|Jebel Ali|RG Cargo|Tata|TVS|Infosys|Arun Kumar|Priya S|Rahul M/));
  check("empty company: Shipment Mix says no shipments", /No shipments yet/.test(dashText()) && /No lanes yet/.test(dashText()));
  check("empty company: KPI cards show zero, not placeholders", /Active Jobs[^A-Z]*0/.test(dashText()) && /No outstanding receivables/.test(dashText()) && /No jobs yet/.test(dashText()));
  w.close();

  // ---------- company with data
  token = await company(devToken, "dd");
  for (const [mode, direction, pol, pod, eta] of [["Sea", "Export", "INMAA", "DEHAM", iso(new Date(today.getTime() + 3 * 864e5))], ["Sea", "Export", "INMAA", "DEHAM", null], ["Air", "Import", "SGSIN", "INMAA", null]]) {
    await j("POST", "/api/shipments", { mode, direction, pol, pod, ...(eta ? { eta } : {}) }, token);
  }
  const sea = (await j("GET", "/api/shipments", null, token)).body.find(x => x.mode === "Sea");
  const mk = async (body) => { const doc = (await j("POST", "/api/documents", body, token)).body; await j("POST", `/api/documents/${doc.id}/post`, null, token); return doc; };
  const inv = await mk({ doc_type: "invoice", party_name: "Acme Ltd", job_number: sea.job_number, lines: [{ charge_name: "Freight", rate: "100000", tax_type: "Non-Taxable" }] });
  await mk({ doc_type: "purchase_invoice", party_name: "MSC", job_number: sea.job_number, lines: [{ charge_name: "Freight cost", rate: "60000", tax_type: "Non-Taxable" }] });
  await mk({ doc_type: "invoice", party_name: "Late Traders", doc_date: iso(new Date(today.getTime() - 50 * 864e5)), due_date: iso(new Date(today.getTime() - 40 * 864e5)), lines: [{ rate: "25000", tax_type: "Non-Taxable" }] });
  w = await openPage(token); d = w.document;
  w.go("dashboard"); await sleep(2500);
  const text = dashText();
  const kpi = key => d.querySelector(`.kpi-flip[data-kpi-select="${key}"]`).textContent.replace(/\s+/g, " ");
  check("Active Jobs KPI shows the real count", /Active Jobs\s*3/.test(kpi("jobs")), kpi("jobs"));
  check("Revenue MTD KPI shows posted invoices", /1,00,000/.test(kpi("revenue")), kpi("revenue"));
  check("Gross Profit KPI = revenue - purchase invoices", /40,000/.test(kpi("gp")) && /40(\.0)?% margin/.test(kpi("gp")), kpi("gp"));
  check("AR Outstanding KPI and overdue customer", /1,25,000/.test(kpi("ar")) && /Late Traders/.test(kpi("ar")), kpi("ar"));
  const mix = Array.from(d.querySelectorAll("#s-dashboard .card")).find(c => c.querySelector(".ct") && c.querySelector(".ct").textContent.trim() === "Shipment Mix").textContent.replace(/\s+/g, " ");
  check("Shipment Mix donut/legend shows the real mix", /Sea Export\s*2/.test(mix) && /Air Import\s*1/.test(mix) && /INMAA → DEHAM\s*2 jobs/.test(mix) && !/\b23\b|\b14\b|Hamburg/.test(mix), mix);
  check("shipment-type matrix shows real counts", /Sea Export\s*2\s*jobs\s*0\s*jobs\s*2\s*jobs/.test(text) || /Sea Export.*?2/.test(text), text.slice(text.indexOf("By Shipment Type"), text.indexOf("By Shipment Type") + 300));
  check("Recent Jobs lists the real jobs", d.querySelectorAll('#s-dashboard tr[data-job]').length === 3, d.querySelectorAll('#s-dashboard tr[data-job]').length);
  check("AR ageing shows the 31-60 day bucket", /31–60 days\s*₹25,000/.test(text), text.slice(text.indexOf("AR Ageing"), text.indexOf("AR Ageing") + 300));
  const alerts = d.getElementById("dashAlerts").textContent.replace(/\s+/g, " ");
  check("alerts list overdue invoices and arriving jobs", /Overdue invoices/.test(alerts) && /Arriving within 7 days/.test(alerts), alerts);
  check("chart is drawn from real months/jobs", /<text[^>]*>3<\/text>/.test(d.getElementById("dashChart").innerHTML), d.getElementById("dashChart").innerHTML.slice(0, 200));
  check("carousel only offers slides driven by real data", w.document.querySelectorAll("#dashDots > *").length === 3, w.document.querySelectorAll("#dashDots > *").length);
  check("decorative branch/company filters are gone", !d.getElementById("dfBranch") && !d.querySelector("#s-dashboard .dash-filters"));
  check("no dummy names remain on the dashboard", !/Chennai → Hamburg|Halifax|Jebel Ali|RG Cargo|Tata|TVS|Infosys|Arun Kumar|Priya S|Rahul M/.test(text));
  w.close();

  // ---------- organization form: clear guidance when required fields are missing
  w = await openPage(token); d = w.document;
  w.go("org-new"); await sleep(600);
  d.getElementById("org_code").value = "ABC1"; d.getElementById("org_name").value = "ABC Traders";
  d.querySelector('#orgRoleFlags .fi[data-role="shipper"]').click();
  d.getElementById("orgSaveBtn").click(); await sleep(800);
  const banner = d.querySelector("#s-org-new .mk-missing");
  check("missing fields are listed in a banner on the form", !!banner && /GSTIN/.test(banner.textContent) && /Address Line 1/.test(banner.textContent) && /tab/.test(banner.textContent), banner && banner.textContent);
  check("nothing was saved while fields were missing", (await j("GET", "/api/customers", null, token)).body.length === 0);
  const country = Array.from(d.querySelectorAll("#s-org-new .fg")).find(x => /^Country/.test(x.querySelector("label.fl").textContent.trim())).querySelector("select");
  country.value = Array.from(country.options).find(o => /UAE/.test(o.text)).value; country.dispatchEvent(new w.Event("change", { bubbles: true }));
  d.getElementById("orgSaveBtn").click(); await sleep(800);
  check("the form jumps to the tab holding the first missing field", d.getElementById("ot2").classList.contains("on") && /Address Line 1/.test(d.querySelector("#s-org-new .mk-missing").textContent), "ot2 on=" + d.getElementById("ot2").classList.contains("on"));
  const fill = (label, v) => { const fg = Array.from(d.querySelectorAll("#s-org-new .fg")).find(x => x.querySelector("label.fl").textContent.replace(/\*/g, "").trim().startsWith(label)); const el = fg.querySelector("input,select"); el.value = v; };
  fill("Address Line 1", "Business Bay"); fill("City", "Dubai");
  Array.from(d.querySelectorAll("#s-org-new .fg")).filter(x => /^State \/ Province/.test(x.querySelector("label.fl").textContent.trim())).forEach(x => { x.querySelector("input,select").value = "Dubai"; });
  d.getElementById("orgSaveBtn").click(); await sleep(1500);
  const saved = (await j("GET", "/api/customers", null, token)).body;
  check("once the required fields are filled the organization saves", saved.length === 1 && saved[0].code === "ABC1" && saved[0].country === "UAE" && saved[0].roles[0] === "shipper", JSON.stringify(saved).slice(0, 300) + " | " + (d.querySelector("#s-org-new .mk-missing") || {}).textContent);
  w.close();

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
