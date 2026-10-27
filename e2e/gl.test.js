// Headless UI check: ledger reports (trial balance, general ledger, P&L, balance sheet, day book), voucher viewer, journal / contra vouchers and the Bank master.
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
  const org = await j("POST", "/api/organizations", { code: "GL" + tag.slice(-5).toUpperCase(), name: "Gl " + tag, admin_email: `gl${tag}@t.test`, admin_username: "gl" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "G" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "gl" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("POST", "/api/customers", { code: "UI-G1", name: "Ui Gl Client", roles: ["shipper"], data: {} }, token);
  const doc = await j("POST", "/api/documents", { doc_type: "invoice", party_name: "Ui Gl Client", supply_type: "intra", lines: [{ charge_name: "Ocean Freight", qty: "1", rate: "10000", tax_type: "Taxable", tax_pct: "18" }] }, token);
  const posted = await j("POST", `/api/documents/${doc.body.id}/post`, null, token);
  await j("POST", "/api/payments", { direction: "RECEIPT", pay_date: new Date().toISOString().slice(0, 10), party_name: "Ui Gl Client", method: "Cash", amount: "5000.00", kind: "part", allocations: [{ document_id: doc.body.id, amount: "5000.00" }] }, token);
  check("setup: a posted invoice and a part receipt", posted.status === 200, JSON.stringify(posted.body).slice(0, 150));
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; w.URL.createObjectURL = () => "blob:x"; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s), qa = s => Array.from(d.querySelectorAll(s));
  await sleep(3500);
  const item = t => qa("#subAccounts .mn-sub-item").find(x => x.textContent.replace(/\s+/g, " ").trim().startsWith(t));
  // ---- Trial balance from the Accounts menu
  item("Trial Balance").click(); await sleep(3500);
  check("Trial Balance menu opens the ledger reports on the trial balance tab", q("#s-gl-reports").classList.contains("on") && q('[data-gl-tab="tb"]').classList.contains("btn-pri"));
  let txt = q("#gl_body").textContent;
  check("trial balance lists the debtor, income, bank and tax ledgers", /Ui Gl Client/.test(txt) && /Output CGST/.test(txt) && /Cash in Hand/.test(txt) && /SUNDRY DEBTORS/.test(txt), txt.slice(0, 200));
  check("trial balance agrees", /agrees/.test(txt));
  // ---- general ledger of the party via the link
  q('#gl_body [data-gl-ledger]') && qa('#gl_body [data-gl-ledger]').find(a => a.textContent.includes("Ui Gl Client")).click(); await sleep(2500);
  txt = q("#gl_body").textContent;
  check("clicking a ledger opens its account transactions with the running balance", q('[data-gl-tab="ledger"]').classList.contains("btn-pri") && /11,800\.00/.test(txt) && /6,800\.00 Dr/.test(txt) && /5,000\.00/.test(txt), txt.slice(0, 300));
  qa("#gl_body [data-gl-voucher]")[0].click(); await sleep(1500);
  check("a voucher number opens the voucher with balanced ledger entries", /Sales Invoice/.test(q(".modal, #modalRoot, body").textContent) && /Output SGST/.test(d.body.textContent) && !!q("[data-gl-src='document']"));
  w.closeModal(); await sleep(300);
  // ---- P&L, balance sheet, day book
  item("Profit & Loss").click(); await sleep(2500);
  check("Profit & Loss shows income and net profit", /Total income/.test(q("#gl_body").textContent) && /Freight Forwarding Income|Ocean Freight/.test(q("#gl_body").textContent), q("#gl_body").textContent.slice(0, 200));
  item("Balance Sheet").click(); await sleep(2500);
  check("Balance Sheet agrees", /The balance sheet agrees/.test(q("#gl_body").textContent) && /Liabilities/.test(q("#gl_body").textContent), q("#gl_body").textContent.slice(0, 200));
  item("Day Book").click(); await sleep(2500);
  check("Day Book lists the invoice and the receipt", /Sales Invoice/.test(q("#gl_body").textContent) && /Receipt Voucher/.test(q("#gl_body").textContent), q("#gl_body").textContent.slice(0, 200));
  // ---- Bank master
  d.querySelector('[data-go="bank-master"]') && w.go("bank-master"); await sleep(1500);
  q("#bank_new").click(); await sleep(1500);
  q("#bk_ben").value = "Ui Gl Pvt Ltd"; q("#bk_upi").value = "uigl@hdfc"; q("#bk_ifsc").value = "hdfc0000001"; q("#bk_code").value = "HDFC1"; q("#bk_name").value = "HDFC Bank"; q("#bk_acc").value = "5010099"; q("#bk_ob").value = "20000"; q("#bk_go").click(); await sleep(2000);
  check("Bank master saves and shows the bank ledger code", /HDFC Bank/.test(q("#bank_body").textContent) && /ACBC\d{4}/.test(q("#bank_body").textContent), q("#bank_body").textContent.slice(0, 200));
  // ---- contra: cash -> bank
  item("Contra Voucher").click(); await sleep(2000);
  q("#cv_new").click(); await sleep(1500);
  const rows = qa("[data-jv-row]"); const opts = Array.from(rows[0].querySelector('[data-jv="ledger"]').options);
  const bank = opts.find(o => /HDFC Bank/.test(o.textContent)), cash = opts.find(o => /Cash in Hand/.test(o.textContent));
  check("the contra form offers only cash and bank ledgers", opts.every(o => /Cash in Hand|HDFC Bank/.test(o.textContent)) && !!bank && !!cash, opts.map(o => o.textContent).join("|"));
  rows[0].querySelector('[data-jv="ledger"]').value = bank.value; rows[0].querySelector('[data-jv="debit"]').value = "1500"; rows[0].querySelector('[data-jv="debit"]').dispatchEvent(new w.Event("input", { bubbles: true }));
  rows[1].querySelector('[data-jv="ledger"]').value = cash.value; rows[1].querySelector('[data-jv="credit"]').value = "1500"; rows[1].querySelector('[data-jv="credit"]').dispatchEvent(new w.Event("input", { bubbles: true }));
  q("#jv_go").click(); await sleep(2000);
  check("contra voucher posts and appears in the list", /CT\/\d\d-\d\d\/\d{5}/.test(q("#cv_body").textContent), q("#cv_body").textContent.slice(0, 120));
  // ---- journal voucher: unbalanced is stopped
  item("Journal Voucher").click(); await sleep(1500);
  q("#jv_new").click(); await sleep(1500);
  const jr = qa("[data-jv-row]"); jr[0].querySelector('[data-jv="debit"]').value = "100"; jr[1].querySelector('[data-jv="credit"]').value = "90"; q("#jv_go").click(); await sleep(800);
  check("an unbalanced journal is not posted", q("#jv_go") !== null && !/JV\//.test(q("#jv_body").textContent));
  w.closeModal();
  // ---- GST and bank reconciliation tabs
  w.go("gl-reports", { glTab: "gst" }); await sleep(2500);
  txt = q("#gl_body").textContent;
  check("GST tab shows output GST, the sales register and agrees with the ledgers", /Output GST/.test(txt) && /Ui Gl Client/.test(txt) && /1,800\.00/.test(txt) && /Agrees with the Output/.test(txt), txt.slice(0, 250));
  w.go("gl-reports", { glTab: "recon" }); await sleep(2500);
  const rs = q("#gl_ledger"); const bopt = Array.from(rs.options).find(o => /HDFC Bank/.test(o.textContent));
  check("the reconciliation lists only bank ledgers", !!bopt && Array.from(rs.options).every(o => !o.value || /HDFC Bank/.test(o.textContent)), Array.from(rs.options).map(o => o.textContent).join("|"));
  rs.value = bopt.value; rs.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(2000);
  txt = q("#gl_body").textContent;
  check("book balance 21,500 (20,000 opening + 1,500 contra) and 1,500 not yet cleared", /21,500\.00/.test(txt) && /Depositsnotcleared1,500\.00/.test(txt.replace(/\s+/g, "")) , txt.replace(/\s+/g, " ").slice(0, 250));
  q("[data-rc-entry]").checked = true; q("#rc_clear").click(); await sleep(2000);
  txt = q("#gl_body").textContent.replace(/\s+/g, " ");
  check("marking the entry cleared moves the bank balance", /Balanceasperbank21,500\.00/.test(txt.replace(/ /g, "")), txt.slice(0, 250));
  const st = q("#rc_stmt"); st.value = "21500"; st.dispatchEvent(new w.Event("input", { bubbles: true }));
  check("a matching statement balance says Reconciled", /Reconciled/.test(q("#rc_diff").textContent));
  // ---- TDS, RCM, HSN, cash flow tabs and the payroll dialog
  for (const [tab, re, label] of [["tds", /TDS receivable[^]*TDS payable/, "TDS tab shows receivable and payable"], ["rcm", /Reverse-charge/, "RCM tab explains and lists reverse-charge entries"], ["hsn", /Taxable value[^]*Ocean Freight|Taxable value/, "HSN / SAC summary lists the supplies"], ["cash", /Money in[^]*Money out[^]*Cash inflows/, "Cash Flow shows inflows and outflows"]]) {
    w.go("gl-reports", { glTab: tab }); await sleep(2200);
    check(label, re.test(q("#gl_body").textContent), q("#gl_body").textContent.slice(0, 160));
  }
  check("cash flow counts the receipt and not the contra", /Receipt Voucher/.test(q("#gl_body").textContent) && !/Contra Voucher/.test(q("#gl_body").textContent));
  await j("POST", "/api/masters/employee", { code: "UIE1", name: "Ui Employee", data: {} }, token);
  item("Journal Voucher").click(); await sleep(1500);
  q("#jv_payroll").click(); await sleep(1800);
  q("#pr_amt").value = "12000"; q("#pr_go").click(); await sleep(2000);
  check("salary posts as a journal voucher against the employee", /JV\/\d\d-\d\d\/\d{5}/.test(q("#jv_body").textContent) && /Salary - Ui Employee/.test(q("#jv_body").textContent), q("#jv_body").textContent.slice(0, 200));
  // ---- GST calendar and TCS
  w.go("gl-reports", { glTab: "cal" }); await sleep(2500);
  check("GST calendar lists twelve periods with GSTR-1 and GSTR-3B due dates", qa("#gl_table tbody tr").length === 12 && /Due \d\d-\d\d-\d{4}/.test(q("#gl_table").textContent), q("#gl_body").textContent.slice(0, 160));
  qa("[data-cal-mark]")[0].click(); await sleep(500);
  q("#cal_ack").value = "AA2700001"; q("#cal_save").click(); await sleep(1800);
  check("marking a return filed records the date and ARN", /AA2700001/.test(q("#gl_body").textContent) && /Filed/.test(q("#gl_body").textContent), q("#gl_body").textContent.slice(0, 200));
  w.go("gl-reports", { glTab: "tcs" }); await sleep(2200);
  check("TCS tab shows both TCS ledgers", /TCS receivable/.test(q("#gl_body").textContent) && /TCS payable/.test(q("#gl_body").textContent), q("#gl_body").textContent.slice(0, 160));
  // ---- MIS
  for (const [tab, re, label] of [["exec", /Gross profit[^]*Receivable[^]*Cash and bank/, "Executive MIS shows the profit, receivable and cash figures"], ["rev", /Total revenue[^]*Ui Gl Client/, "Revenue Analysis lists the customer"], ["cust", /Ui Gl Client/, "Customer Profitability lists the customer"], ["gp", /Revenue[^]*By job/, "GP Analysis shows modes and jobs"], ["ship", /Jobs created/, "Shipment Analysis shows the job counts"], ["teu", /Total TEUs/, "Carrier Wise TEUs shows the TEU total"], ["port", /Parties[^]*Top customers/, "Customer Portfolio shows parties and top customers"], ["branch", /Unassigned \(jobs without a branch\)/, "Branch Analysis groups jobs by branch and lists the jobs without a branch as Unassigned"]]) {
    w.go("mis", { glTab: tab }); await sleep(2200);
    check(label, re.test(q("#mis_body").textContent) && q("#s-mis").classList.contains("on"), q("#mis_body").textContent.slice(0, 160));
  }
  // ---- financial year closing
  w.go("gl-reports", { glTab: "fy" }); await sleep(2500);
  check("Financial Year tab lists the current year as not closable until it ends", /Closing a year/.test(q("#gl_body").textContent) && /Current/.test(q("#gl_body").textContent) && /has not ended yet/.test(q("#gl_body").textContent) && q("[data-fy-close]").disabled === true, q("#gl_body").textContent.slice(0, 220));
  // ---- ITC / GSTR-2B
  await j("POST", "/api/customers", { code: "UI-V1", name: "Ui Itc Vendor", roles: ["carrier"], data: {} }, token);
  const nowD = new Date(); const per = nowD.getFullYear() + "-" + String(nowD.getMonth() + 1).padStart(2, "0"); const dd = nowD.toISOString().slice(0, 10);
  const pb = await j("POST", "/api/documents", { doc_type: "purchase_invoice", party_name: "Ui Itc Vendor", party_gstin: "27AAAAA0000A1Z5", header: { vendor_inv_no: "VI/0042", vendor_inv_date: dd }, lines: [{ charge_name: "Ocean Freight", qty: "1", rate: "1000", tax_type: "Taxable", tax_pct: "18" }] }, token);
  await j("POST", `/api/documents/${pb.body.id}/post`, null, token);
  w.BK.openNew("purchase_invoice"); await sleep(3000);
  check("the purchase editor asks for the vendor invoice number and date", !!q('#s-purchase-invoice-new [data-bkh="vendor_inv_no"]') && !!q('#s-purchase-invoice-new [data-bkh="vendor_inv_date"]'));
  w.go("gl-reports", { glTab: "itc" }); await sleep(2500);
  w.GLKit.itcPeriod = per; w.GLKit.load(); await sleep(1500);
  q("#itc_import").click(); await sleep(500);
  const portal = { data: { rtnprd: per.slice(5) + per.slice(0, 4), docdata: { b2b: [{ ctin: "27AAAAA0000A1Z5", trdnm: "Ui Itc Vendor", inv: [{ inum: "VI-42", dt: dd.split("-").reverse().join("-"), val: 1180, txval: 1000, igst: 180, cgst: 0, sgst: 0, itcavl: "Y" }] }, { ctin: "29BBBBB1111B1Z5", trdnm: "Other Supplier", inv: [{ inum: "X1", dt: dd.split("-").reverse().join("-"), txval: 500, igst: 90 }] }], cdnr: [] } } };
  q("#ib_text").value = JSON.stringify(portal); q("#ib_text").dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(300);
  check("the portal JSON is read: two documents found and the period filled in", /2 document\(s\) found/.test(q("#ib_out").textContent) && q("#ib_period").value === per, q("#ib_out").textContent);
  q("#ib_go").click(); await sleep(2500);
  const tx = q("#gl_body").textContent;
  check("the bill matches its GSTR-2B row despite the different invoice number format, and the other supplier shows as not booked", /Matched/.test(tx) && /Not in books/.test(tx) && /Ui Itc Vendor/.test(tx) && /Other Supplier/.test(tx), tx.slice(0, 300));
  // ---- the receipt dialog offers the bank account
  const bankLedger = (await j("GET", "/api/coa/ledgers?source_type=bank", null, token)).body;
  check("the bank master created its ledger under Bank Accounts", bankLedger.length === 1 && /BANK ACCOUNTS/.test(bankLedger[0].path) && bankLedger[0].opening_balance === "20000.00");
  const tbk = (await j("GET", "/api/gl/trial-balance", null, token)).body;
  check("an unmatched opening balance is reported as a difference, not hidden", tbk.balanced === false && Number(tbk.totals.closing_dr) - Number(tbk.totals.closing_cr) === 20000, JSON.stringify(tbk.totals));
  const bsk = (await j("GET", "/api/gl/balance-sheet", null, token)).body;
  check("and the balance sheet shows that difference", bsk.balanced === false && Number(bsk.difference) === 20000, JSON.stringify(bsk).slice(0, 200));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
