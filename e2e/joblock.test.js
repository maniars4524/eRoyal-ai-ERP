// Job locking (one editor per job); the new-tab link is in newtab.test.js: two users on the same CHA job, conflict pop-up, read-only mode, access request and hand-over, direct API refusal, new-tab link.
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
  const org = await j("POST", "/api/organizations", { code: "JL" + tag.slice(-6).toUpperCase(), name: "JobLock " + tag, admin_email: `jl${tag}@t.test`, admin_username: "jl" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Anna" }, dev.body.access_token);
  const lic = org.body.organization.license_number;
  const tokenA = (await j("POST", "/api/auth/login", { identifier: "jl" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: lic })).body.access_token;
  await j("POST", "/api/users", { username: "jb" + tag, email: `jb${tag}@t.test`, password: "Ui-Test-Passw0rd-1", first_name: "Bala", role: "ERP_ADMIN" }, tokenA);
  const tokenB = (await j("POST", "/api/auth/login", { identifier: "jb" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: lic })).body.access_token;
  const no = (await j("POST", "/api/cha/jobs", { job_type: "Export", mode: "Sea", exporter: "SWITCH INTERNATIONAL", importer: "ARMIN RUB", pol: "INMAA", pod: "HKHKG" }, tokenA)).body.cha_job_number;
  const open = async (token, url) => JSDOM.fromURL(BASE + (url || "/erp"), { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { if (o) delete o.keepalive; for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.__jlHb = 700; w.__jlPoll = 600;
      w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "Customer waiting"; } });
  const A = await open(tokenA); await sleep(6000);
  A.window.go("cha-job", { jobNo: no }); await sleep(3500);
  const st = async tok => (await j("GET", "/api/job-locks/" + encodeURIComponent(no) + "/status", null, tok)).body;
  let s = await st(tokenA);
  check("opening the job takes the editing lock for the first user", s.locked && s.mine && /Anna/.test(s.holder.user_name), JSON.stringify(s));
  const B = await open(tokenB); await sleep(6000);
  B.window.go("cha-job", { jobNo: no }); await sleep(3500);
  const bd = B.window.document, ad = A.window.document;
  const modal = () => (bd.getElementById("modalBox") || bd.querySelector(".modal-box") || { textContent: "" }).textContent.replace(/\s+/g, " ");
  check("the second user sees Job Already Being Processed with the editor, job number, type and start time", /Job Already Being Processed/.test(modal()) && /Anna/.test(modal()) && modal().includes(no) && /CHA/.test(modal()) && /Started At/.test(modal()), modal().slice(0, 300));
  check("the pop-up offers Read-Only, Request Editing Access and Cancel", ["jlRO", "jlReq", "jlCancel"].every(id => !!bd.getElementById(id)));
  check("an authorised user is offered Kill / Force Unlock in the notification", /Kill \/ Force Unlock/.test(bd.getElementById("jlTake") ? bd.getElementById("jlTake").textContent : ""), bd.getElementById("jlTake") && bd.getElementById("jlTake").textContent);
  bd.getElementById("jlRO").click(); await sleep(900);
  check("Open in Read-Only Mode shows the banner and locks the form", !!bd.getElementById("jlBanner") && bd.body.classList.contains("job-readonly") && /Anna/.test(bd.getElementById("jlBanner").textContent));
  const saveB = bd.getElementById("chjSave");
  check("in read-only mode the Save button and fields are disabled but the tabs still work", !!saveB && saveB.disabled && !!bd.querySelector("[data-chj-tab]") && !bd.querySelector("[data-chj-tab]").disabled);
  const direct = await j("PATCH", "/api/cha/jobs/" + encodeURIComponent(no), { customs_location: "INMAA1" }, tokenB);
  check("a direct API write by the second user is refused by the server (423)", direct.status === 423 && direct.body.detail.code === "JOB_LOCKED", JSON.stringify(direct));
  check("the first user can still save", (await j("PATCH", "/api/cha/jobs/" + encodeURIComponent(no), { customs_location: "INMAA1" }, tokenA)).status === 200);
  // access request and hand-over
  bd.getElementById("jlReq2").click(); await sleep(2800);
  const am = (ad.getElementById("modalBox") || ad.querySelector(".modal-box") || { textContent: "" }).textContent.replace(/\s+/g, " ");
  check("the editor is told someone asks for access, with Hand over / Decline", /Editing access requested/.test(am) && /Bala/.test(am) && !!ad.querySelector('[data-jl-answer="1"]'), am.slice(0, 200));
  ad.querySelector('[data-jl-answer="1"]').click(); await sleep(4500);
  s = await st(tokenB);
  check("after the hand-over the second user is the editor", s.mine && /Bala/.test(s.holder.user_name), JSON.stringify(s));
  check("the second user's screen switches to editing", !bd.body.classList.contains("job-readonly") && !bd.getElementById("jlBanner"), bd.body.className);
  check("the first user's screen becomes read-only", ad.body.classList.contains("job-readonly") && !!ad.getElementById("jlBanner"), ad.body.className);
  check("the first user can no longer save through the API", (await j("PATCH", "/api/cha/jobs/" + encodeURIComponent(no), { customs_location: "INMAA1" }, tokenA)).status === 423);
  const ev = (await j("GET", "/api/job-locks/" + encodeURIComponent(no) + "/events", null, tokenA)).body.map(e => e.event);
  check("lock events are kept (acquired, requested, transferred)", ["ACQUIRED", "REQUESTED", "TRANSFERRED"].every(e => ev.includes(e)), ev.join());
  // leaving the job releases it
  B.window.go("dashboard"); await sleep(3500);
  { const sx = await st(tokenA); check("leaving the job screen releases the lock", sx.locked === false, JSON.stringify(sx) + " B.job=" + JSON.stringify(B.window.JobLock.state.job) + " ev=" + (await j("GET", "/api/job-locks/" + encodeURIComponent(no) + "/events", null, tokenA)).body.map(e => e.event).join()); }
  // ---- invoices: the same lock, and a save from an old copy is refused ----
  const inv = (await j("POST", "/api/documents", { doc_type: "invoice", party_name: "Lock Party", lines: [{ charge_name: "x", rate: "100", tax_pct: "18" }] }, tokenA)).body;
  A.window.BK.openDoc(inv.id); await sleep(4000);
  const ist = async tok => (await j("GET", "/api/job-locks/" + encodeURIComponent("INV:" + inv.id) + "/status", null, tok)).body;
  let is = await ist(tokenA);
  check("opening a draft invoice takes the editing lock for the first accounts user", is.locked && is.mine, JSON.stringify(is));
  B.window.BK.openDoc(inv.id); await sleep(4500);
  const bm = (bd.getElementById("modalBox") || bd.querySelector(".modal-box") || { textContent: "" }).textContent.replace(/\s+/g, " ");
  check("the second accounts user sees Invoice Already Being Processed with the editor and the invoice", /Invoice Already Being Processed/.test(bm) && /Anna/.test(bm) && /Draft invoice/.test(bm), bm.slice(0, 260));
  bd.getElementById("jlRO").click(); await sleep(900);
  check("the invoice opens read-only for the second user (Post / Save disabled)", bd.body.classList.contains("job-readonly") && !!bd.getElementById("jlBanner") && [...bd.querySelectorAll('[data-bk="post"],[data-bk="save"]')].every(b => b.disabled), [...bd.querySelectorAll('[data-bk]')].map(b => b.dataset.bk + ":" + b.disabled + ":" + b.textContent.trim().slice(0, 14)).join("|"));
  const blocked = await j("PATCH", "/api/documents/" + inv.id, { remarks: "x" }, tokenB);
  check("a direct invoice write by the second user is refused by the server (423)", blocked.status === 423 && blocked.body.detail.code === "JOB_LOCKED" && /invoice/.test(blocked.body.detail.message), JSON.stringify(blocked));
  check("posting the invoice by the second user is refused as well", (await j("POST", "/api/documents/" + inv.id + "/post", null, tokenB)).status === 423);
  // the editor's copy goes stale when the invoice is changed by another path: the save is refused, nothing is overwritten
  await j("PATCH", "/api/documents/" + inv.id, { remarks: "changed behind the screen" }, tokenA);
  const saveBtn = ad.querySelector('[data-bk="save"]'); saveBtn && saveBtn.click(); await sleep(2500);
  const stale = (ad.getElementById("modalBox") || ad.querySelector(".modal-box") || { textContent: "" }).textContent.replace(/\s+/g, " ");
  check("saving from an old copy shows Changed by someone else and keeps the newer data", /Changed by someone else/.test(stale) && (await j("GET", "/api/documents/" + inv.id, null, tokenA)).body.remarks === "changed behind the screen", stale.slice(0, 200));
  const rel = ad.getElementById("jlReload"); rel && rel.click(); await sleep(3500);
  is = await ist(tokenA);
  check("Reload latest brings the current invoice back and the editor keeps the lock", is.mine, JSON.stringify(is));
  A.window.go("dashboard"); await sleep(2500);
  check("leaving the invoice screen releases its lock", (await ist(tokenB)).locked === false);
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
