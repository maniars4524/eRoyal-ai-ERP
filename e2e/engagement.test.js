// Engagement: Complete / Reschedule / Postpone / Cancel / Reopen with reasons and history, saved to the server, and the alerts in the notification centre
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
  const org = await j("POST", "/api/organizations", { code: "EG" + tag, name: "Eg " + tag, admin_email: `eg${tag}@t.test`, admin_username: "eg" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "eg" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const pad = n => String(n).padStart(2, "0"), at = (d, h, m) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(h)}:${pad(m)}`;
  const now = new Date(), y = new Date(now.getTime() - 2 * 86400000), n3 = new Date(now.getTime() + 3 * 86400000);
  const mk = async (ref, due, type) => (await j("PUT", "/api/crm/followup", { ref, company: "Co " + ref, status: "Open", data: { type, related: "ENQ:1", relatedLabel: "Enquiry - Co " + ref, due, notes: "agenda " + ref, done: false } }, token)).status;
  const R = { A: "FU-A" + tag, B: "FU-B" + tag, C: "FU-C" + tag, D: "FU-D" + tag, E: "FU-E" + tag };
  await mk(R.A, at(now, 11, 22), "Online Meeting"); await mk(R.B, at(now, 15, 5), "Phone Call"); await mk(R.C, at(y, 10, 0), "Customer Visit"); await mk(R.D, at(now, 16, 0), "Office Meeting"); await mk(R.E, at(now, 17, 0), "Phone Call");
  const get = async ref => ((await j("GET", "/api/crm/followup", null, token)).body || []).find(r => r.ref === ref);
  const alerts = async () => ((await j("GET", "/api/notifications", null, token)).body.items || []).filter(i => i.id.startsWith("Tasks:task:")).map(i => i.title);
  const a0 = await alerts();
  check("today's and the overdue follow-ups alert in the notification centre (with the time and a what-to-do)", a0.length === 5 && a0.some(t => /Due today 11:22/.test(t)) && a0.some(t => /^Overdue/.test(t)), a0.join(" | "));

  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = [];
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => d.accept());
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(4000);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const act = async (ref, kind) => { await click(`[data-crm-fu-act="${kind}"][data-fu-id="${ref}"]`); await sleep(700); };
  const setv = (sel, v) => page.evaluate((sel, v) => { const e = document.querySelector(sel); e.value = v; e.dispatchEvent(new Event("input", { bubbles: true })); }, sel, v);
  const go = async () => { await click("#crmFuGo"); await sleep(1200); };
  await page.evaluate(() => window.go("crm-engagement")); await sleep(3500);
  const kinds = await page.evaluate(r => [...document.querySelectorAll(`[data-fu-id="${r}"]`)].map(b => b.textContent.trim()).join(), R.A);
  check("an open engagement offers Complete, Reschedule, Postpone and Cancel", kinds === "Complete,Reschedule,Postpone,Cancel", kinds);
  await act(R.A, "complete"); await setv("#crmFuWhy", "met the customer, quote asked"); await go();
  // Reschedule A2 = B
  await act(R.B, "reschedule"); await setv("#crmFuNewDue", at(n3, 9, 30)); await go();
  check("reschedule needs a reason", /Required|Reason required/i.test(await text("#modalBody") + await text(".toast-stack")) || (await page.evaluate(() => !!document.querySelector("#crmFuGo"))), "");
  await setv("#crmFuWhy", "customer asked for Monday"); await go();
  await act(R.D, "postpone"); await click('[data-fu-shift="3"]'); await setv("#crmFuWhy", "customer travelling"); await go();
  await act(R.E, "cancel"); await go();
  check("cancel without a reason is refused (the dialog stays open)", await page.evaluate(() => !!document.querySelector("#crmFuGo")));
  await setv("#crmFuWhy", "customer cancelled the visit"); await go();
  await sleep(2500);                                                   // the screen saves its records by itself
  const A = await get(R.A), B = await get(R.B), D = await get(R.D), E = await get(R.E);
  check("completed is saved with its outcome", A.data.done === true && A.data.status === "Completed" && /quote asked/.test(A.data.outcome), JSON.stringify(A.data).slice(0, 200));
  check("rescheduled keeps the follow-up open on the new date with the reason and history", B.data.done !== true && B.data.status === "Rescheduled" && B.data.due === at(n3, 9, 30) && B.data.moves === 1 && /Monday/.test(B.data.outcome) && B.data.history.length === 1 && B.data.history[0].from.length > 0, JSON.stringify(B.data).slice(0, 260));
  check("postponed moves it three days later", D.data.status === "Postponed" && new Date(D.data.due).getTime() > Date.now() + 2.5 * 86400000, D.data.due);
  check("cancelled is saved with its reason", E.data.cancelled === true && E.data.status === "Cancelled" && /cancelled the visit/.test(E.data.outcome), JSON.stringify(E.data).slice(0, 200));
  const page1 = await text("#crmEngagementRoot");
  check("the table shows Completed, Rescheduled, Postponed and Cancelled", ["Completed", "Rescheduled", "Postponed", "Cancelled"].every(w => page1.includes(w)), page1.slice(-500));
  const a1 = await alerts();
  check("alerts follow the outcomes: completed, cancelled, rescheduled and postponed ones stop alerting; the overdue one remains", a1.length === 1 && /^Overdue/.test(a1[0]), a1.join(" | "));
  check("Today's follow-ups on the snapshot excludes finished ones", await page.evaluate(() => { const k = [...document.querySelectorAll("#crmEngagementRoot .dash-cards .kpi")].find(x => /TODAY/i.test(x.innerText)); return !!k && /\b0\b/.test(k.innerText); }));
  await act(R.E, "reopen"); await go(); await sleep(2500);
  const E2 = await get(R.E);
  check("reopen brings a cancelled engagement back (history kept)", E2.data.cancelled === false && E2.data.status === "Open" && E2.data.history.length === 2, JSON.stringify(E2.data).slice(0, 200));
  await page.evaluate(r => document.querySelector(`[data-crm-fu-history="${r}"]`).click(), R.B); await sleep(800);
  check("history shows what changed", /Rescheduled/.test(await text("#modalBody")) && /Monday/.test(await text("#modalBody")), await text("#modalBody"));
  await page.evaluate(() => window.closeModal()); await sleep(300);
  // the dashboard notification strip lists the alert
  await page.evaluate(() => { window.__fetchCacheClear && window.__fetchCacheClear(); window.go("dashboard"); window.__rtAlerts && window.__rtAlerts(); }); await sleep(3500);
  check("the dashboard notification bar shows the engagement alert", /Overdue|Due today/.test(await text("#rtDash")), await text("#rtDash"));
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
