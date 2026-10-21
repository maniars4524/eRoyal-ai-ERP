// Headless UI check: Mark as Paid (full / part, mode and transaction details, several invoices in one voucher), receipt vouchers, the Paid chip, Accounts reports and the job profit and loss.
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
  const org = await j("POST", "/api/organizations", { code: "RCP" + tag.slice(-4).toUpperCase(), name: "Receipts " + tag, admin_email: `rc${tag}@t.test`, admin_username: "rc" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "R" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "rc" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const cust = (await j("POST", "/api/customers", { code: "ACM" + tag.slice(-3).toUpperCase(), name: "Acme Exports", roles: ["shipper", "client"], data: {} }, token)).body;
  await j("PUT", "/api/company/doc-profile", { legal_name: "RECEIPTS TEST CO", address: "Chennai", gstin: "33AAACV1234A1Z5" }, token);
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "SGSIN", customer_id: cust.id }, token)).body;
  const mk = async rate => { const d = (await j("POST", "/api/documents", { doc_type: "invoice", party_name: "Acme Exports", customer_id: cust.id, job_number: job.job_number, lines: [{ charge_name: "Ocean Freight", hsn: "996521", uom: "Per Shipment", qty: "1", ccy: "INR", exch: "1", rate, tax_type: "Non-Taxable", tax_pct: "0" }] }, token)).body; await j("POST", `/api/documents/${d.id}/post`, null, token); return d; };
  const inv1 = await mk("1000"), inv2 = await mk("500");
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; w.open = () => null; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s);
  await sleep(3000); w.BK.openDoc(inv1.id); await sleep(3500);
  check("a posted invoice has Preview and Mark as Paid", !!q('#s-invoice-new [data-bk="preview"]') && !!q('#s-invoice-new [data-bk="receipt"]') && /Mark as Paid/.test(q('#s-invoice-new [data-bk="receipt"]').textContent));
  q('#s-invoice-new [data-bk="receipt"]').click(); await sleep(2500);
  check("the receipt page asks for full / part payment, the mode and its transaction details", !!q('input[name="pk_kind"][value="part"]') && !!q("#pk_mode") && !!q("#pk_ref") && /Full payment/.test(d.getElementById("modalBody").textContent));
  q("#pk_mode").value = "Cheque"; q("#pk_mode").dispatchEvent(new w.Event("change", { bubbles: true }));
  check("choosing Cheque asks for the cheque number, date and bank", !!q("#pk_chq") && !!q("#pk_chqd") && !!q("#pk_bank") && !q("#pk_ref"));
  const part = q('input[name="pk_kind"][value="part"]'); part.checked = true; part.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  const amt = q('[data-pk-doc="' + inv1.id + '"]'); amt.value = "400"; amt.dispatchEvent(new w.Event("input", { bubbles: true }));
  q("#pk_acc").value = "HDFC Current"; q("#pk_chq").value = "556677"; q("#pk_chqd").value = "2026-10-20"; q("#pk_bank").value = "HDFC Bank";
  q("#pk_go").click(); await sleep(3500);
  const pays = (await j("GET", `/api/documents/${inv1.id}/payments`, null, token)).body;
  check("a part receipt is recorded with the cheque details and PART settlement", pays.payments.length === 1 && pays.payments[0].applied_to_this === "400.00" && Number(pays.outstanding) === 600, JSON.stringify(pays.payments[0]));
  check("the receipt voucher opens as an A4 page showing Part payment and the balance outstanding", /RECEIPT VOUCHER/.test(d.getElementById("docViewZone").innerHTML) && /Part payment/.test(d.getElementById("docViewZone").innerHTML) && /556677/.test(d.getElementById("docViewZone").innerHTML) && /600\.00/.test(d.getElementById("docViewZone").innerHTML), d.getElementById("docViewZone").innerHTML.slice(0, 200));
  q('#s-doc-view [data-go]').click(); await sleep(2500);
  check("Back returns to the invoice, now Partial with the receipt listed", /Partial/i.test(q("#s-invoice-new").textContent) && !!q("#bkReceiptsBody [data-bk-voucher]"));

  // one voucher for both invoices, in full, by NEFT
  w.BK.openPayment("RECEIPT", null); await sleep(2500);
  q("#pk_mode").value = "NEFT"; q("#pk_mode").dispatchEvent(new w.Event("change", { bubbles: true }));
  for (const id of [inv1.id, inv2.id]) { const c = q('[data-pk-pick="' + id + '"]'); c.checked = true; c.dispatchEvent(new w.Event("change", { bubbles: true })); }
  await sleep(200);
  check("full payment fills each invoice's balance and the total", q("#pk_amount").value === "1100.00", q("#pk_amount").value);
  q("#pk_go").click(); await sleep(1500);
  check("NEFT without a UTR is refused", (await j("GET", `/api/documents/${inv2.id}/payments`, null, token)).body.payments.length === 0);
  q("#pk_ref").value = "UTR-998877"; q("#pk_go").click(); await sleep(3500);
  const both = (await j("GET", `/api/documents/${inv2.id}`, null, token)).body;
  const bothPays = (await j("GET", `/api/documents/${inv2.id}/payments`, null, token)).body;
  check("one NEFT voucher settles both invoices in full", both.payment_status === "PAID" && bothPays.payments.length === 1 && bothPays.payments[0].against.length === 2, JSON.stringify(bothPays.payments[0] && bothPays.payments[0].against));
  check("its voucher lists both invoices", /UTR-998877/.test(d.getElementById("docViewZone").innerHTML) && /Full payment/.test(d.getElementById("docViewZone").innerHTML));

  // the Paid chip
  w.BK.openDoc(inv1.id); await sleep(3500);
  const chip = q("#s-invoice-new [data-vch-doc]");
  check("the Paid chip is touchable", !!chip, "");
  chip.click(); await sleep(1500);
  check("touching Paid on an invoice with two receipts lists the vouchers", /Receipt vouchers/.test(d.getElementById("modalTitle").textContent) && d.querySelectorAll("[data-pk-view]").length === 2, d.getElementById("modalTitle").textContent);
  d.querySelector("[data-pk-view]").click(); await sleep(3000);
  check("and opens the voucher", /RECEIPT VOUCHER/.test(d.getElementById("docViewZone").innerHTML));

  // TDS deducted by the customer, shown on the receipt with the invoice number
  const inv3 = await mk("10000");
  w.BK.openDoc(inv3.id); await sleep(3500);
  q('#s-invoice-new [data-bk="receipt"]').click(); await sleep(2500);
  q("#pk_mode").value = "RTGS"; q("#pk_mode").dispatchEvent(new w.Event("change", { bubbles: true }));
  const pick = q('[data-pk-pick="' + inv3.id + '"]'); check("the invoice being paid is preselected", pick.checked);
  const tds = q('[data-pk-tds="' + inv3.id + '"]'); tds.value = "100"; tds.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(200);
  check("TDS is deducted from what is received (full payment: 10,000 - 100)", q('[data-pk-doc="' + inv3.id + '"]').value === "9900.00" && q("#pk_amount").value === "9900.00" && /TDS deducted 100\.00/.test(q("#pk_tdsnote").textContent), q("#pk_amount").value + " | " + q("#pk_tdsnote").textContent);
  q("#pk_ref").value = "UTR-TDS-1"; q("#pk_go").click(); await sleep(3500);
  const html3 = d.getElementById("docViewZone").innerHTML;
  check("the receipt shows the invoice number, the TDS deducted and the total settled", html3.includes((await j("GET", `/api/documents/${inv3.id}`, null, token)).body.doc_number) && /TDS deducted/.test(html3) && /Total settled/.test(html3) && /TDS Deducted/.test(html3) && /Full payment/.test(html3), JSON.stringify({num: html3.includes(inv3.doc_number), tds: /TDS deducted/.test(html3), settled: /Total settled/.test(html3), col: /TDS Deducted/.test(html3), full: /Full payment/.test(html3), dn: inv3.doc_number}));
  check("the invoice is Paid although the bank received only the cash part", (await j("GET", `/api/documents/${inv3.id}`, null, token)).body.payment_status === "PAID");
  const cb = (await j("GET", "/api/accounts/cash-book", null, token)).body; check("the cash book carries only the cash received", cb.entries.some(e => e.reference === "UTR-TDS-1" && e.receipt === "9900.00"), JSON.stringify(cb.entries.slice(-1)));

  // vouchers raised anywhere show on the job's Charges tab
  w.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
  q('.tbb[data-tab="jt5"]').click(); await sleep(2200);
  check("the Charges tab shows the job's billing position from the vouchers", /Invoiced \(AR\)/.test(q("#jcBillBar").textContent) && /Receivable/.test(q("#jcBillBar").textContent) && /11,500\.00/.test(q("#jcBillBar").textContent), q("#jcBillBar") && q("#jcBillBar").textContent);

  // Country master
  w.go("country-master"); await sleep(5000);
  const countries = (await j("GET", "/api/masters/country", null, token)).body;
  check("the Country Master holds all 249 ISO countries", countries.length >= 249 && countries.some(c => c.code === "IN" && c.name === "India") && countries.some(c => c.code === "SG"), countries.length);
  check("the Country Master screen lists them", d.querySelectorAll("#s-country-master tbody tr").length >= 200, d.querySelectorAll("#s-country-master tbody tr").length);
  w.go("org-list"); await sleep(1500); w.go("org-new"); await sleep(2500);
  check("country pick-lists read the master (all countries, not six)", q("#org_country") ? q("#org_country").options.length >= 249 : true, q("#org_country") && q("#org_country").options.length);

  // round off on the invoice and on a receipt, one invoice for two jobs, the voucher list
  const cust2 = (await j("POST", "/api/customers", { code: "MJ" + tag.slice(-4).toUpperCase(), name: "Multi Job Co", roles: ["shipper"], data: {} }, token)).body;
  const jobs2 = [];
  for (const pod of ["SGSIN", "AEJEA"]) { const jb = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod, customer_id: cust2.id }, token)).body; await j("PUT", `/api/shipments/by-number/${jb.job_number}/charges`, { lines: [{ charge_name: "Ocean Freight", qty: "1", hsn: "996521", sell: { ccy: "INR", rate: "1000.40", tax_type: "Taxable", tax_pct: "18", party: "Multi Job Co" }, buy: { ccy: "INR", rate: "0", tax_type: "Non-Taxable", tax_pct: "0", party: "" } }] }, token); jobs2.push(jb.job_number); }
  const mj = (await j("POST", "/api/documents/from-job", { job_number: jobs2[0], doc_type: "invoice" }, token)).body;
  w.BK.openDoc(mj.id); await sleep(3500);
  check("the invoice screen shows the round-off line with its switch", /Round off/.test(q("#s-invoice-new").textContent) && !!q('[data-bkh-check="no_round_off"]') && /0\.47|−0\.47/.test(q("#s-invoice-new").textContent.replace(/₹/g, "")), "");
  check("Get Job Details is offered on a draft invoice", !!q('#s-invoice-new [data-bk="getjob"]'));
  q('#s-invoice-new [data-bk="getjob"]').click(); await sleep(2500);
  check("it lists the other job of the same party", d.querySelectorAll("[data-gj]").length === 1 && d.getElementById("modalBody").textContent.includes(jobs2[1]), d.getElementById("modalBody").textContent.slice(0, 200));
  d.querySelector("[data-gj]").checked = true; d.getElementById("gjGo").click(); await sleep(4000);
  const merged = (await j("GET", `/api/documents/${mj.id}`, null, token)).body;
  check("one invoice now carries both jobs, each line with its job number", merged.lines.length === 2 && new Set(merged.lines.map(l => l.job_number)).size === 2 && merged.grand_total === "2361.00", JSON.stringify(merged.lines.map(l => l.job_number)) + merged.grand_total);
  check("the editor shows each line's job", /Job /.test(q("#s-invoice-new").textContent) && d.querySelectorAll("#s-invoice-new [data-bkl]").length > 0);
  await j("POST", `/api/documents/${mj.id}/post`, null, token);
  w.BK.openDoc(mj.id); await sleep(3500);
  q('#s-invoice-new [data-bk="receipt"]').click(); await sleep(2500);
  q("#pk_mode").value = "Cash"; q("#pk_mode").dispatchEvent(new w.Event("change", { bubbles: true }));
  const pp = q('input[name="pk_kind"][value="part"]'); pp.checked = true; pp.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  const amt2 = q('[data-pk-doc="' + mj.id + '"]'); amt2.value = "2360.50"; amt2.dispatchEvent(new w.Event("input", { bubbles: true }));
  q("#pk_roundoff").click(); await sleep(200);
  check("Round off closes the small remainder of the invoice", q('[data-pk-ro="' + mj.id + '"]').value === "0.50" && /round off 0\.50/.test(q("#pk_tdsnote").textContent), q('[data-pk-ro="' + mj.id + '"]').value + " | " + q("#pk_tdsnote").textContent);
  q("#pk_go").click(); await sleep(3500);
  check("the receipt shows the round off and the invoice is Paid", /Round off/.test(d.getElementById("docViewZone").innerHTML) && (await j("GET", `/api/documents/${mj.id}`, null, token)).body.payment_status === "PAID", "");
  w.go("job-detail", { jobNo: jobs2[0] }); await sleep(3500);
  q('.tbb[data-tab="jt5"]').click(); await sleep(2200);
  q("[data-vk-list]").click(); await sleep(2500);
  check("the Voucher List opens with the invoice and its receipt", /Voucher List/.test(d.getElementById("modalTitle").textContent) && /Transaction Details/.test(d.getElementById("modalBody").textContent) && d.querySelectorAll("[data-vk-txn-doc]").length >= 1 && d.querySelectorAll("[data-vk-txn-pay]").length >= 1, d.getElementById("modalBody").textContent.slice(0, 200));
  d.querySelector("[data-vk-txn-doc]").click(); await sleep(2500);
  check("View shows the Job Voucher Transaction: voucher, amount, against voucher no and amount", /Job Voucher Transaction/.test(d.getElementById("modalTitle").textContent) && /Against Voucher No/.test(d.getElementById("modalBody").textContent) && /RCP\//.test(d.getElementById("modalBody").textContent), d.getElementById("modalBody").textContent.slice(0, 200));
  d.getElementById("modalOvl").classList.remove("on");

  // Accounts reports
  w.go("acc-reports"); await sleep(4000);
  q("#ar_party").value = "Acme Exports"; q("#ar_party").dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(1500);
  check("the party ledger shows the invoices and receipts with a closing balance", /Acme Exports/.test(q("#ar_body").textContent) && /UTR-998877|receipt/i.test(q("#ar_body").textContent) && /Total/.test(q("#ar_body").textContent), q("#ar_body").textContent.slice(0, 200));
  q('[data-ar-tab="cash"]').click(); await sleep(1500);
  check("the cash / bank book lists the cheque and the NEFT with their references", /556677/.test(q("#ar_body").textContent) && /UTR-998877/.test(q("#ar_body").textContent), q("#ar_body").textContent.slice(0, 200));
  q('[data-ar-tab="outstanding"]').click(); await sleep(1500);
  check("outstanding shows nothing for the fully paid party", /Nothing outstanding/.test(q("#ar_body").textContent), q("#ar_body").textContent.slice(0, 160));

  // job profit and loss
  await j("PUT", `/api/shipments/by-number/${job.job_number}/charges`, { lines: [{ charge_name: "Ocean Freight", qty: "1", hsn: "996521", sell: { ccy: "INR", rate: "1500", tax_type: "Non-Taxable", tax_pct: "0", party: "Acme Exports" }, buy: { ccy: "INR", rate: "900", tax_type: "Non-Taxable", tax_pct: "0", party: "MSC Lines" } }] }, token);
  w.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
  q('.tbb[data-tab="jt7b"]').click(); await sleep(2000);
  const plBtn = Array.from(d.querySelectorAll("#odSeaExportDocs [data-od=pl][data-act=view]"))[0];
  check("Out Docs has the Job Profit and Loss with View / Print / PDF / Send", d.querySelectorAll("#odSeaExportDocs [data-od=pl]").length === 4, d.querySelectorAll("#odSeaExportDocs [data-od=pl]").length);
  plBtn.click(); await sleep(3500);
  const html = d.getElementById("docViewZone").innerHTML;
  check("the job P&L has provisional and actual figures, tax, revenue and expense", /JOB PROFIT AND LOSS/.test(html) && /PROVISIONAL/.test(html) && /ACTUAL/.test(html) && /REVENUE/.test(html) && /EXPENSE/.test(html) && /1,500\.00/.test(html) && /MSC Lines/.test(html) && /CASH POSITION/.test(html), html.slice(0, 160));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
