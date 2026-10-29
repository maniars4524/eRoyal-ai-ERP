// Five accounts / operations users of one company at the same time (five separate browser windows): the same job, the same invoice, and five different jobs.
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
  const org = await j("POST", "/api/organizations", { code: "FV" + tag.slice(-6).toUpperCase(), name: "Five " + tag, admin_email: `fv${tag}@t.test`, admin_username: "fv" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "User1" }, dev.body.access_token);
  const lic = org.body.organization.license_number;
  const login = async u => (await j("POST", "/api/auth/login", { identifier: u, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: lic })).body.access_token;
  const tokens = [await login("fv" + tag)];
  for (let i = 2; i <= 5; i++) { await j("POST", "/api/users", { username: `fv${i}${tag}`, email: `fv${i}${tag}@t.test`, password: "Ui-Test-Passw0rd-1", first_name: "User" + i, role: "ERP_ADMIN" }, tokens[0]); tokens.push(await login(`fv${i}${tag}`)); }
  const jobs = []; for (let i = 0; i < 5; i++) jobs.push((await j("POST", "/api/cha/jobs", { job_type: "Export", mode: "Sea", exporter: "EXP" + i, importer: "IMP" + i, pol: "INMAA", pod: "HKHKG" }, tokens[0])).body.cha_job_number);
  const inv = (await j("POST", "/api/documents", { doc_type: "invoice", party_name: "Five Party", lines: [{ charge_name: "x", rate: "100", tax_pct: "18" }] }, tokens[0])).body;
  const open = async token => JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { if (o) delete o.keepalive; for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.__jlHb = 800; w.__jlPoll = 600;
      w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "x"; } });
  const wins = await Promise.all(tokens.map(open)); await sleep(7000);
  const ro = w => w.window.document.body.classList.contains("job-readonly");
  // 1. all five open the same job together
  await Promise.all(wins.map(w => { w.window.go("cha-job", { jobNo: jobs[0] }); return sleep(10); })); await sleep(5000);
  const editors = wins.filter(w => !ro(w)).length;
  check("five users open the same job together: exactly one is the editor, four are read-only", editors === 1 && wins.filter(ro).length === 4, "editors=" + editors);
  const writes = await Promise.all(tokens.map(t => j("PATCH", "/api/cha/jobs/" + encodeURIComponent(jobs[0]), { customs_location: "INMAA1" }, t)));
  check("five simultaneous saves of that job: only the editor's goes through, the other four are refused", writes.filter(r => r.status === 200).length === 1 && writes.filter(r => r.status === 423).length === 4, writes.map(r => r.status).join());
  // 2. everyone leaves, then five different jobs at the same time
  await Promise.all(wins.map(w => { w.window.go("dashboard"); return sleep(10); })); await sleep(3500);
  await Promise.all(wins.map((w, i) => { w.window.go("cha-job", { jobNo: jobs[i] }); return sleep(10); })); await sleep(5000);
  check("five users on five different jobs: all five can edit", wins.every(w => !ro(w)), wins.map(ro).join());
  const own = await Promise.all(tokens.map((t, i) => j("PATCH", "/api/cha/jobs/" + encodeURIComponent(jobs[i]), { customs_location: "INMAA1" }, t)));
  check("and all five saves succeed together", own.every(r => r.status === 200), own.map(r => r.status).join());
  const cross = await j("PATCH", "/api/cha/jobs/" + encodeURIComponent(jobs[1]), { customs_location: "INMAA4" }, tokens[0]);
  check("nobody can write another user's job", cross.status === 423, cross.status);
  // 3. the same invoice
  await Promise.all(wins.map(w => { w.window.go("dashboard"); return sleep(10); })); await sleep(3500);
  await Promise.all(wins.map(w => { w.window.BK.openDoc(inv.id); return sleep(10); })); await sleep(6500);
  const ed2 = wins.filter(w => !ro(w)).length;
  check("five accounts users open the same invoice together: exactly one is the editor, four are read-only", ed2 === 1 && wins.filter(ro).length === 4, "editors=" + ed2);
  const iw = await Promise.all(tokens.map(t => j("PATCH", "/api/documents/" + inv.id, { remarks: "race" }, t)));
  check("five simultaneous invoice saves: one goes through, four are refused", iw.filter(r => r.status === 200).length === 1 && iw.filter(r => r.status === 423).length === 4, iw.map(r => r.status).join());
  const ev = (await j("GET", "/api/job-locks/" + encodeURIComponent("INV:" + inv.id) + "/events", null, tokens[0])).body.map(e => e.event);
  check("the invoice lock history is recorded", ev.includes("ACQUIRED"), ev.join());
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
