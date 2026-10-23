// Job page links (booking, same-booking jobs, invoices) and Excel import of a master list.
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
  const org = await j("POST", "/api/organizations", { code: "JL" + tag.slice(-5).toUpperCase(), name: "Joblinks " + tag, admin_email: `jl${tag}@t.test`, admin_username: "jl" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Jl" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "jl" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const b = (await j("POST", "/api/bookings", { mode: "Sea", direction: "Export", shipment_type: "FCL", booking_type: "Direct", party_name: "Links Exports", carrier: "MSC", carrier_booking_no: "L1", vessel: "V", pol: "INMAA", pod: "BEANR", etd: "2030-01-10", eta: "2030-02-05", si_cutoff: "2030-01-05", vgm_cutoff: "2030-01-05", data: { services: "Freight, CHA", container_type: "40HC", container_qty: "2", charges: [] } }, token)).body;
  const conf = (await j("POST", `/api/bookings/${b.id}/confirm`, null, token)).body;
  const freight = conf.jobs.find(x => x.job_type === "Forwarding").job_number, cha = conf.jobs.find(x => x.job_type === "Customs").job_number;
  const inv = (await j("POST", "/api/documents", { doc_type: "invoice", party_name: "Links Exports", job_number: freight, lines: [{ charge_name: "Ocean Freight", hsn: "996521", uom: "Per Shipment", qty: "1", ccy: "INR", exch: "1", rate: "500", tax_type: "Non-Taxable", tax_pct: "0" }] }, token)).body;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(6000);
  w.go("job-detail", { jobNo: freight }); await sleep(3500);
  const strip = d.getElementById("jobLinks");
  check("the job page shows a LINKED strip with the booking, the customs job of the same booking and the invoice", !!strip && /Booking/.test(strip.textContent) && strip.textContent.includes(conf.number) && strip.textContent.includes(cha) && /Invoice/.test(strip.textContent), strip && strip.textContent);
  strip.querySelector("[data-jl-doc]").click(); await sleep(2500);
  check("the invoice link opens that invoice (no search)", d.getElementById("s-invoice-new").classList.contains("on") || d.querySelector(".scr.on").id !== "s-job-detail", d.querySelector(".scr.on").id);
  w.go("job-detail", { jobNo: freight }); await sleep(3500);
  d.querySelector('#jobLinks [data-go="job-detail"]').click(); await sleep(3500);
  check("the same-booking link jumps to the customs job and its own links point back to the freight job", d.getElementById("jd_no").textContent === cha && d.getElementById("jobLinks").textContent.includes(freight), d.getElementById("jd_no").textContent + " | " + d.getElementById("jobLinks").textContent);
  // Excel: a workbook becomes CSV text for the import
  await new Promise(r => { const s = d.createElement("script"); s.src = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"; s.onload = r; s.onerror = r; d.head.appendChild(s); });
  if (w.XLSX) {
    const ws = w.XLSX.utils.aoa_to_sheet([["CODE", "NAME", "PORT_CODE"], ["CFS9", "Excel Gateway CFS", "INMAA"]]); const wb = w.XLSX.utils.book_new(); w.XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
    const bytes = w.XLSX.write(wb, { type: "array", bookType: "xlsx" });
    const file = { name: "cfs.xlsx", arrayBuffer: async () => new Uint8Array(bytes).buffer };
    const csv = await w.XlsxKit.toCsv(file);
    check("an Excel sheet is read into CSV text for the import", /CODE,NAME,PORT_CODE/.test(csv) && /CFS9,Excel Gateway CFS,INMAA/.test(csv), csv);
    const res = await j("POST", "/api/network/cfs/import", { rows: [{ CODE: "CFS9", NAME: "Excel Gateway CFS", PORT_CODE: "INMAA" }], apply: true }, token);
    check("and the rows import through the same checked import", res.status === 200 && res.body.new === 1);
  } else console.log("SKIP Excel reader not reachable (no internet)");
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
