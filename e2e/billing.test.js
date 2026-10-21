// Headless UI check #5: billing documents and payments, end to end (real API + QA database).
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
  const org = await j("POST", "/api/organizations", { code: "BL" + tag.slice(-6).toUpperCase(), name: "Billing " + tag, admin_email: `bl${tag}@t.test`, admin_username: "bl" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Bl" }, dev.body.access_token);
  const login = await j("POST", "/api/auth/login", { identifier: "bl" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number });
  const token = login.body.access_token;
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR" }, token)).body.job_number;
  await j("PUT", `/api/shipments/by-number/${job}/charges`, { lines: [
    { charge_name: "Ocean Freight", qty: "2", hsn: "996512", buy: { ccy: "USD", exch: "83.1", rate: "680", tax_type: "Non-Taxable", party: "MSC", use_party: true }, sell: { ccy: "USD", exch: "83.5", rate: "850", tax_type: "Taxable", tax_pct: "18", party: "Acme Ltd", use_party: true } },
    { charge_name: "THC", qty: "2", hsn: "996719", buy: { ccy: "INR", rate: "5200", tax_type: "Taxable", tax_pct: "18", party: "Port Trust", use_party: true }, sell: { ccy: "INR", rate: "6500", tax_type: "Taxable", tax_pct: "18", party: "Acme Ltd", use_party: true } }] }, token);

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
  const clearToasts = () => d.querySelectorAll("#toastStack .toast").forEach(x => x.remove());
  const q = s => d.querySelector(s);
  const click = (sel) => { const el = typeof sel === "string" ? q(sel) : sel; el.click(); };
  const text = sel => (q(sel) || { textContent: "" }).textContent.replace(/\s+/g, " ");

  // --- generate an invoice from the job's charges
  w.go("job-detail", { jobNo: job }); await sleep(2200);
  check("job generation row starts as Not Generated", /Not Generated/.test(text("#jc_genRow")));
  click("#jc_genInvoiceBtn"); await sleep(2500);
  const editor = "#s-invoice-new";
  check("Generate Invoice opens a DRAFT editor built from the job charges", /Tax Invoice/.test(text(editor)) && /PF\/\d\d-\d\d\/\d{5} \(proforma\)/.test(text(editor)) && d.querySelectorAll(editor + " tbody tr").length === 2, text(editor).slice(0, 200));
  check("editor shows server-calculated totals", /1,82,841\.00/.test(text(editor)) && /27,891\.00/.test(text(editor)), text(editor).slice(0, 400));
  check("party was taken from the sell-side client", q(editor + ' [data-bkh="party_name"]').value === "Acme Ltd");
  q(editor + ' [data-bkh="place_of_supply"]').value = "Tamil Nadu"; q(editor + ' [data-bkh="place_of_supply"]').dispatchEvent(new w.Event("input", { bubbles: true }));
  clearToasts(); click(editor + ' [data-bk="save"]'); await sleep(1200);
  check("Save Draft persists", toastsNow().some(t => /draft saved/i.test(t)), toastsNow().join(" ; "));
  clearToasts(); click(editor + ' [data-bk="post"]'); await sleep(1500);
  const invList = (await j("GET", "/api/documents?doc_type=invoice", null, token)).body;
  check("Post assigns a legal number and locks the document", invList.length === 1 && /^INV\/\d\d-\d\d\/00001$/.test(invList[0].doc_number) && invList[0].status === "POSTED" && invList[0].place_of_supply === "Tamil Nadu", JSON.stringify(invList[0]));
  check("posted editor is read-only", q(editor + ' [data-bkh="party_name"]').disabled === true && !q(editor + ' [data-bk="post"]'));
  const inv = invList[0];

  // --- job generation row now shows the invoice; duplicate generation is refused by the server
  w.go("job-detail", { jobNo: job }); await sleep(2200);
  check("generation row shows the invoice number", new RegExp(inv.doc_number.replace(/\//g, "\\/")).test(text("#jc_genRow")), text("#jc_genRow"));

  // --- list screen
  w.go("invoice-list"); await sleep(1200);
  check("invoice list shows the real invoice (no dummy rows)", d.querySelectorAll("#s-invoice-list tbody tr").length === 1 && text("#s-invoice-list tbody").includes(inv.doc_number) && !/Tata|RGC-INV/.test(text("#s-invoice-list tbody")), text("#s-invoice-list tbody"));

  // --- partial receipt via the modal
  click('#s-invoice-list [data-bkopen="' + inv.id + '"]'); await sleep(1500);
  click(editor + ' [data-bk="receipt"]'); await sleep(1500);
  check("receipt modal lists the invoice with its outstanding balance", /1,82,841\.00/.test(text("#modalBody")));
  d.querySelector('input[name="pk_kind"][value="part"]').checked = true; d.querySelector('input[name="pk_kind"][value="part"]').dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  d.querySelector("[data-pk-doc]").value = "50000"; d.querySelector("[data-pk-doc]").dispatchEvent(new w.Event("input", { bubbles: true }));
  check("the amount received is the sum of what is settled", d.getElementById("pk_amount").value === "50000.00" && d.querySelector("[data-pk-doc]").value === "50000", d.getElementById("pk_amount").value);
  d.getElementById("pk_mode").value = "Bank Transfer"; d.getElementById("pk_mode").dispatchEvent(new w.Event("change", { bubbles: true }));
  click("#pk_go"); click("#pk_go"); await sleep(2500);
  const pays = (await j("GET", "/api/payments", null, token)).body;
  check("receipt recorded exactly once even with a double click", pays.length === 1 && pays[0].amount === "50000.00" && /^RCP\//.test(pays[0].pay_number), JSON.stringify(pays.map(p => p.pay_number)));
  const afterPay = (await j("GET", `/api/documents/${inv.id}`, null, token)).body;
  check("invoice became PARTIAL with the right balance", afterPay.payment_status === "PARTIAL" && afterPay.outstanding === "132841.00", JSON.stringify(afterPay));
  check("the receipt voucher opens after recording", /RECEIPT VOUCHER/.test(text("#docViewZone")), text("#docViewZone").slice(0, 80));
  w.BK.openDoc(inv.id); await sleep(2500);
  check("editor shows paid and outstanding", /1,32,841\.00/.test(text(editor)) && /50,000\.00/.test(text(editor)));
  w.go("payment"); await sleep(1200);
  check("payments screen lists the receipt and KPI", text("#s-payment tbody").includes(pays[0].pay_number) && /50,000\.00/.test(text("#s-payment .kpi-row")), text("#s-payment tbody") + text("#s-payment .kpi-row"));

  // --- credit note against the invoice
  w.BK.openDoc(inv.id); await sleep(1200);
  click(editor + ' [data-bk="note"]'); await sleep(1500);
  const noteScreen = "#s-debit-note-new";
  check("Issue Credit Note opens a draft referencing the invoice", /Credit Note/.test(text(noteScreen)) && q(noteScreen + ' [data-bkh="ref_document_id"]').value === String(inv.id) && q(noteScreen + ' [data-bkh="party_name"]').value === "Acme Ltd", text(noteScreen).slice(0, 200));
  const line = (f, v) => { const el = q(noteScreen + ` [data-bkl="0"][data-f="${f}"]`); el.value = v; el.dispatchEvent(new w.Event("input", { bubbles: true })); };
  line("charge_name", "Rate adjustment"); line("rate", "10000"); line("tax_pct", "0");
  clearToasts(); click(noteScreen + ' [data-bk="post"]'); await sleep(2000);
  const afterCn = (await j("GET", `/api/documents/${inv.id}`, null, token)).body;
  check("posting the credit note reduces the invoice balance", afterCn.credited_amount === "10000.00" && afterCn.outstanding === "122841.00", JSON.stringify(afterCn));

  // --- purchase invoice: several vendors -> choose one
  w.go("job-detail", { jobNo: job }); await sleep(2200);
  click("#jc_genPurInvoiceBtn"); await sleep(1800);
  check("purchase invoice generation asks which vendor to bill", /Choose vendor/.test(text("#modalTitle")) && /MSC/.test(text("#modalBody")) && /Port Trust/.test(text("#modalBody")), text("#modalBody"));
  d.getElementById("bk_vendor_pick").value = "MSC"; click("#bk_vendor_go"); await sleep(2500);
  const pi = "#s-purchase-invoice-new";
  check("vendor purchase invoice draft is created with buy-side amounts", /1,13,016\.00/.test(text(pi)) && q(pi + ' [data-bkh="party_name"]').value === "MSC", text(pi).slice(0, 300));
  click(pi + ' [data-bk="post"]'); await sleep(1800);
  const pis = (await j("GET", "/api/documents?doc_type=purchase_invoice", null, token)).body;
  check("purchase invoice posted with PI number", pis.length === 1 && /^PI\//.test(pis[0].doc_number));

  // --- validation
  w.go("invoice-new"); await sleep(1500);
  clearToasts(); click("#s-invoice-new" + ' [data-bk="post"]'); await sleep(1500);
  check("posting an empty invoice is rejected and explained", toastsNow().some(t => /positive total|party name|at least one line/i.test(t)), toastsNow().join(";"));
  check("no extra document was created by the failed post", (await j("GET", "/api/documents?doc_type=invoice&status=POSTED", null, token)).body.length === 1);

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
