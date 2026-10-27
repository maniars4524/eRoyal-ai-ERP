// A released House BL printed with "digital signature and seal" carries the company's signature and seal images from the company documents profile.
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
const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==";
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "BS" + tag.slice(-6).toUpperCase(), name: "Blsign " + tag, admin_email: `bs${tag}@t.test`, admin_username: "bs" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Bo" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "bs" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  check("company profile accepts a signature, a seal and the standard BL remark", (await j("PUT", "/api/company/doc-profile", { signatory: "Authorised Signatory", sign_image: PNG, seal_image: PNG, bl_note: "FOR ALL DOOR, HOUSE DELIVERIES THE STANDARD UNLOAD FREE TIME AS PREVALENT WILL APPLY." }, token)).status === 200);
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR" }, token)).body.job_number;
  await j("POST", `/api/jobs/${job}/hbl`, { hbl_type: "MTO" }, token);
  await j("POST", `/api/jobs/${job}/bl/stage`, { stage: "Confirmed" }, token);
  const rel = await j("POST", `/api/jobs/${job}/bl/stage`, { stage: "Released", release_type: "Express Release", digital_sign: true }, token);
  check("the BL is released with digital sign", rel.status === 200 && rel.body.bl.digitalSign === true, JSON.stringify(rel.body).slice(0, 200));
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window;
  await sleep(6000);
  const rd = await w.BLDocKit.data(job);
  const html = w.BLDocKit.html(rd);
  const n = (html.match(/<img src="data:image\/png/g) || []).length;
  check("the printed House BL shows the digital signature and the seal", n >= 2, "images found: " + n);
  check("the foot follows the sample: delivery agent, freight amount / payable at, originals, place and date, signature, clauses; the standard remark is printed", /Delivery Agent/.test(html) && /Freight Amount/.test(html) && /Freight Payable at/.test(html) && /Number of Original/.test(html) && /Place and date of issue/.test(html) && /Authorised Signatory/.test(html) && /STANDARD UNLOAD FREE TIME/.test(html) && /EXPRESS BL RELEASED/.test(html));
  const sheets = w.BLDocKit.sheets(rd);
  check("the BL is two A4 sheets (the BL face and the terms and conditions)", (sheets.match(/class="bl-sheet"/g) || []).length === 2 && /TERMS AND CONDITIONS OF THE MULTIMODAL TRANSPORT DOCUMENT/.test(sheets) && /EXPRESS BL RELEASED/.test(sheets), "");
  if (process.env.BL_PREVIEW) fs.writeFileSync(process.env.BL_PREVIEW, "<html><head><style>@page{size:A4;margin:0}html,body{margin:0;background:#888}.bl-sheet{margin:0 auto 12px!important}</style></head><body>" + sheets + "<script>var fitSheet=" + w.BLDocKit.fitSheet.toString() + ";window.onload=function(){document.querySelectorAll('.bl-sheet').forEach(function(s){fitSheet(s,734,1063);});};<\/script></body></html>");
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
