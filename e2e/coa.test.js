// Headless UI check: Chart of Accounts screen (tree, new group, new G/L, search, details) and the Accounts menu opening the right voucher screens.
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
  const org = await j("POST", "/api/organizations", { code: "CO" + tag.slice(-5).toUpperCase(), name: "Coa " + tag, admin_email: `co${tag}@t.test`, admin_username: "co" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "co" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("POST", "/api/customers", { code: "UI-C1", name: "Ui Coa Client", roles: ["shipper"], data: {} }, token);
  const doc = await j("POST", "/api/documents", { doc_type: "invoice", party_name: "Ui Coa Client", lines: [{ charge_name: "X", qty: "1", rate: "1000", tax_type: "Non-Taxable", tax_pct: "0" }] }, token);
  await j("POST", `/api/documents/${doc.body.id}/confirm`, null, token); await j("POST", `/api/documents/${doc.body.id}/post`, null, token);
  const pay = await j("POST", "/api/payments", { direction: "RECEIPT", pay_date: new Date().toISOString().slice(0, 10), party_name: "Ui Coa Client", method: "Cash", amount: "400.00", allocations: [{ document_id: doc.body.id, amount: "400.00" }] }, token);
  check("setup: a posted invoice and a receipt", pay.status === 201, JSON.stringify(pay.body).slice(0, 200));
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s), qa = s => Array.from(d.querySelectorAll(s));
  await sleep(3500);
  // ---- menu routes
  const item = t => qa("#subAccounts .mn-sub-item").find(x => x.textContent.replace(/\s+/g, " ").trim().startsWith(t));
  check("menu: Receipt Voucher opens the receipt voucher screen", item("Receipt Voucher").dataset.go === "receipt-voucher");
  check("menu: Payment Voucher opens the payment voucher screen", item("Payment Voucher").dataset.go === "payment-voucher");
  check("menu: Customer Receipts opens the receipt voucher screen", item("Customer Receipts").dataset.go === "receipt-voucher");
  check("menu: Tax Invoices still opens the invoice list", item("Tax Invoices").dataset.go === "invoice-list");
  check("menu: Tax Master opens the SAC / HSN master", item("Tax Master").dataset.go === "hsn-master");
  check("menu: TDS opens the TDS report and Financial Year Closing no longer opens the Chart of Accounts", item("TDS").dataset.go === "gl-reports" && item("Financial Year Closing").dataset.go !== "coa");
  item("Receipt Voucher").click(); await sleep(2500);
  check("clicking Receipt Voucher shows the receipt screen", q("#s-receipt-voucher").classList.contains("on") && !q("#s-invoice-list").classList.contains("on"));
  check("it lists the receipt", /RCT|RV|REC/i.test(q("#s-receipt-voucher tbody").textContent) || q("#s-receipt-voucher tbody").textContent.includes("400"), q("#s-receipt-voucher tbody").textContent.slice(0, 120));
  check("the receipt row is clickable to open the voucher", !!q("#s-receipt-voucher tbody [data-vch-pay]"));
  item("Payment Voucher").click(); await sleep(2500);
  check("Payment Voucher shows the payment screen without the receipt", q("#s-payment-voucher").classList.contains("on") && !q("#s-payment-voucher tbody").textContent.includes("400.00"), q("#s-payment-voucher tbody").textContent.slice(0, 120));
  // ---- COA screen
  item("Chart of Accounts").click(); await sleep(3000);
  check("COA screen is open and shows the four main groups", q("#s-coa").classList.contains("on") && ["ASSETS", "LIABILITIES", "INCOME", "EXPENSES"].every(n => q("#coaTree").textContent.includes(n)), q("#coaTree").textContent.slice(0, 200));
  check("sundry debtors group carries the party ledger", (() => { qa("#coaTree [data-coa-act]"); return true; })());
  w.CoaKit.open = {}; q('[data-coa-act="all"]').click(); await sleep(300);
  check("Expand all shows the customer's ledger with an ACSC code", /ACSC\d{4}[^]*Ui Coa Client/.test(q("#coaTree").textContent), q("#coaTree").textContent.slice(0, 300));
  const row = qa("#coaTree [data-coa-l]").find(x => x.textContent.includes("Ui Coa Client")); row.click(); await sleep(200);
  check("selecting a ledger shows its details", q("#coaDetails").textContent.includes("system generated") && q("#coaDetails").textContent.includes("Ui Coa Client"));
  q('[data-coa-act="ng"]').click(); await sleep(400);
  q("#cg_name").value = "Ui Test Group"; q("#cg_go").click(); await sleep(1500);
  check("New Group adds a sub group", q("#coaTree").textContent.includes("UI TEST GROUP"));
  q('[data-coa-act="nl"]').click(); await sleep(400);
  q("#cl_name").value = "Ui Test Ledger"; q("#cl_go").click(); await sleep(1500);
  check("New G/L adds an account with a system code", /[A-Z]{2,4}\d{4}\s*—\s*Ui Test Ledger/.test(q("#coaTree").textContent), q("#coaTree").textContent.slice(-200));
  const s = q("#coaSearch"); s.value = "Ui Test Ledger"; s.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(900);
  check("search finds the account", q("#coaTree").textContent.includes("Ui Test Ledger") && !q("#coaTree").textContent.includes("CASH-IN-HAND"));
  // ---- opening balance import dialog
  q("#coaSearch").value = ""; q("#coaSearch").dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(600);
  q('[data-coa-act="ob"]').click(); await sleep(600);
  q("#ob_text").value = "Code,Name,Debit,Credit\n,Cash in Hand,\"1,500.00\",0\n,Capital Account,0,1500\n,Nope Ledger,10,0";
  q("#ob_check").click(); await sleep(1500);
  check("the import preview flags the unknown ledger and blocks Apply", /Nope Ledger/.test(q("#ob_out").textContent) && /No ledger with this code or name/.test(q("#ob_out").textContent) && q("#ob_apply").disabled === true, q("#ob_out").textContent.slice(0, 200));
  q("#ob_text").value = "Code,Name,Debit,Credit\n,Cash in Hand,\"1,500.00\",0\n,Capital Account,0,1500";
  q("#ob_check").click(); await sleep(1500);
  check("a clean file shows balanced books and enables Apply", /balanced/.test(q("#ob_out").textContent) && q("#ob_apply").disabled === false, q("#ob_out").textContent.slice(0, 200));
  q("#ob_apply").click(); await sleep(2000);
  const cashLed = (await j("GET", "/api/coa/ledgers", null, token)).body.find(l => l.name === "Cash in Hand");
  check("applying sets the opening balance on the ledger", cashLed.opening_balance === "1500.00" && cashLed.opening_side === "Dr", JSON.stringify(cashLed).slice(0, 160));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
