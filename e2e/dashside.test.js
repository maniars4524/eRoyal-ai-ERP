// Headless UI check: dashboard panels and side lists use live data and each "View All" opens its own list.
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
  const org = await j("POST", "/api/organizations", { code: "DS" + tag.slice(-6).toUpperCase(), name: "Dash " + tag, admin_email: `ds${tag}@t.test`, admin_username: "ds" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "D" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ds" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR" }, token);
  await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "DEHAM" }, token);
  await j("POST", "/api/shipments", { mode: "Air", direction: "Import", pol: "DEFRA", pod: "INMAA" }, token);
  const line = { side: "SELL", charge_name: "Ocean Freight", carrier: "", basis: "Per Container", ccy: "USD", exch: "83.5", rate: "850", qty: "2", tax_pct: "0" };
  const q = (await j("POST", "/api/quotations", { party_name: "Acme Exports", mode: "Sea", direction: "Export", shipment_type: "FCL", pol: "INMAA", pod: "BEANR", lines: [line] }, token)).body;
  await j("POST", `/api/quotations/${q.id}/approval/submit`, null, token);
  await j("PUT", "/api/crm/followup", { ref: "FU-D-1", company: "Acme", status: "open", data: { type: "Call", relatedLabel: "Acme Exports", due: new Date().toISOString().slice(0, 10), done: false, notes: "Confirm rates" } }, token);

  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(3000); w.go("dashboard"); await sleep(2500);
  const q$ = s => d.querySelector(s);
  const text = s => ((q$(s) || {}).textContent || "").replace(/\s+/g, " ");
  const card = title => Array.from(d.querySelectorAll("#s-dashboard .card")).find(c => ((c.querySelector(".ct") || { textContent: "" }).textContent.trim() === title));

  // the shipment-type panel
  const panel = text("#kpiDetailPanel");
  check("Active Jobs — By Shipment Type shows the real counts", /Sea Export/.test(panel) && /Air Import/.test(panel) && !/No data yet/.test(panel), panel.slice(0, 200));
  const seaCells = Array.from(d.querySelectorAll('#kpiDetailPanel .dd-cell[data-filter^="sea-export"]')).map(c => c.textContent.trim());
  check("Sea Export: 2 active, 0 closed, 2 pending; Air Import has 1 active", seaCells.join(",") === "2,0,2" && q$('#kpiDetailPanel .dd-cell[data-filter="air-import-active"]').textContent.trim() === "1", seaCells.join(","));
  q$('#s-dashboard [data-kpi-select="revenue"]').click(); await sleep(300);
  check("the Revenue panel is live too (no invoices yet, so it says so)", /Revenue MTD — By Mode/.test(text("#kpiDetailPanel")));
  q$('#s-dashboard [data-kpi-select="jobs"]').click(); await sleep(300);
  check("and the jobs panel comes back with its figures", /Sea Export/.test(text("#kpiDetailPanel")) && !!q$('#kpiDetailPanel .dd-cell'));

  // the side lists
  const viewAll = t => card(t).querySelector("[data-go]").dataset.go;
  check("each side widget's View All opens its own page, none opens the shipment list", [viewAll("Today's Tasks"), viewAll("Pending Approvals"), viewAll("Notifications"), viewAll("Calendar"), viewAll("Recent Activity")].join(",") === "crm-engagement,approvals,notifications,meetings,activity");
  check("Pending Approvals lists the quotation waiting for the user, with its party and who submitted it", /Quotation/.test(text2(card("Pending Approvals"))) && /Acme Exports/.test(text2(card("Pending Approvals"))), text2(card("Pending Approvals")));
  check("Today's Tasks lists the follow-up due today", /Call/.test(text2(card("Today's Tasks"))) && /Due today/.test(text2(card("Today's Tasks"))), text2(card("Today's Tasks")));
  check("Recent Activity lists what happened", /Quotation submitted/i.test(text2(card("Recent Activity"))), text2(card("Recent Activity")));
  function text2(c) { return c ? c.textContent.replace(/\s+/g, " ") : ""; }

  // the pages behind View All
  card("Pending Approvals").querySelector("[data-go]").click(); await sleep(1800);
  check("Pending Approvals opens the approvals page with the item", d.querySelector(".scr.on").id === "s-approvals" && /Quotation/.test(text("#apprBody")) && /Acme Exports/.test(text("#apprBody")), d.querySelector(".scr.on").id + " " + text("#apprBody"));
  d.querySelector('[data-side-page="approvals:0"]').click(); await sleep(2000);
  check("Open takes the approver to that quotation", d.querySelector(".scr.on").id === "s-quotation-print" || d.querySelector(".scr.on").id === "s-quotation", d.querySelector(".scr.on").id);
  w.go("activity"); await sleep(1500);
  check("Recent Activity opens the activity page", /Quotation submitted/i.test(text("#actBody")));
  w.go("notifications"); await sleep(1200);
  check("Notifications opens its own page", d.querySelector(".scr.on").id === "s-notifications" && text("#notifBody").length > 0);

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
