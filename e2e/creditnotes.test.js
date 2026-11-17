// Credit notes through the real screens: against an invoice (difference amount and chosen entries), against a purchase invoice (PCN) and against the job; each is saved, posted, found in the lists and checked in the ledger
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); let b = null; try { b = await r.json(); } catch (e) {} return { status: r.status, body: b }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (n, ok, x) => { console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + (x || ""))); if (!ok) failures++; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "CN" + tag, name: "Cn " + tag, admin_email: `cn${tag}@t.test`, admin_username: "cn" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "cn" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const api = async p => (await j("GET", p, null, token)).body;
  const cust = "ABC EXPORTS " + tag, vend = "CARRIER X " + tag, agent = "AGENT USA " + tag;
  await j("POST", "/api/customers", { code: "AB" + tag, name: cust, roles: ["shipper"], data: {} }, token);
  await j("POST", "/api/customers", { code: "CX" + tag, name: vend, roles: ["carrier"], data: {} }, token);
  await j("POST", "/api/customers", { code: "AU" + tag, name: agent, roles: ["agent"], data: {} }, token);
  const job1 = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "USNYC" }, token)).body.job_number;
  const job2 = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "SGSIN" }, token)).body.job_number;
  const mk = async (doc_type, party, job, rate, extra) => { const d = (await j("POST", "/api/documents", { doc_type, party_name: party, job_number: job, supply_type: "intra", lines: [{ charge_name: "Ocean Freight", qty: "1", rate, tax_type: "Taxable", tax_pct: "18", job_number: job, hsn: "996521" }].concat(extra || []) }, token)).body; return (await j("POST", `/api/documents/${d.id}/post`, null, token)).body; };
  const inv = await mk("invoice", cust, job1, "10000", [{ charge_name: "THC", qty: "2", rate: "1000", tax_type: "Taxable", tax_pct: "18", job_number: job1, hsn: "996719" }]);   // 12,000 + 2,160
  const pi = await mk("purchase_invoice", vend, job1, "10000");
  check("invoice and purchase invoice posted", !!(inv.doc_number && pi.doc_number), JSON.stringify([inv, pi]).slice(0, 200));
  const ledger = async name => { const l = (await api("/api/coa/ledgers")).find(x => x.name === name); return (await api("/api/gl/ledger?ledger_id=" + l.id)).closing; };

  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1100 }); const errs = [];
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => d.accept());
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(3000);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const setv = (sel, val) => page.evaluate((sel, val) => { const e = document.querySelector(sel); e.value = val; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }, sel, val);
  const openDoc = async id => { await page.evaluate(i => window.BK.openDoc(i), id); await sleep(1800); };
  const saveAndPost = async () => { await click('.scr.on [data-bk="save"]'); await sleep(1500); await click('.scr.on [data-bk="post"]'); await sleep(2200); };

  // 1. credit note against the invoice: only the difference amount
  await openDoc(inv.id);
  check("invoice screen has Issue Credit Note", await page.evaluate(() => !!document.querySelector('[data-bk="note"]')));
  await click('.scr.on [data-bk="note"]'); await sleep(1500);
  const opts = await page.evaluate(() => [...document.querySelectorAll("#vkMode option")].map(o => o.value).join());
  check("chooser offers difference / entries / full / part / job", opts === "diff,entries,full,part,job", opts);
  await setv("#vkAmt", "1500"); await click("#vkGo"); await sleep(2000);
  check("credit note form opens against the invoice with the difference line", await page.evaluate(i => { const s = document.querySelector('.scr.on [data-bkh="ref_document_id"]'); return !!s && s.value === String(i) && document.querySelector('.scr.on [data-bkl="0"][data-f="charge_name"]').value === "Credit for difference" && document.querySelector('.scr.on [data-bkl="0"][data-f="rate"]').value === "1500"; }, inv.id));
  await saveAndPost();
  let invNow = await api("/api/documents/" + inv.id);
  check("posted: invoice credited 1,770 (1,500 + 18% GST) and outstanding reduced", Number(invNow.credited_amount) === 1770 && Number(invNow.outstanding) === 12000 * 1.18 - 1770, JSON.stringify([invNow.credited_amount, invNow.outstanding]));
  check("ledger: customer balance reduced by the credit note", Number(await ledger(cust)) === 12000 * 1.18 - 1770, await ledger(cust));

  // 2. credit note against the invoice: chosen entries (THC, quantity 1)
  await openDoc(inv.id); await click('.scr.on [data-bk="note"]'); await sleep(1500);
  await page.select("#vkMode", "entries"); await sleep(800);
  await page.evaluate(() => { document.querySelector('[data-vke="1"]').click(); const q = document.querySelector('[data-vkq="1"]'); q.value = "1"; });
  await click("#vkGo"); await sleep(2000);
  check("entries: the THC line is copied with the quantity to credit", await page.evaluate(() => document.querySelector('.scr.on [data-bkl="0"][data-f="charge_name"]').value === "THC" && document.querySelector('.scr.on [data-bkl="0"][data-f="qty"]').value === "1"));
  await saveAndPost();
  invNow = await api("/api/documents/" + inv.id);
  check("posted: THC x1 credited (1,000 + 180)", Number(invNow.credited_amount) === 1770 + 1180, invNow.credited_amount);

  // 3. purchase credit note: more was entered than the vendor bill
  await openDoc(pi.id); await click('.scr.on [data-bk="note"]'); await sleep(1500);
  await setv("#vkAmt", "2000"); await click("#vkGo"); await sleep(2000);
  check("PCN form opens against the purchase invoice", await page.evaluate(i => { const s = document.querySelector('.scr.on [data-bkh="ref_document_id"]'); return !!s && s.value === String(i); }, pi.id));
  await saveAndPost();
  const piNow = await api("/api/documents/" + pi.id);
  check("posted: purchase invoice credited 2,360 and the vendor ledger falls", Number(piNow.credited_amount) === 2360 && Number(await ledger(vend)) === -(11800 - 2360), JSON.stringify([piNow.credited_amount, await ledger(vend)]));

  // 4. credit note against the job (no invoice in job2)
  await page.evaluate(j2 => window.VoucherKit.start("credit_note", j2), job2); await sleep(2200);
  check("job without invoice: credit note against the job asks for the party", await page.evaluate(() => !!document.querySelector("#vkParty")));
  await setv("#vkParty", agent); await click("#vkJobGo"); await sleep(2000);
  await setv('.scr.on [data-bkl="0"][data-f="charge_name"]', "Net credit of the job"); await setv('.scr.on [data-bkl="0"][data-f="rate"]', "5000"); await setv('.scr.on [data-bkl="0"][data-f="tax_type"]', "Non-Taxable"); await sleep(500);
  await saveAndPost();
  const cns = (await api("/api/documents?doc_type=credit_note&limit=100")).items || (await api("/api/documents?doc_type=credit_note&limit=100"));
  const jobCn = (Array.isArray(cns) ? cns : []).find(d => d.job_number === job2);
  check("job-level credit note posted without an invoice", !!jobCn && jobCn.status === "POSTED" && !jobCn.ref_document_id, JSON.stringify(jobCn || cns).slice(0, 200));
  check("ledger: the agent is credited 5,000", Number(await ledger(agent)) === -5000, await ledger(agent));

  // 5. navigation: lists, the credit note shows its invoice, the invoice lists its credit notes
  const all = await api("/api/documents?limit=200"); const list = Array.isArray(all) ? all : all.items;
  const cn1 = list.find(d => d.doc_type === "credit_note" && d.ref_document_id === inv.id);
  await page.evaluate(() => window.go("debit-note-list")); await sleep(2000);
  check("Debit / Credit Note list shows the credit notes", cn1 && (await text("#s-debit-note-list")).includes(cn1.doc_number), cn1 && cn1.doc_number);
  await page.evaluate(() => window.go("purchase-invoice-list")); await sleep(2000);
  const pcn = list.find(d => d.doc_type === "purchase_credit_note");
  check("Purchase list shows the PCN", pcn && (await text("#s-purchase-invoice-list")).includes(pcn.doc_number), pcn && pcn.doc_number);
  await openDoc(inv.id);
  check("invoice screen lists its credit notes", cn1 && (await text("#s-invoice-new")).includes(cn1.doc_number) || (await page.evaluate(n => document.body.innerText.includes(n), cn1.doc_number)));
  await openDoc(cn1.id);
  check("credit note shows the invoice it credits", await page.evaluate(i => document.querySelector('.scr.on [data-bkh="ref_document_id"]').value === String(i), inv.id));
  // 6. printing: Template two names the invoice it credits
  await page.evaluate(i => window.DocKit.view("doc", { docId: i }), cn1.id); await sleep(2500);
  await page.select("#dkTpl", "two"); await sleep(2500);
  const pt = await text("#docViewZone");
  check("printed credit note (Template two) names the invoice", pt.includes("Against Invoice") && pt.includes(inv.doc_number), pt.slice(0, 300));
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
