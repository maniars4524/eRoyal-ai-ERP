// Headless UI check: the job's vouchers (proforma -> tax invoice, credit / debit notes, purchase invoice and credit note), the Vouchers tab, Out Docs and the A4 documents.
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
  const org = await j("POST", "/api/organizations", { code: "VCH" + tag.slice(-4).toUpperCase(), name: "Vouchers " + tag, admin_email: `vc${tag}@t.test`, admin_username: "vc" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "V" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "vc" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const cust = (await j("POST", "/api/customers", { code: "ACM" + tag.slice(-3).toUpperCase(), name: "Acme Exports", roles: ["shipper", "client"], data: {} }, token)).body;
  await j("PUT", "/api/company/doc-profile", { legal_name: "VOUCHERS TEST CO", gstin: "33AAACV1234A1Z5", address: "Chennai", bank_name: "HSBC", bank_account: "123" }, token);
  await j("POST", "/api/masters/setting", { code: "INVOICE_REQUIRES_PROFORMA", name: "Invoice requires a confirmed proforma", data: { enabled: "yes" } }, token);
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "SGSIN", customer_id: cust.id }, token)).body;
  const side = (tt, rate, pct) => ({ ccy: "INR", rate, tax_type: tt, tax_pct: pct, party: "Acme Exports" });
  const buy = (rate, v) => ({ ccy: "INR", rate, tax_type: "Taxable", tax_pct: "18", party: v });
  await j("PUT", `/api/shipments/by-number/${job.job_number}/charges`, { lines: [
    { charge_name: "Ocean Freight", qty: "1", hsn: "996521", sell: side("Taxable", "10000", "18"), buy: buy("6000", "MSC Lines") },
    { charge_name: "Documentation", qty: "1", hsn: "996799", sell: side("Non-Taxable", "1500", "0"), buy: buy("0", "") },
    { charge_name: "Port Fee Reimbursed", qty: "1", hsn: "996759", sell: side("Pure Agent", "800", "0"), buy: buy("0", "") }] }, token);

  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; w.open = () => null; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s);
  await sleep(3000); w.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
  q('.tbb[data-tab="jt5"]').click(); await sleep(800);

  // 1. Invoice from the job's charges: split by kind
  q("#jc_genInvoiceBtn").click(); await sleep(3500);
  let docs = (await j("GET", `/api/documents?job_number=${job.job_number}`, null, token)).body;
  const kinds = docs.map(x => x.invoice_kind).sort().join(",");
  check("Invoice from the job's charges makes a proforma per kind of entry (tax + non-taxable together, pure agent apart)", docs.length === 2 && kinds === "PURE_AGENT,TAX" && docs.every(x => x.status === "DRAFT"), JSON.stringify(docs.map(x => [x.invoice_kind, x.status])));
  const taxDoc = docs.find(x => x.invoice_kind === "TAX");
  check("the editor opens the proforma with Confirm Proforma and without Generate Tax Invoice until it is confirmed", !!q('[data-bk="confirm"]') && !q('[data-bk="post"]'), Array.from(d.querySelectorAll("[data-bk]")).map(b => b.dataset.bk).join(","));
  q('[data-bk="confirm"]').click(); await sleep(2500);
  check("confirming gives the proforma number and the Tax Invoice button appears", !!q('[data-bk="post"]') && (await j("GET", `/api/documents/${taxDoc.id}`, null, token)).body.status === "CONFIRMED");
  q('[data-bk="post"]').click(); await sleep(2500);
  const posted = (await j("GET", `/api/documents/${taxDoc.id}`, null, token)).body;
  check("generating the Tax Invoice posts it with its legal number and keeps the proforma reference", posted.status === "POSTED" && posted.doc_number && posted.header.proforma_no, JSON.stringify(posted.header));

  // 2. Vouchers tab, credit note against the invoice, debit note
  w.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
  q('.tbb[data-tab="jt8"]').click(); await sleep(1500);
  check("the Vouchers tab lists the job's invoice with its number and the proforma of the pure agent charges", /Tax Invoice/.test(q("#vtax").textContent) && q("#vtax").textContent.includes(posted.doc_number) && /Proforma/.test(q("#vtax").textContent), q("#vtax").textContent.slice(0, 200));
  const creditBtn = q(`#vtax [data-vk="credit"][data-id="${posted.id}"]`);
  check("the posted invoice has Credit Note and Debit Note buttons", !!creditBtn && !!q(`#vtax [data-vk="debit"][data-id="${posted.id}"]`));
  creditBtn.click(); await sleep(1500);
  check("Credit Note asks how much of the invoice to credit", !!d.getElementById("vkMode") && /raised wrongly/.test(d.getElementById("modalBody").textContent));
  d.getElementById("vkGo").click(); await sleep(3000);
  check("the credit note editor opens against that invoice with its lines copied", q('[data-bkh="ref_document_id"]') && String(q('[data-bkh="ref_document_id"]').value) === String(posted.id) && d.querySelectorAll("[data-bkl]").length > 0, q('[data-bkh="ref_document_id"]') && q('[data-bkh="ref_document_id"]').value);
  q('[data-bk="post"]').click(); await sleep(2800);
  docs = (await j("GET", `/api/documents?job_number=${job.job_number}`, null, token)).body;
  const cn = docs.find(x => x.doc_type === "credit_note");
  check("the credit note is posted against the invoice and reduces what is outstanding", cn && cn.status === "POSTED" && cn.ref_document_id === posted.id && Number((await j("GET", `/api/documents/${posted.id}`, null, token)).body.outstanding) === 0, JSON.stringify(cn));

  w.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
  q('.tbb[data-tab="jt8"]').click(); await sleep(1500);
  q(`#vtax [data-vk="debit"][data-id="${posted.id}"]`).click(); await sleep(2500);
  check("Debit Note opens for the job and customer", d.getElementById("s-debit-note-new").classList.contains("on") && /Acme Exports/.test(d.getElementById("s-debit-note-new").innerHTML) || !!q('[data-bkh="party_name"]'));

  // 3. Purchase invoice and purchase credit note
  const pi = await j("POST", "/api/documents/from-job", { job_number: job.job_number, doc_type: "purchase_invoice", party: "MSC Lines" }, token);
  check("purchase invoice from the job's buy charges", pi.status === 201 && pi.body.doc_type === "purchase_invoice", JSON.stringify(pi.body).slice(0, 150));
  await j("POST", `/api/documents/${pi.body.id}/post`, null, token);
  w.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
  q('.tbb[data-tab="jt8"]').click(); await sleep(1500);
  check("the purchase invoice is in the Vouchers tab with a Purchase Credit Note button", q("#vpi").textContent.includes(pi.body.doc_number || "") || /Purchase Invoice|PI/.test(q("#vpi").textContent), q("#vpi").textContent.slice(0, 150));
  const pcnBtn = q(`#vpi [data-vk="pcredit"][data-id="${pi.body.id}"]`);
  check("the purchase invoice has a Purchase Credit Note button", !!pcnBtn);

  // 4. the Accounts side sees the same vouchers
  const reg = (await j("GET", "/api/documents?doc_type=invoice", null, token)).body;
  check("the Accounts invoice register carries the job's invoice", reg.some(x => x.id === posted.id && x.job_number === job.job_number));
  const regC = (await j("GET", "/api/documents?doc_type=credit_note", null, token)).body;
  check("and the credit note issued against it", regC.some(x => x.ref_document_id === posted.id));
  w.go("invoice-list"); await sleep(2500);
  check("the invoice register screen lists it", d.getElementById("s-invoice-list").textContent.includes(posted.doc_number), "");

  // 5. Out Docs and the A4 documents
  w.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
  q('.tbb[data-tab="jt7b"]').click(); await sleep(2500);
  check("Out Docs lists the job's billing documents with View / Print / PDF / Send", d.querySelectorAll("#odBilling [data-od=doc][data-act=view]").length >= 3 && d.querySelectorAll("#odBilling [data-od=doc][data-act=mail]").length >= 3, d.querySelectorAll("#odBilling [data-od=doc]").length);
  w.__docKind = null; d.querySelector(`#odBilling [data-od=doc][data-act=view][data-doc-id="${posted.id}"]`).click(); await sleep(3500);
  const html = d.getElementById("docViewZone").innerHTML;
  check("the Tax Invoice opens as an A4 page with ORIGINAL, bill-to, shipment references, HSN summary and invoice value summary", /TAX INVOICE/.test(html) && /ORIGINAL/.test(html) && /SHIPMENT &amp; JOB REFERENCES/.test(html) && /HSN \/ SAC WISE TAX SUMMARY/.test(html) && /INVOICE VALUE SUMMARY/.test(html) && /class="pg"/.test(html) && /BILL TO \/ CUSTOMER DETAILS/.test(html), html.slice(0, 160));
  check("there is no Reference column in the entries", !/>Reference</.test(html));
  check("a Non-Taxable / tax type legend with P for pure agent is printed", /P – Pure Agent/.test(html));

  // 6. e-Invoice
  const ei = await j("POST", `/api/documents/${posted.id}/e-invoice`, null, token);
  check("e-Invoice needs the customer's GSTIN", ei.status === 422 && /GSTIN/.test(JSON.stringify(ei.body)), JSON.stringify(ei.body).slice(0, 120));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
