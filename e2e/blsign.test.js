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
  check("company profile accepts a signature and a seal", (await j("PUT", "/api/company/doc-profile", { signatory: "Authorised Signatory", sign_image: PNG, seal_image: PNG }, token)).status === 200);
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
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
