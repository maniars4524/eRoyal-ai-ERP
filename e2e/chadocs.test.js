// Printed documents on a CHA job: Checklist (Shipping Bill / Bill of Entry), Annexure C, ICEGATE JSON buttons.
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
  const org = await j("POST", "/api/organizations", { code: "XT" + tag.slice(-6).toUpperCase(), name: "ExpTab " + tag, admin_email: `xt${tag}@t.test`, admin_username: "xt" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "xt" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);

  const R = () => d.getElementById("chaJobRoot");
  const open = async (type, mode) => {
    const no = (await j("POST", "/api/cha/jobs", { job_type: type, mode, exporter: "SWITCH INTERNATIONAL", importer: "ARMIN RUB", pol: "INMAA", pod: "HKHKG" }, token)).body.cha_job_number;
    w.go("cha-job", { jobNo: no }); await sleep(3000); return no;
  };
  for (const [type, mode] of [["Export", "Air"], ["Import", "Sea"]]) {
    await open(type, mode);
    R().querySelector('[data-chj-tab="Checklist"]').click(); await sleep(500);
    const b = R().querySelector("#chjCkl");
    check(type + ": the Checklist tab has Generate checklist", !!b);
    b.click(); await sleep(1800);
    const v = d.getElementById("chjDocView");
    check(type + ": the checklist opens as a printable page with Print and Word download", !!v && /CHECK ?LIST/i.test(v.textContent) && !!d.getElementById("chjDocPrint") && !!d.getElementById("chjDocWord"), v && v.textContent.slice(0, 120));
    if (type === "Export") {
      w.closeModal && w.closeModal(); await sleep(300);
      R().querySelector('[data-chj-tab="Checklist"]').click(); await sleep(300);
      const a = R().querySelector("#chjAnnex"); check("Export: Annexure C button present", !!a);
      a.click(); await sleep(1800);
      check("Export: Annexure C opens", /ANNEX/i.test((d.getElementById("chjDocView") || {}).textContent || ""));
    }
    w.closeModal && w.closeModal(); await sleep(300);
    check(type + ": toolbar JSON button is live", !!R().querySelector("#chjJson"));
  }
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
