// Back button / browser history: Back returns to the page the user was really on (with its record, tab and scroll), not a fixed parent; pop-ups close first; unsaved work is protected; repeated clicks are ignored.
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
  const org = await j("POST", "/api/organizations", { code: "NB" + tag.slice(-6).toUpperCase(), name: "Navback " + tag, admin_email: `nb${tag}@t.test`, admin_username: "nb" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Nia" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "nb" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const mk = async () => (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "USLAX" }, token)).body.job_number;
  const jobA = await mk(), jobB = await mk();
  let confirmAnswer = true, confirms = 0;
  const open = async (url) => {
    const dom = await JSDOM.fromURL(url || BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
      beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => { confirms++; return confirmAnswer; }; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
    await sleep(7000); return dom.window;
  };
  let w = await open(); let d = w.document;
  const on = () => (d.querySelector(".scr.on") || {}).id;
  const backBtn = () => d.querySelector(".scr.on [title='Go back'], .scr.on [data-go='back']") || Array.from(d.querySelectorAll(".scr.on .pa button")).find(x => /^\W*back\W*$/i.test(x.textContent.trim()));
  const clickBack = async () => { const b = backBtn(); if (!b) return null; b.click(); await sleep(700); return on(); };
  // Dashboard -> Job list -> Job A -> BL tab -> HBL document
  w.go("job-list"); await sleep(700); w.go("job-detail", { jobNo: jobA }); await sleep(2500);
  d.querySelector('[data-tab="jt3"]').click(); await sleep(500);
  w.go("bl-draft"); await sleep(1200);
  check("the forward walk is recorded", w.NAV.stack.map(e => e.screen).join(">") === "dashboard>job-list>job-detail>bl-draft" || w.NAV.stack.map(e => e.screen).join(">").endsWith("job-list>job-detail>bl-draft"), w.NAV.stack.map(e => e.screen).join(">"));
  check("Back from the HBL document returns to the job (not the job list or dashboard), on the BL / AWB tab", (await clickBack()) === "s-job-detail" && d.querySelector('[data-tab="jt3"]').classList.contains("on"), on());
  check("the same job is shown again", (d.getElementById("jobDetailNo") || {}).textContent.trim() === jobA, (d.getElementById("jobDetailNo") || {}).textContent);
  check("Back from the job returns to the job list", (await clickBack()) === "s-job-list", on());
  check("Back from the job list returns to the dashboard", (await clickBack()) === "s-dashboard", on());
  check("Back with no earlier page uses the safe parent page and does not break", (await clickBack()) === null || on() === "s-dashboard", on());
  // two different jobs: Back returns to the previous record
  w.go("job-list"); await sleep(500); w.go("job-detail", { jobNo: jobA }); await sleep(1800); w.go("job-detail", { jobNo: jobB }); await sleep(1800);
  await clickBack(); await sleep(1200);
  check("Back between two jobs returns to the first job", on() === "s-job-detail" && (d.getElementById("jobDetailNo") || {}).textContent.trim() === jobA, (d.getElementById("jobDetailNo") || {}).textContent);
  // browser Back / Forward walk the same history
  w.history.back(); await sleep(800);
  check("browser Back goes to the job list", on() === "s-job-list", on());
  w.history.forward(); await sleep(1500);
  check("browser Forward comes back to the job", on() === "s-job-detail", on());
  // repeated clicks
  const idx0 = w.NAV.idx; const b = backBtn(); b.click(); b.click(); b.click(); await sleep(900);
  check("repeated Back clicks move one page only", w.NAV.idx === idx0 - 1, idx0 + " -> " + w.NAV.idx);
  // pop-up: Back closes the pop-up first
  w.go("job-detail", { jobNo: jobB }); await sleep(1500); const before = w.NAV.idx;
  w.openModal({ title: "Test", bodyHTML: "x", footHTML: "" }); await sleep(200);
  backBtn().click(); await sleep(500);
  check("Back with a pop-up open closes the pop-up and stays on the page", !d.getElementById("modalOvl").classList.contains("on") && on() === "s-job-detail" && w.NAV.idx === before, on());
  // unsaved quotation work is protected
  w.go("quotation-new"); await sleep(1500); w.QK.dirty = true; confirmAnswer = false; confirms = 0;
  if (d.getElementById("modalOvl").classList.contains("on")) { w.closeModal(); await sleep(200); }
  backBtn() && backBtn().click(); await sleep(600);
  check("Back with unsaved changes asks first and stays when declined", confirms >= 1 && on() === "s-quotation-new", on() + " confirms=" + confirms);
  confirmAnswer = true; backBtn().click(); await sleep(900);
  check("and leaves when confirmed", on() !== "s-quotation-new", on());
  // refresh keeps the history
  const stackBefore = w.NAV.stack.map(e => e.screen).join(">"), keep = w.sessionStorage.getItem("esway_nav");
  check("the history is kept for a page refresh (per tab)", !!keep && JSON.parse(keep).stack.length >= 3);
  w.close();
  // a direct visit with no history: Back falls back to the parent
  w = await open(BASE + "/erp#job-list"); d = w.document;
  check("opening a page by its address works and Back from it falls back to the dashboard", on() === "s-job-list" && ((await clickBack()) === "s-dashboard"), on());
  w.close();
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
