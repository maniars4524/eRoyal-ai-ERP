// Headless UI check: Airline / Sea Line / Airline Stock Line / IATA / CFS masters (header, KPI cards, filters + Reset, table, pagination, drawer, import with preview, link to organization).
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
  const org = await j("POST", "/api/organizations", { code: "NM" + tag.slice(-5).toUpperCase(), name: "Netmasters " + tag, admin_email: `nm${tag}@t.test`, admin_username: "nm" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Nm" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "nm" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s), qa = s => Array.from(d.querySelectorAll(s));
  await sleep(3000);
  const toasts = () => qa("#toastStack .toast").map(x => x.textContent.replace(/\s+/g, " ").trim());
  const clear = () => qa("#toastStack .toast").forEach(x => x.remove());
  const ev = (el, t) => el.dispatchEvent(new w.Event(t, { bubbles: true }));
  const scr = k => q("#s-net-" + k);
  const rows = k => qa(`#s-net-${k} [data-net-body] tr`).map(r => r.textContent.replace(/\s+/g, " ").trim());
  const setNf = (k, name, v, t) => { const el = scr(k).querySelector(`[data-nf="${name}"]`); el.value = v; ev(el, t || "input"); };
  const drawerSet = (id, v) => { const el = q("#nf_" + id); el.value = v; ev(el, "input"); };
  const open = async k => { w.go("net-" + k); await sleep(1500); };

  // ---- Airline Master
  await open("airlines");
  check("Airline Master opens with the header buttons Import Data, Export and Add", /Airline Master/.test(scr("airlines").textContent) && ["import", "export", "add"].every(a => scr("airlines").querySelector(`[data-net="${a}"]`)));
  check("four KPI cards with icons are shown", qa("#s-net-airlines .kpi-rich").length === 4 && qa("#s-net-airlines .kpi-rich svg").length === 4);
  check("nothing is pre-filled: the master starts empty", /No airline yet/.test(rows("airlines").join()), rows("airlines").join("|"));
  scr("airlines").querySelector('[data-net="import"]').click(); await sleep(400);
  check("Import Data offers the company airline list", !!q("#netImpStd") && !!q("#netImpFile"));
  q("#netImpStd").click(); await sleep(1500);
  check("the check shows 94 new, 3 skipped (placeholder + two repeats), no errors and the padded numbers", /94 new/.test(q("#netImpOut").textContent) && /3 skipped/.test(q("#netImpOut").textContent) && /0 with errors/.test(q("#netImpOut").textContent) && /padded to 3 digits/.test(q("#netImpOut").textContent), q("#netImpOut").textContent.slice(0, 200));
  check("the skipped rows say why", /Placeholder row/.test(q("#netImpOut").textContent) && /numeric prefix/.test(q("#netImpOut").textContent));
  check("checking saved nothing yet", (await j("GET", "/api/network/airlines", null, token)).body.total === 0);
  q("#netImpApply").click(); await sleep(2500);
  check("saving loads the airlines", (await j("GET", "/api/network/airlines", null, token)).body.total === 94 && /Saved/.test(q("#netImpOut").textContent));
  w.closeModal();
  check("the list shows 10 rows with pagination 'Showing 1 to 10 of 94'", rows("airlines").length === 10 && /Showing 1 to 10 of 94/.test(scr("airlines").querySelector("[data-net-pager]").textContent), scr("airlines").querySelector("[data-net-pager]").textContent);
  scr("airlines").querySelector('[data-pg="next"]').click(); await sleep(900);
  check("next page shows the next ten", /Showing 11 to 20 of 94/.test(scr("airlines").querySelector("[data-net-pager]").textContent));
  setNf("airlines", "q", "air india"); await sleep(1200);
  check("search narrows the list", rows("airlines").length >= 1 && rows("airlines").length < 10 && rows("airlines").every(r => /air india/i.test(r)), rows("airlines").join("|"));
  scr("airlines").querySelector('[data-net="reset"]').click(); await sleep(1000);
  check("Reset brings the full list back", /of 94/.test(scr("airlines").querySelector("[data-net-pager]").textContent) && scr("airlines").querySelector('[data-nf="q"]').value === "");
  check("unlinked airlines offer 'Not linked' (no organizations were auto-created)", /Not linked/.test(rows("airlines")[0]) && (await j("GET", "/api/customers", null, token)).body.length === 0);

  // ---- add through the drawer: validation, then save
  scr("airlines").querySelector('[data-net="add"]').click(); await sleep(800);
  check("Add opens the right-side drawer", q("#netDrawerOvl").classList.contains("on") && /Add Airline/.test(q("#netDrawerTitle").textContent));
  q("#netSave").click(); await sleep(200);
  check("required fields are flagged", /Airline Code is required/.test(q("#nf_code_e").textContent) && /Airline Name is required/.test(q("#nf_name_e").textContent));
  drawerSet("code", "zz"); drawerSet("name", "Zeta Cargo " + tag); drawerSet("num_code", "9x9"); drawerSet("email", "bad");
  q("#netSave").click(); await sleep(200);
  check("IATA number and email formats are checked", /1 to 3 digits/.test(q("#nf_num_code_e").textContent) && /not valid/.test(q("#nf_email_e").textContent));
  drawerSet("num_code", "9"); drawerSet("email", "ops@zeta.test");
  q("#netSave").click(); await sleep(1500);
  const zz = (await j("GET", "/api/network/airlines?q=Zeta", null, token)).body.items[0];
  check("saved airline: code upper-cased, number padded", !!zz && zz.code === "ZZ" && zz.num_code === "009", JSON.stringify(zz));
  const dup = await j("POST", "/api/network/airlines", { code: "ZZ", name: "Other" }, token);
  check("a duplicate code is refused", dup.status === 409);
  // ---- create the organization from the record
  setNf("airlines", "q", "Zeta Cargo"); await sleep(1000);
  const more = scr("airlines").querySelector('[data-net-act="more"]'); more.click(); await sleep(200);
  check("the more menu offers view, edit, link, create organization, deactivate, delete", ["View details", "Edit", "Link to organization", "Create organization", "Deactivate", "Delete"].every(t => q(".net-menu").textContent.includes(t)));
  Array.from(qa(".net-menu div")).find(x => /Create organization/.test(x.textContent)).click(); await sleep(1500);
  const cs = (await j("GET", "/api/customers", null, token)).body;
  check("the organization is created with the Carrier role and airline details", cs.length === 1 && cs[0].roles.includes("carrier") && cs[0].data.airline_code === "ZZ" && cs[0].data.carrier_airline === "yes", JSON.stringify(cs.map(c => [c.name, c.roles, c.data])));
  check("the row now shows the organization", /Zeta Cargo/.test(rows("airlines")[0]) && !/Not linked/.test(rows("airlines")[0]), rows("airlines")[0]);
  scr("airlines").querySelector('[data-net-act="more"]').click(); await sleep(200);
  Array.from(qa(".net-menu div")).find(x => /Deactivate/.test(x.textContent)).click(); await sleep(1200);
  check("deactivating changes the status", (await j("GET", "/api/network/airlines?q=Zeta", null, token)).body.items[0].status === "INACTIVE");

  // ---- Sea Line master
  await open("sea-lines");
  scr("sea-lines").querySelector('[data-net="add"]').click(); await sleep(700);
  drawerSet("code", "msc"); drawerSet("name", "MSC Mediterranean"); drawerSet("scac_code", "mscu");
  q("#netSave").click(); await sleep(1500);
  check("Sea Line saved with the SCAC code", /MSC Mediterranean/.test(rows("sea-lines").join()) && /MSCU/.test(rows("sea-lines").join()), rows("sea-lines").join("|"));
  scr("sea-lines").querySelector('[data-net="add"]').click(); await sleep(700);
  drawerSet("code", "MSC"); drawerSet("name", "Another"); clear();
  q("#netSave").click(); await sleep(1200);
  check("a duplicate sea line code is reported in the drawer flow", toasts().some(t => /already exists/.test(t)), toasts().join(";"));
  w.document.querySelector("#netCancel").click();

  const live = (await j("GET", "/api/network/airlines?status=ACTIVE&limit=1", null, token)).body.items[0];
  // ---- IATA and CFS (import from pasted rows)
  await open("cfs");
  scr("cfs").querySelector('[data-net="import"]').click(); await sleep(400);
  q("#netImpText").value = "CODE,NAME,PORT_CODE,CITY\nCFS1,Chennai Gateway CFS,inmaa,Chennai\nCFS1,Duplicate,INMAA,Chennai\nCFS2,Bad Port CFS,XX,Chennai";
  q("#netImpCheck").click(); await sleep(1200);
  check("pasted CFS rows: 1 new, 1 skipped as duplicate, 1 error (bad port code)", /1 new/.test(q("#netImpOut").textContent) && /1 skipped/.test(q("#netImpOut").textContent) && /1 with errors/.test(q("#netImpOut").textContent), q("#netImpOut").textContent.slice(0, 220));
  check("rows with errors cannot be saved", q("#netImpApply").disabled === true);
  q("#netImpText").value = "CODE,NAME,PORT_CODE,CITY\nCFS1,Chennai Gateway CFS,inmaa,Chennai";
  q("#netImpCheck").click(); await sleep(1000); q("#netImpApply").click(); await sleep(1500); w.closeModal();
  check("CFS saved with the port code upper-cased", /Chennai Gateway CFS/.test(rows("cfs").join()) && /INMAA/.test(rows("cfs").join()), rows("cfs").join("|"));
  await open("iata");
  scr("iata").querySelector('[data-net="add"]').click(); await sleep(700);
  drawerSet("iata_code", "ia-0001"); drawerSet("name", "Zeta Agents"); q("#nf_airline_id").value = String(live.id); ev(q("#nf_airline_id"), "change");
  q("#netSave").click(); await sleep(1500);
  check("IATA record saved against the airline", /Zeta Agents/.test(rows("iata").join()) && rows("iata").join().includes(live.name), rows("iata").join("|"));

  // ---- Stock lines
  await open("stock-lines");
  scr("stock-lines").querySelector('[data-net="add"]').click(); await sleep(700);
  q("#nf_airline_id").value = String(live.id); drawerSet("from_awb", "12345000"); drawerSet("to_awb", "12345099"); drawerSet("ref_no", "STK-1");
  q("#netSave").click(); await sleep(1500);
  check("stock line saved, 100 AWBs counted", /12345000/.test(rows("stock-lines").join()) && /100/.test(rows("stock-lines").join()), rows("stock-lines").join("|"));
  scr("stock-lines").querySelector('[data-net="add"]').click(); await sleep(700);
  q("#nf_airline_id").value = String(live.id); drawerSet("from_awb", "12345050"); drawerSet("to_awb", "12345200"); clear();
  q("#netSave").click(); await sleep(1200);
  check("an overlapping AWB range is refused", toasts().some(t => /overlaps/.test(t)), toasts().join(";"));
  drawerSet("from_awb", "12345300"); drawerSet("to_awb", "12345200");
  q("#netSave").click(); await sleep(300);
  check("From AWB above To AWB is flagged", /cannot be less/.test(q("#nf_to_awb_e").textContent));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
