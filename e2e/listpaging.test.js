// Lists: Rows per page (5, 15, 25, 50, 100, All) with Previous / Next, on Jobs and on the CRM / booking / purchase invoice lists.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (n, ok, x) => { console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + (x || ""))); if (!ok) failures++; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "LP" + tag, name: "Paging " + tag, admin_email: `lp${tag}@t.test`, admin_username: "lp" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "L" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "lp" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  for (let i = 0; i < 12; i++) await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR", consignee: "Pg " + i }, token);
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.console.warn = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s), qa = s => Array.from(d.querySelectorAll(s));
  await sleep(3500);
  w.go("job-list"); await sleep(3500);
  const rowsShown = () => qa("#s-job-list tbody tr").filter(r => r.children.length > 4 && r.style.display !== "none").length;
  const sel = q('#s-job-list .lk-bar [data-lk="size"]');
  check("the job list has a Rows selector with 5, 15, 25, 50, 100 and All (100+)", !!sel && Array.from(sel.options).map(o => o.textContent).join("|") === "5|15|25|50|100|All (100+)", sel && Array.from(sel.options).map(o => o.textContent).join("|"));
  check("by default every one of the 12 jobs is shown, with an honest count", rowsShown() === 12 && /Showing 1 to 12 of 12/.test(q("#s-job-list .lk-bar [data-lk-count]").textContent), rowsShown() + " | " + q("#s-job-list .lk-bar [data-lk-count]").textContent);
  const setSize = async v => { sel.value = v; sel.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(500); };
  await setSize("5");
  check("Rows = 5 shows 5 jobs and says Page 1 of 3", rowsShown() === 5 && /Page 1 of 3/.test(q("#s-job-list .lk-foot").textContent) && /Showing 1 to 5 of 12/.test(q("#s-job-list .lk-foot").textContent), rowsShown() + " | " + q("#s-job-list .lk-foot").textContent);
  const firstNo = () => (qa("#s-job-list tbody tr").find(r => r.children.length > 4 && r.style.display !== "none").textContent.match(/[A-Z]{2,}[A-Z0-9-]*\d{3,}/) || [""])[0];
  const first = firstNo();
  q('#s-job-list [data-lk-next]').click(); await sleep(400);
  check("Next shows the following 5 jobs (Page 2 of 3)", rowsShown() === 5 && firstNo() !== first && /Page 2 of 3/.test(q("#s-job-list .lk-foot").textContent), firstNo() + " vs " + first);
  q('#s-job-list [data-lk-next]').click(); await sleep(400);
  check("the last page shows the 2 that are left and Next is disabled", rowsShown() === 2 && q('#s-job-list [data-lk-next]').disabled && /Showing 11 to 12 of 12/.test(q("#s-job-list .lk-foot").textContent), rowsShown() + " | " + q("#s-job-list .lk-foot").textContent);
  q('#s-job-list [data-lk-prev]').click(); await sleep(400);
  check("Previous goes back a page", /Page 2 of 3/.test(q("#s-job-list .lk-foot").textContent));
  await setSize("15");
  check("Rows = 15 shows all 12 on one page with no pager buttons", rowsShown() === 12 && q('#s-job-list [data-lk-next]').style.visibility === "hidden");
  await setSize("0");
  check("All (100+) shows every row", rowsShown() === 12);
  await setSize("5");
  const search = q('#s-job-list .lk-bar [data-lk="q"]'); search.value = "Pg 3"; search.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(500);
  check("a filter goes back to page 1 and the count says how many were filtered out", rowsShown() === 1 && /1 of 1 \(filtered from 12\)|Showing 1 to 1 of 1/.test(q("#s-job-list .lk-foot").textContent) && /filtered from 12/.test(q("#s-job-list .lk-foot").textContent), q("#s-job-list .lk-foot").textContent);
  check("the chosen page size is remembered for this user", Object.keys(w.localStorage).some(k => /_job_size$/.test(k) && w.localStorage.getItem(k) === "5"));
  for (const [screen, label] of [["crm-lead", "Leads"], ["crm-enquiry", "Enquiries"], ["crm-opportunity", "Opportunities"], ["booking", "Bookings"], ["purchase-invoice-list", "Purchase invoices"], ["invoice-list", "Invoices"], ["quotation", "Quotations"]]) {
    w.go(screen); await sleep(2800);
    const scr = qa(".scr.on")[0];
    check(label + " list has the Rows selector", !!(scr && scr.querySelector('.lk-bar [data-lk="size"]')), scr && scr.id);
  }
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
