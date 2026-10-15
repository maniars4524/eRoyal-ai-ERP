// Headless UI check: administrator edit of an approved quotation, Cancel / Lost / Reject stages, and the Charge Master reflected in quotation + job cost with an instant "New Charge".
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
  const org = await j("POST", "/api/organizations", { code: "QS" + tag.slice(-6).toUpperCase(), name: "Stages " + tag, admin_email: `qs${tag}@t.test`, admin_username: "qs" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Q" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "qs" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const cust = (await j("POST", "/api/customers", { code: "ACME" + tag.slice(-3).toUpperCase(), name: "Acme Exports", roles: ["shipper"], data: {} }, token)).body;
  await j("POST", "/api/masters/charge", { code: "OFR", name: "Ocean Freight", data: { hsn_code: "996512", gst_percent: "18" } }, token);
  const line = (n, r, side) => ({ side, charge_name: n, carrier: "", basis: "Per Container", ccy: "USD", exch: "83.5", rate: r, qty: "2", tax_pct: "0" });
  const mk = async () => {
    const q = (await j("POST", "/api/quotations", { customer_id: cust.id, party_name: "Acme Exports", mode: "Sea", direction: "Export", shipment_type: "FCL", pol: "INMAA(CHENNAI)", pod: "BEANR(ANTWERPEN)", lines: [line("Ocean Freight", "850", "SELL")] }, token)).body;
    await j("POST", `/api/quotations/${q.id}/approval/submit`, null, token); return (await j("POST", `/api/quotations/${q.id}/approval/approve`, {}, token)).body;
  };
  const approved = await mk();

  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.prompt = () => "Customer chose another forwarder"; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q$ = s => d.querySelector(s);
  const text = s => (q$(s) || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };

  // ---- administrator edit of an approved, numbered quotation
  check("an approved quotation is editable for an administrator", approved.editable === true && /^QT\//.test(approved.number));
  await w.QK.openView(approved.id, false, "NOTAX"); await sleep(1200);
  const editBtn = q$(`#s-quotation-print [data-qk-do="edit:${approved.id}"]`);
  check("the preview toolbar has an Edit button (and no Company Logo button)", !!editBtn && !/Company Logo/.test(text("#s-quotation-print .pa")));
  check("only one Back button in the preview", Array.from(d.querySelectorAll("#s-quotation-print .pa button")).filter(b => b.style.display !== "none" && /^\s*back\s*$/i.test(b.textContent)).length === 1);
  editBtn.click(); await sleep(2000);
  const saveBtn = q$('#qkActions [data-qa="save"]');
  check("the form opens for editing with Save Changes (no resubmission)", !!saveBtn && /Save Changes/.test(saveBtn.textContent) && !q$('#qkActions [data-qa="submit"]'), text("#qkActions"));
  const remarks = q$('[data-qh="remarks"]'); setf(remarks, "Rate corrected by the administrator");
  saveBtn.click(); await sleep(1500);
  const after = (await j("GET", `/api/quotations/${approved.id}`, null, token)).body;
  check("the change is saved and the number is kept", after.remarks === "Rate corrected by the administrator" && after.number === approved.number && after.approval_status === "APPROVED", JSON.stringify([after.remarks, after.number]));

  // ---- charge master reflected + instant New Charge in the quotation
  await j("POST", "/api/masters/charge", { code: "FSC", name: "Fuel Surcharge", data: { hsn_code: "996511", gst_percent: "18" } }, token);
  q$('[data-qa="add"][data-side="SELL"]').click(); await sleep(1200);
  const opts = Array.from(d.querySelectorAll('select[data-ql$=":charge_name"] option')).map(o => o.value);
  check("a charge uploaded to the Charge Master appears in the quotation without reloading", opts.includes("Fuel Surcharge"), opts.join("|"));
  q$('[data-qa="newcharge"][data-side="BUY"]').click(); await sleep(1500);
  check("New Charge opens the Charge Master form", !!q$("#nc_code"));
  setf(q$("#nc_code"), "DGS"); setf(q$("#nc_name"), "DG Surcharge");
  if (q$("#nc_hsn_man") && !q$("#nc_hsn_man").checked) q$("#nc_hsn_man").click();
  setf(q$("#nc_hsn_manual"), "996511"); setf(q$("#nc_gst_pick"), "18", "change");
  q$("#nc_save").click(); await sleep(2000);
  const master = (await j("GET", "/api/masters/charge", null, token)).body;
  check("the new charge is saved in the Charge Master", master.some(c => c.name === "DG Surcharge"));
  check("and it is on the quotation as a buy line, taxable at its GST", Array.from(d.querySelectorAll('select[data-ql^="buy:"][data-ql$=":charge_name"]')).some(s => s.value === "DG Surcharge") && /18/.test(Array.from(d.querySelectorAll('[data-ql^="buy:"][data-ql$=":tax_pct"]')).map(e => e.value).join(",")), text("#qkRoot").slice(0, 100));

  // ---- Cancel / Lost / client Reject
  const toCancel = await mk();
  w.go("quotation"); await sleep(1200);
  check("the list offers Cancel on a numbered quotation", !!q$(`[data-qk-do="cancel:${toCancel.id}"]`));
  q$(`[data-qk-do="cancel:${toCancel.id}"]`).click(); await sleep(1500);
  const c = (await j("GET", `/api/quotations/${toCancel.id}`, null, token)).body;
  check("Cancel closes it with the reason", c.status === "CANCELLED" && c.closure_reason === "Customer chose another forwarder" && c.editable === false, JSON.stringify([c.status, c.closure_reason]));
  const toLose = await mk(); await j("POST", `/api/quotations/${toLose.id}/send`, null, token);
  w.go("quotation"); await sleep(1200);
  check("an issued quotation offers Accept, Reject, Lost and Cancel", ["accept", "reject", "lost", "cancel"].every(k => !!q$(`[data-qk-do="${k}:${toLose.id}"]`)));
  q$(`[data-qk-do="lost:${toLose.id}"]`).click(); await sleep(1500);
  const l = (await j("GET", `/api/quotations/${toLose.id}`, null, token)).body;
  check("Lost closes it as LOST with the reason", l.status === "LOST" && l.closure_reason === "Customer chose another forwarder", l.status);
  check("the list shows the Lost and Cancelled status", /Lost/.test(text("#s-quotation tbody")) && /Cancelled/.test(text("#s-quotation tbody")), text("#s-quotation tbody").slice(0, 300));
  const toReject = await mk(); await j("POST", `/api/quotations/${toReject.id}/send`, null, token);
  w.go("quotation"); await sleep(1200);
  q$(`[data-qk-do="reject:${toReject.id}"]`).click(); await sleep(1500);
  check("client Reject still works", (await j("GET", `/api/quotations/${toReject.id}`, null, token)).body.status === "REJECTED");

  // ---- job cost
  const job = await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR" }, token);
  await j("POST", "/api/masters/charge", { code: "BAF", name: "Bunker Adjustment", data: { hsn_code: "996511", gst_percent: "18" } }, token);
  w.go("job-detail", { jobNo: job.body.job_number }); await sleep(2000);
  q$("#jc_addRowBtn").click(); await sleep(1200);
  check("job cost lists a charge added to the Charge Master a moment ago", /Bunker Adjustment/.test(text("#jc_tbody")), text("#jc_tbody").slice(0, 150));
  check("job cost has a New Charge button", !!q$("#jc_newChargeBtn"));
  q$("#jc_newChargeBtn").click(); await sleep(1500);
  setf(q$("#nc_code"), "CFS"); setf(q$("#nc_name"), "CFS Handling");
  if (q$("#nc_hsn_man") && !q$("#nc_hsn_man").checked) q$("#nc_hsn_man").click();
  setf(q$("#nc_hsn_manual"), "996719"); setf(q$("#nc_gst_pick"), "18", "change");
  q$("#nc_save").click(); await sleep(2000);
  check("the instantly added charge is on the job cost grid and in the master", /CFS Handling/.test(Array.from(d.querySelectorAll("#jc_tbody select")).map(s => s.value).join("|")) && (await j("GET", "/api/masters/charge", null, token)).body.some(x => x.name === "CFS Handling"));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
