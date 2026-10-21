// Headless UI check: the Tracking pop-up can be maximized; "Open tracking page" opens a separate maximized page that names the job and the screen it was opened from (it no longer just switches the job screen's tab).
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
  const org = await j("POST", "/api/organizations", { code: "TK" + tag.slice(-5).toUpperCase(), name: "Track " + tag, admin_email: `tk${tag}@t.test`, admin_username: "tk" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "T" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "tk" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "SGSIN" }, token)).body;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s), qa = s => Array.from(d.querySelectorAll(s));
  await sleep(3500); w.go("job-detail", { jobNo: job.job_number }); await sleep(4000);
  const box = () => q("#modalBox"), open = () => q("#modalOvl").classList.contains("on");
  q('[data-jl="tracking"]').click(); await sleep(700);
  check("one click opens the tracking pop-up with the milestones", open() && /Tracking — /.test(q("#modalTitle").textContent) && /Operational Milestones/.test(q("#modalBody").textContent), q("#modalTitle").textContent);
  check("the pop-up offers a Maximize button and is not maximized yet", q("#modalMaxBtn").style.display !== "none" && !box().classList.contains("max"));
  q("#modalMaxBtn").click();
  check("Maximize expands the pop-up", box().classList.contains("max") && q("#modalMaxBtn").title === "Restore size");
  q("#modalMaxBtn").click();
  check("and Restore brings it back", !box().classList.contains("max"));
  q("#jlTrackPage").click(); await sleep(1500);
  check("Open tracking page opens a separate maximized page, not the job's tab", open() && box().classList.contains("max") && /Tracking page — /.test(q("#modalTitle").textContent) && !q("#s-job-detail #jt6").classList.contains("on"), q("#modalTitle").textContent);
  check("it says which job it belongs to and where it was opened from", /opened from the Job screen/.test(q("#modalTitle").textContent) && /tracking page of job/.test(q("#modalBody").textContent) && q("#modalBody").textContent.includes(job.job_number));
  check("it shows the milestones and the latest tracking activity", /Operational Milestones/.test(q("#modalBody").textContent) && /Latest tracking activity/.test(q("#modalBody").textContent), q("#modalBody").textContent.slice(0, 200));
  q("#jlTrackBack").click(); await sleep(300);
  check("Back to job closes the page and leaves the job screen as it was", !open() && q("#s-job-detail").classList.contains("on"));
  q('[data-jl="tracking"]').dispatchEvent(new w.MouseEvent("dblclick", { bubbles: true })); await sleep(1500);
  check("double-clicking Tracking opens the same full page", open() && box().classList.contains("max") && /Tracking page — /.test(q("#modalTitle").textContent));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
