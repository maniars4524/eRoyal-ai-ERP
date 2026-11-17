// Dashboard AR Outstanding -> list from the debtor ledgers; the Tax Invoice Register cards count correctly and the page does not redraw itself
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
  const org = await j("POST", "/api/organizations", { code: "AR" + tag, name: "Ar " + tag, admin_email: `ar${tag}@t.test`, admin_username: "ar" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ar" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const mk = async (type, party, rate, ref) => { const d = (await j("POST", "/api/documents", { doc_type: type, party_name: party, supply_type: "intra", ref_document_id: ref, lines: [{ charge_name: "X", qty: "1", rate, tax_type: "Taxable", tax_pct: "18" }] }, token)).body; return (await j("POST", `/api/documents/${d.id}/post`, null, token)).body; };
  const A = "ALPHA " + tag, B = "BETA " + tag, G = "GAMMA " + tag;
  const i1 = await mk("invoice", A, "10000"), i2 = await mk("invoice", B, "20000"); await mk("invoice", G, "1000");
  const cn = await mk("credit_note", A, "1000", i1.id);
  await j("POST", "/api/payments", { direction: "RECEIPT", party_name: B, amount: "5000", allocations: [{ document_id: i2.id, amount: "5000" }] }, token);
  // expected from the ledgers: ALPHA 11,800 - 1,180 = 10,620 ; BETA 23,600 - 5,000 = 18,600 ; GAMMA 1,180
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = [];
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => d.accept());
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(4500);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);

  // 1. the dashboard card leads to the ledger list
  await page.evaluate(() => document.querySelector('#dashXBody .dx-kpi[data-dx-go="ar-outstanding"]').click()); await sleep(2500);
  check("AR Outstanding card opens the AR Outstanding screen", await page.evaluate(() => document.querySelector(".scr.on").id === "s-ar-outstanding"));
  const t = await text("#s-ar-outstanding");
  check("list comes from the ledgers: BETA 18,600.00, ALPHA 10,620.00, GAMMA 1,180.00", ["18,600.00", "10,620.00", "1,180.00"].every(x => t.includes(x)), t.slice(0, 400));
  check("total AR outstanding 30,400.00 and it matches the Sundry Debtors ledger group", t.includes("30,400.00"), t.slice(0, 300));
  const order = await page.evaluate(() => [...document.querySelectorAll("#arBody tr td:first-child b")].map(b => b.textContent.split(" ")[0]));
  check("largest balance first", order.join() === "BETA,ALPHA,GAMMA", order.join());
  await page.evaluate(() => document.querySelector('#arBody [data-ar-ledger]').click()); await sleep(1800);
  const mt = await text("#modalBody");
  check("a customer's ledger opens with its vouchers", /INV\//.test(mt) && /18,600\.00/.test(mt), mt.slice(0, 200));
  await page.evaluate(() => window.closeModal()); await sleep(300);
  await page.evaluate(() => { const q = document.querySelector("#arQ"); q.value = "alpha"; q.dispatchEvent(new Event("input", { bubbles: true })); });
  check("search narrows the list", (await page.evaluate(() => document.querySelectorAll("#arBody tr").length)) === 1);
  await page.evaluate(() => window.go("dashboard")); await sleep(800);
  const menuItem = await page.evaluate(() => { const e = [...document.querySelectorAll('[data-go="ar-outstanding"]')].find(x => /Outstanding Receivable \(Ledger\)/.test(x.textContent)); if (!e) return false; e.click(); return true; }); await sleep(2000);
  check("the menu entry Outstanding Receivable (Ledger) leads here", menuItem && await page.evaluate(() => document.querySelector(".scr.on").id === "s-ar-outstanding"), String(menuItem));

  // 2. the Tax Invoice Register: cards count the real statuses
  await page.evaluate(() => window.go("invoice-list")); await sleep(3500);
  const cards = await page.evaluate(() => [...document.querySelectorAll('#s-invoice-list .dash-cards[data-dash] .kpi')].map(k => k.innerText.replace(/\s+/g, " ").trim()));
  check("register cards: TOTAL 3, UNPAID 1, PART PAID 2, PAID 0", /TOTAL 3/i.test(cards[0]) && /UNPAID 1/i.test(cards[2]) && /PART PAID 2/i.test(cards[3]) && /PAID 0/i.test(cards[4]), cards.join(" | "));
  const money = await page.evaluate(() => [...document.querySelectorAll("#s-invoice-list .kpi-row .kpi, #s-invoice-list .dash-cards:not([data-dash]) .kpi")].map(k => k.innerText.replace(/\s+/g, " ").trim()));
  check("register money cards: outstanding 30,400.00 and received this month 5,000.00 with a real average age", money.join(" ").includes("30,400.00") && money.join(" ").includes("5,000.00") && /days/i.test(money.join(" ")) && !/Drafts/.test(money.join(" ")), money.join(" | "));
  await page.evaluate(() => document.querySelector('#s-invoice-list [data-dcard="3"]').click()); await sleep(600);
  const shown = await page.evaluate(() => [...document.querySelectorAll("#s-invoice-list tbody tr")].filter(r => r.children.length > 4 && r.style.display !== "none").length);
  check("clicking Part Paid shows its 2 rows", shown === 2, String(shown));
  await page.evaluate(() => document.querySelector('#s-invoice-list [data-dcard="0"]').click()); await sleep(400);
  // 3. nothing shakes: the page does not redraw itself
  const muts = await page.evaluate(async () => { let n = 0; const mo = new MutationObserver(l => { n += l.length; }); mo.observe(document.body, { childList: true, subtree: true, characterData: true }); await new Promise(r => setTimeout(r, 4000)); mo.disconnect(); return n; });
  check("the register stays still (" + muts + " page changes in 4 seconds)", muts < 40, String(muts));
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
