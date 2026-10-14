// Headless UI check #14: Settings -> Sequence Setting (list, builder, live preview, 16-character rule, suggest, activate, integration).
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
  const org = await j("POST", "/api/organizations", { code: "SQ" + tag.slice(-6).toUpperCase(), name: "Seq " + tag, admin_email: `sq${tag}@t.test`, admin_username: "sq" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "S" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "sq" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("POST", "/api/masters/branch", { code: "MAA", name: "Chennai", data: {} }, token);
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q = s => d.querySelector(s);
  const text = s => (q(s) || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  const toastsNow = () => Array.from(d.querySelectorAll("#toastStack .toast")).map(x => x.textContent.replace(/\s+/g, " ").trim());
  const clearToasts = () => d.querySelectorAll("#toastStack .toast").forEach(x => x.remove());
  const menuLabels = Array.from(d.querySelectorAll(".tn-label")).map(x => x.textContent.trim());

  check("Sequence Setting is listed under Settings", menuLabels.includes("Sequence Setting") && menuLabels.includes("Numbering"));
  w.go("sequence-setting"); await sleep(2000);
  const heads = Array.from(d.querySelectorAll("#seqRoot thead th")).map(t => t.textContent.trim());
  check("table has the required columns", ["Sequence Name", "Module", "Document Type", "Branch", "Financial Year", "Shipment Type", "Prefix", "Next Number", "Padding", "Suffix", "Total Length", "Status", "Last Updated"].every(x => heads.includes(x)), heads.join("|"));
  check("existing numbering shows as default sequences, active, with next numbers", /Sales Invoice/.test(text("#seqRoot")) && /default/.test(text("#seqRoot tbody")) && /INV\/\d\d-\d\d\/00001/.test(text("#seqRoot tbody")), text("#seqRoot tbody").slice(0, 200));
  setf(q('[data-sqf="q"]'), "Sales Invoice"); await sleep(1200);
  check("search filters the list", d.querySelectorAll("#seqRoot tbody tr").length === 1);
  setf(q('[data-sqf="q"]'), ""); await sleep(1000);

  // ---------- builder + live preview + 16 character rule
  q('[data-sq="new"]').click(); await sleep(1200);
  setf(q('[data-sq-f="name"]'), "Invoice too long"); setf(q('[data-sq-f="doc_type"]'), "invoice"); await sleep(800);
  check("preview is live and shows simulated numbers with their length", /Live preview/.test(text("#sqPrev")) && /Worst-case length/.test(text("#sqPrev")) && /Generated number/.test(text("#sqPrev")), text("#sqPrev"));
  q('[data-sq-tpl="A"]').click(); setf(q('[data-sq-f="number_width"]'), "6"); await sleep(1000);
  check("an invoice format over 16 characters is flagged as invalid with its length", /Cannot be activated/.test(text("#sqPrev")) && /exceeds the maximum permitted length of 16 characters/.test(text("#sqPrev")) && /Current length/.test(text("#sqPrev")), text("#sqPrev"));
  q('[data-sq-tpl="B"]').click(); await sleep(900);
  check("a shorter format becomes valid (Type / Year / No.)", /Valid — can be activated/.test(text("#sqPrev")), text("#sqPrev"));
  setf(q('[data-sq-f="name"]'), "Invoice short"); setf(q('[data-sq-f="start_number"]'), "100"); await sleep(600);
  q("#sqSave").click(); await sleep(1500);
  const list = (await j("GET", "/api/sequences", null, token)).body.items;
  const mine = list.find(c => c.name === "Invoice short");
  check("it is saved as a Draft (never active until authorised)", mine && mine.status === "DRAFT" && mine.start_number === 100, JSON.stringify(mine));
  check("the saved draft shows in the table", /Invoice short/.test(text("#seqRoot tbody")) && /Draft|DRAFT/.test(text("#seqRoot tbody")));

  // ---------- test + activate + integration
  q(`[data-sq="test:${mine.id}"]`).click(); await sleep(1000);
  check("Preview & Test shows samples without issuing numbers", /Diagnostic run: nothing is issued/.test(text("#modalBody")) && /INV\/\d\d-\d\d\/000100/.test(text("#modalBody")), text("#modalBody").slice(0, 200));
  q("#modalFoot button").click(); await sleep(300);
  q(`[data-sq="activate:${mine.id}"]`).click(); await sleep(1500);
  const after = (await j("GET", `/api/sequences/${mine.id}`, null, token)).body;
  check("activation replaces the default of the same scope", after.status === "ACTIVE" && (await j("GET", "/api/sequences", null, token)).body.items.find(c => c.doc_type === "invoice" && c.is_system_default).status === "INACTIVE");
  const doc = (await j("POST", "/api/documents", { doc_type: "invoice", party_name: "Seq Co", lines: [{ charge_name: "x", rate: "100" }] }, token)).body;
  const posted = (await j("POST", `/api/documents/${doc.id}/post`, null, token)).body;
  check("a real invoice now uses the configured sequence and stays within 16 characters", /^INV\/\d\d-\d\d\/000100$/.test(posted.doc_number) && posted.doc_number.length <= 16, posted.doc_number);

  // ---------- history + adjust + suggest
  w.go("sequence-setting"); await sleep(1500);
  q(`[data-sq="history:${mine.id}"]`).click(); await sleep(1200);
  check("history lists the activation and the issued number", /sequence activated/i.test(text("#modalBody")) && /INV\/\d\d-\d\d\/000100/.test(text("#modalBody")), text("#modalBody").slice(0, 200));
  q("#modalFoot button").click(); await sleep(300);
  q(`[data-sq="adjust:${mine.id}"]`).click(); await sleep(500);
  setf(q("#sqAdjN"), "50"); setf(q("#sqAdjR"), "going backwards"); clearToasts(); q("#sqAdjGo").click(); await sleep(1000);
  check("the next number cannot be moved backwards", toastsNow().some(t => /only move forward/i.test(t)), toastsNow().join(";"));
  setf(q("#sqAdjN"), "200"); setf(q("#sqAdjR"), "Align with paper register"); q("#sqAdjGo").click(); await sleep(1200);
  check("moving it forward is accepted and audited", (await j("GET", `/api/sequences/${mine.id}`, null, token)).body.next_number.endsWith("000200") && (await j("GET", `/api/sequences/${mine.id}/history`, null, token)).body.events.some(e => e.action === "NEXT_NUMBER_ADJUSTED"));
  q('[data-sq="suggest"]').click(); await sleep(600);
  setf(q("#sqSugDoc"), "payment"); q("#sqSugGo").click(); await sleep(1500);
  check("system suggestions are explained and previewed", /valid ·/.test(text("#sqSugOut")) && /Short and legally clean|Branch-wise/.test(text("#sqSugOut")) && !!q("[data-sq-use]"), text("#sqSugOut").slice(0, 200));
  const before = (await j("GET", "/api/sequences", null, token)).body.items.length;
  q("[data-sq-use]").click(); await sleep(1200);
  check("using a suggestion opens it for review (not saved or activated)", /New Sequence/.test(text("#modalTitle") || text("#modalOvl")) && (await j("GET", "/api/sequences", null, token)).body.items.length === before);

  // ---------- CRM numbers come from the service
  q("#modalFoot button").click(); await sleep(300);
  w.go("crm-lead"); await sleep(1500);
  q("#crmNewLeadBtn").click(); await sleep(300);
  setf(q("#crmLd_company"), "Acme"); q("#crmLd_save").click(); await sleep(2500);
  const lead = (await j("GET", "/api/crm/lead", null, token)).body[0];
  check("a CRM lead gets its number from the central service", lead && /^LEAD\/\d\d-\d\d\/00001$/.test(lead.ref), JSON.stringify(lead && lead.ref));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
