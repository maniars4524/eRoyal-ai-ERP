// Real Chrome: the cargo dimensions popup on an Air quotation, and on jobs (Air, Sea LCL, FCL, consolidation by house reference), with quotation -> job.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (n, ok, x) => { console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + (x || ""))); if (!ok) failures++; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "IP" + tag, name: "Inv " + tag, admin_email: `ip${tag}@t.test`, admin_username: "ip" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ip" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const api = async p => (await j("GET", p, null, token)).body;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = []; let confirms = 0;
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => { if (d.type() === "confirm") confirms++; d.accept(); });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const setv = (sel, val) => page.evaluate((sel, val) => { const e = document.querySelector(sel); e.value = val; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }, sel, val);
  // the menu
  const menu = await page.evaluate(() => { const rows = Array.from(document.querySelectorAll(".tn-row, .mn-sub-item")); return rows.map(r => (r.querySelector(".tn-label") || r).textContent.trim()); });
  check("CHA has the Invoice & Packing List entry after Air Import", menu.includes("Invoice & Packing List") && menu.indexOf("Invoice & Packing List") > menu.indexOf("Air Import"), menu.filter(x => /Air Import|Invoice/.test(x)).join("|"));
  await page.evaluate(() => window.go("cha-invpl")); await sleep(1500);
  check("the screen opens with the list and New", /Invoice & Packing List/.test(await text("#s-cha-invpl")) && /No invoice and packing list yet/.test(await text("#s-cha-invpl")), (await text("#s-cha-invpl")).slice(0, 200));
  await page.evaluate(() => document.getElementById("ipNew").click()); await sleep(800);
  await page.evaluate(() => { const r = document.querySelector('input[name="ipNoMode"][value="manual"]'); r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true })); }); await setv("#ip_no", "1333"); await setv("#ip_exref", "AAGCE4436R"); await setv("#ip_buyord", "buy ord no.009");
  await setv("#ip_exporter", "PSR EXPORTERS\n2 C, 202 Damodar Park, Mumbai"); await setv("#ip_consignee", "SYED TRADING CO. LLC\nP.O. Box 12345, Dubai"); await setv("#ip_port_of_loading", "Bengaluru, India"); await setv("#ip_port_of_discharge", "Dubai"); await setv("#ip_vessel_flight", "EK-215");
  await page.evaluate(() => document.querySelector('[data-ip="tab:charge"]').click()); await sleep(400);
  await setv("#ip_cur", "USD"); await setv("#ip_curvalue", "74.45"); await setv("#ip_addpct", "2");
  await setv('[data-ipl="0:pkgs"]', "10"); await setv('[data-ipl="0:description"]', "GARMENTS"); await setv('[data-ipl="0:qty"]', "2"); await setv('[data-ipl="0:rate"]', "200"); await setv('[data-ipl="0:hs"]', "6109");
  await page.evaluate(() => document.querySelector('[data-ip="add"]').click()); await sleep(300);
  await setv('[data-ipl="1:pkgs"]', "5"); await setv('[data-ipl="1:description"]', "SHIRTS"); await setv('[data-ipl="1:qty"]', "3"); await setv('[data-ipl="1:rate"]', "10");
  const tot = await page.evaluate(() => ["ip_tpkgs", "ip_tnet", "ip_tgross", "ip_tcif", "ip_rscif"].map(i => document.getElementById(i).value).join("|"));
  check("the totals follow the lines (15 pkgs, net 5, gross 5.10, CIF 430, Rs. 32,013.50)", tot === "15|5.000|5.100|430.00|32,013.50", tot);
  check("the amount in words is filled", /Four Hundred Thirty/.test(await page.evaluate(() => document.getElementById("ip_words").value)), await page.evaluate(() => document.getElementById("ip_words").value));
  await page.evaluate(() => document.getElementById("ipSave").click()); await sleep(1800);
  const saved = (await j("GET", "/api/cha/invoice-packing", null, token)).body.items;
  check("Save stores the record with the CIF total", saved.length === 1 && saved[0].invoice_no === "1333" && saved[0].total === "430.00", JSON.stringify(saved));
  // auto numbering, delete at the start of the row, masters, import
  check("each item row has its Delete button as the first cell", await page.evaluate(() => { const tr = document.querySelector("#ipLines tr"); return !!tr && !!tr.cells[0].querySelector('[data-ip^="rm:"]'); }));
  const pd = await page.evaluate(() => ({ ports: !!document.getElementById("ipPorts"), cc: document.getElementById("ipCountries").options.length, nav: !!document.querySelector('[data-go="port-master"]') && !!document.querySelector('[data-go="org-list"]') && !!document.querySelector('[data-go="country-master"]') }));
  check("the Port / Country master lists and the master links are on the form", pd.ports && pd.nav && pd.cc > 5, JSON.stringify(pd));
  const imp = await page.evaluate(() => { const rows = [["Invoice No", "psr/01/26 • 15-Feb-2026"], ["Exporter", "PSR EXPORTERS"], ["Consignee", "SYED"], ["Country of Origin", "INDIA"], ["Currency", "INR"], [], ["Sr.", "Description of Goods", "HS Code", "Quantity", "Unit", "Net Wt (kg)", "Gross Wt (kg)", "Unit Rate", "Amount"], ["01", "ONION", "[Enter]", "1650", "KGS", "1650", "1650", "17", "28050"], ["02", "[Add item description]", "[HS code]"], ["TOTAL", "", "", "", "", "", "", "", "28050"]]; const r = window.ChaInvPl.parseRows(rows); return { no: r.invoice_no, date: r.invoice_date, exp: r.data.exporter, lines: r.data.lines.length, desc: r.data.lines[0].description, rate: r.data.lines[0].rate }; });
  check("an Excel invoice is read: number, date, parties and item lines", imp.no === "psr/01/26" && imp.date === "2026-02-15" && imp.exp === "PSR EXPORTERS" && imp.lines === 1 && imp.desc === "ONION" && imp.rate === "17", JSON.stringify(imp));
  const tpl = await page.evaluate(async () => { await new Promise(r => { const t = document.createElement("script"); t.src = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"; t.onload = r; document.head.appendChild(t); }); const buf = window.ChaInvPl.templateXlsx(); const wb = window.XLSX.read(buf, { type: "array" }); const rows = window.XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, defval: "" }); const back = window.ChaInvPl.parseRows(rows.map(r => r.slice())); return { rows: rows.length, lines: back.data.lines.length }; });
  check("the Excel template downloads and reads back empty", tpl.rows > 30 && tpl.lines === 0, JSON.stringify(tpl));
  // print options
  await page.evaluate(() => document.getElementById("ipPrint").click()); await sleep(2500);
  const pages = async () => page.evaluate(() => document.querySelectorAll("#ipPreview .pg").length);
  const body = async () => (await text("#ipPreview")).replace(/\s+/g, " ");
  let bd = await body();
  check("Invoice + Packing List: two pages with both titles", (await pages()) === 2 && /COMMERCIAL INVOICE/.test(bd) && /PACKING LIST/.test(bd) && /GARMENTS/.test(bd) && /PSR EXPORTERS/.test(bd), bd.slice(0, 200));
  const pick = async v => { await page.evaluate(v => { const r = document.querySelector('input[name="ipKind"][value="' + v + '"]'); r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true })); }, v); await sleep(700); };
  await pick("packing"); bd = await body();
  check("Packing List alone: one page, no invoice title, weights shown", (await pages()) === 1 && !/COMMERCIAL INVOICE/.test(bd) && /Net Weight/.test(bd) && /5\.100/.test(bd), bd.slice(0, 160));
  await pick("invoice"); bd = await body();
  check("Invoice alone: one page with the invoice value and the declaration", (await pages()) === 1 && /INVOICE VALUE \(CIF\)/.test(bd) && /Declaration/.test(bd) && /430\.00/.test(bd) && !/Gross Weight \(KGS\)/.test(bd), bd.slice(0, 160));
  await pick("combined"); bd = await body();
  check("Invoice-cum-Packing List: one page with invoice and weight columns together", (await pages()) === 1 && /INVOICE CUM PACKING LIST/.test(bd) && /Gross Wt/i.test(bd) && /Unit Price/i.test(bd) && /430\.00/.test(bd), bd.slice(0, 160));
  const xl = await page.evaluate(async () => { const rec = window.ChaInvPl.S.rec; const buf = await window.ChaInvPl.makeXlsx(rec, "both", true); const wb = window.XLSX.read(buf, { type: "array" }); return { sheets: wb.SheetNames, inv: window.XLSX.utils.sheet_to_json(wb.Sheets["Invoice"], { header: 1 }).flat().join("|") }; });
  check("the Excel download holds an Invoice sheet and a Packing List sheet with the figures", xl.sheets.join(",") === "Invoice,Packing List" && /GARMENTS/.test(xl.inv) && /430/.test(xl.inv), JSON.stringify(xl).slice(0, 300));
  const pdf = await page.evaluate(async () => { const rec = window.ChaInvPl.S.rec; const bl = await window.ChaInvPl.makePdf(rec, "both", true); return { size: bl.size, type: bl.type }; });
  check("the PDF download is a real PDF of both pages", pdf.size > 8000 && /pdf/.test(pdf.type), JSON.stringify(pdf));
  check("Download PDF and Download Excel buttons are in the dialog", await page.evaluate(() => !!document.getElementById("ipDoPdf") && !!document.getElementById("ipDoXlsx")));
  await page.evaluate(() => { for (const id of ["ipSign", "ipSeal"]) { const c = document.getElementById(id); c.checked = false; c.dispatchEvent(new Event("change", { bubbles: true })); } }); await sleep(600);
  check("without signature the preview still draws (blank signature space)", (await pages()) === 1 && /Authorised Signatory/.test(await body()));
  const fmtPick = async v => { await page.evaluate(v => { const r = document.querySelector('input[name="ipFmt"][value="' + v + '"]'); r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true })); }, v); await sleep(800); };
  await fmtPick("export"); bd = await body();
  check("Export format: grades, cartons, FOB and the declaration box", /INVOICE CUM PACKING LIST/.test(bd) && /Grades/.test(bd) && /Cartons/.test(bd) && /FOB/.test(bd) && /Authorised Signatory/.test(bd) && /Rate \/ Kg/.test(bd), bd.slice(0, 200));
  const dlDir = require("path").join(require("os").tmpdir(), "ipdl" + Date.now()); fs.mkdirSync(dlDir, { recursive: true });
  const cdp = await page.createCDPSession(); await cdp.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: dlDir });
  for (const f of ["std", "export", "part"]) {
    await fmtPick(f); fs.readdirSync(dlDir).forEach(x => fs.unlinkSync(require("path").join(dlDir, x)));
    await click("#ipDoPdf"); let got = []; for (let i = 0; i < 40 && !got.some(x => /\.pdf$/.test(x)); i++) { await sleep(500); got = fs.readdirSync(dlDir); }
    check("clicking Download PDF saves a PDF file (" + f + ")", got.some(x => /\.pdf$/.test(x)), got.join(","));
    await click("#ipDoXlsx"); for (let i = 0; i < 30 && !fs.readdirSync(dlDir).some(x => /\.xlsx$/.test(x)); i++) await sleep(500);
    check("clicking Download Excel saves an Excel file (" + f + ")", fs.readdirSync(dlDir).some(x => /\.xlsx$/.test(x)), fs.readdirSync(dlDir).join(","));
  }
  await fmtPick("part"); bd = await body();
  check("Part-number format: part number, lot / serial, COO, Total Net", /COMMERCIAL INVOICE/.test(bd) && /Part Number/.test(bd) && /Lot\/Serial/.test(bd) && /Total Net/.test(bd), bd.slice(0, 200));
  check("the old Masters shortcut row is gone", await page.evaluate(() => !document.querySelector(".ip-links")));
  await page.evaluate(() => closeModal()); await sleep(400);
  // list, edit
  await page.evaluate(() => window.go("cha-invpl")); await sleep(1200);
  check("the saved record is in the list", /1333/.test(await text("#s-cha-invpl")) && /430\.00/.test(await text("#s-cha-invpl")));
  await page.evaluate(() => document.querySelector('[data-ip^="edit:"]').click()); await sleep(1200);
  check("Edit reopens the saved lines", (await page.evaluate(() => document.querySelector('[data-ipl="0:description"]').value)) === "GARMENTS");
  check("no script errors", errs.length === 0, errs.join(" | "));
  await browserClose();
  async function browserClose() { await page.browser().close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0); }
})();
