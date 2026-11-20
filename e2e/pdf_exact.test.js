// The downloaded PDF is the exact preview (printed by the server's browser): real text, nothing clipped or shifted, 2 pages for a House Air Waybill with its conditions
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
  const org = await j("POST", "/api/organizations", { code: "PX" + tag, name: "Px " + tag, admin_email: `px${tag}@t.test`, admin_username: "px" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "px" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("PUT", "/api/company/doc-profile", { legal_name: "TRIO SHIPPING SERVICES PVT LTD", address: "CHENNAI" }, token);
  const job = (await j("POST", "/api/shipments", { mode: "Air", direction: "Export", pol: "INMAA", pod: "SGSIN" }, token)).body.job_number;
  const put = async (sec, data) => (await j("PUT", `/api/jobs/${encodeURIComponent(job)}/details/${sec}`, { data }, token)).status;
  await put("parties", { shipper: "SUN PHARMACEUTICAL INDUSTRIES LTD.\nACME PLAZA, ANDHERI-KURLA ROAD,ANDHERI EAST MUMBAI - 400059", consignee: "PLANET PHARMA\nLE GRAND QUEVILLY ROUEN\nFrance" });
  await put("bl", { bookingType: "House", awb: { airline: "UL", routingTo: "SIN", shipperAccount: "0000000", consigneeAccount: "0000000", airCharges: [{ desc: "AIR WAYBILL CHARGES", code: "AWB", ac: "A", pre: "1000", col: "" }, { desc: "AIRPORT HANDLING CHARGES", code: "AHS", ac: "A", pre: "500", col: "" }] } });
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"], protocolTimeout: 300000 });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1300 }); const errs = [];
  page.on("pageerror", e => errs.push(String(e.message)));
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(3000);
  const st = await page.evaluate(async () => (await (await fetch("/api/pdf/status", { headers: { Authorization: "Bearer " + sessionStorage.getItem("esway_access_token") } })).json()));
  check("the server can print PDFs (browser found)", st.available === true, JSON.stringify(st));
  const r = await page.evaluate(async jn => {
    const bd = await window.DocKit.build("awb", { job: jn, fmt: "three", mode: "a4" });
    const t0 = performance.now(); const res = await window.DocKit.pdf(bd);
    const uri = res.pdf.output("datauristring");
    return { ms: Math.round(performance.now() - t0), exact: !!res.pdf.exact, uri };
  }, job);
  check("DocKit.pdf used the exact (server) route", r.exact, JSON.stringify({ ms: r.ms, exact: r.exact }));
  const buf = Buffer.from(r.uri.split(",")[1], "base64");
  check("it is a real PDF of a reasonable size", buf.slice(0, 4).toString() === "%PDF" && buf.length > 5000, String(buf.length));
  fs.writeFileSync("../scratch/exact_hawb.pdf", buf);
  // read the text of the PDF with pdf.js (from this server) and check nothing is clipped
  const txt = await page.evaluate(async b64 => {
    await new Promise((ok, bad) => { const s = document.createElement("script"); s.src = "/vendor/pdf.min.js"; s.onload = ok; s.onerror = bad; document.head.appendChild(s); });
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdf.worker.min.js";
    const pdf = await window.pdfjsLib.getDocument({ data: Uint8Array.from(atob(b64), c => c.charCodeAt(0)) }).promise; const out = { pages: pdf.numPages, text: [] };
    for (let i = 1; i <= pdf.numPages; i++) { const p = await pdf.getPage(i); const c = await p.getTextContent(); out.text.push(c.items.map(x => x.str).join(" ").replace(/\s+/g, " ")); }
    return out;
  }, r.uri.split(",")[1]);
  check("2 pages: the waybill and the conditions of contract", txt.pages === 2, String(txt.pages));
  const p1 = txt.text[0] || "";
  check("page 1 has the full labels", /Total other Charges Due Agent/i.test(p1) && /Optional Shipping Information/i.test(p1) && /Routing and Destination/i.test(p1) && /Requested Flight\/Date/i.test(p1), p1.slice(0, 600));
  check("the other charges text and the party names are printed", /AIR WAYBILL CHARGES : 1000\/-/.test(p1) && /SUN PHARMACEUTICAL/i.test(p1) && /PLANET PHARMA/i.test(p1), p1.slice(0, 400));
  check("page 2 is the conditions of contract", /CONDITIONS OF CONTRACT/.test(txt.text[1] || ""));
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
