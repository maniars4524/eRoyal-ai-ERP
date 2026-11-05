// Real Chrome: container survey -> damage -> repair -> quality approval, lease on/off-hire, stock verification, EIR form + print layout, photo upload, CSC / lease expiry register.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (n, ok, x) => { console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + (x || ""))); if (!ok) failures++; };
const checkDigit = s => { const v = "0123456789A_BCDEFGHIJK_LMNOPQRSTU_VWXYZ"; let t = 0; for (let i = 0; i < 10; i++) { t += v.indexOf(s[i]) * 2 ** i; } return t % 11 % 10; };
const num = p => p + checkDigit(p);
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "CO" + tag, name: "Ops " + tag, admin_email: `co${tag}@t.test`, admin_username: "co" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "O" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "co" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const m = (t, code, name, data) => j("POST", "/api/masters/" + t, { code, name, data: data || {} }, token);
  await m("inv_container_size", "S20", "20 FT"); await m("inv_container_ownership", "OWN", "Company owned", { treatment: "Company fixed asset" }); await m("inv_container_ownership", "LSE", "Leased", { treatment: "Leased (not a company asset)" });
  await m("inv_container_grade", "G1", "Grade 1"); await m("inv_container_condition", "GOOD", "Good", { available_for_use: "Yes" }); await m("inv_container_condition", "REP", "Repaired", { available_for_use: "Yes" });
  await m("inv_depot", "CHN", "Chennai Depot"); await m("inv_survey_type", "GIN", "Gate-in survey"); await m("inv_survey_item", "ROOF", "Roof", { applies_to: "All containers" }); await m("inv_survey_item", "FLR", "Floor", { applies_to: "All containers" });
  await m("inv_repair_category", "WLD", "Welding");
  const mk = async (p, own) => { const c = (await j("POST", "/api/containers", { container_number: num(p), size: "20 FT", ownership: own || "Company owned", grade: "Grade 1", condition: "Good" }, token)).body; return c; };
  const c1 = await mk("OPSU100001"); const lc = await mk("OPSU100002", "Leased"); const c3 = await mk("OPSU100003");
  const inMove = async c => { const mv = (await j("POST", "/api/container-movements", { container_id: c.id, direction: "IN", purpose: "RECEIPT", to_depot: "Chennai Depot", movement_date: "2026-11-05", truck_number: "TN01AB1234" }, token)).body; await j("POST", `/api/container-movements/${mv.id}/approve`, null, token); return mv; };
  const mv1 = await inMove(c1); await inMove(c3);
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 950 }); const errs = []; page.on("pageerror", e => errs.push(e.message)); page.on("dialog", d => d.accept("checked and fine"));
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const setv = (sel, val) => page.evaluate((sel, val) => { const e = document.querySelector(sel); e.value = val; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }, sel, val);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const nav = async s => { await click('.tn-row[data-go="' + s + '"]'); await sleep(1800); };
  const api = async p => (await j("GET", p, null, token)).body;
  const actBtn = (grid, act) => page.evaluate((grid, act) => { const b = document.querySelector('[data-ogact^="' + grid + ':' + act + ':"]'); if (b) b.click(); return !!b; }, grid, act);

  const goes = await page.evaluate(() => Array.from(document.querySelectorAll('.tn-row[data-go^="inv-"]')).map(r => r.dataset.go));
  check("menu has Survey, Damage, Repair, Leasing, Stock Verification and Expiry entries", ["inv-surveys", "inv-damages", "inv-repairs", "inv-leases", "inv-counts", "inv-expiry"].every(x => goes.includes(x)), goes.join());

  // survey -> damage
  await nav("inv-surveys");
  check("the survey list starts empty", /No surveys yet/.test(await text("#s-inv-surveys")));
  await click('[data-ognew="surveys"]'); await sleep(1200);
  await setv("#of_cn", c1.container_number); await setv("#of_type", "Gate-in survey"); await setv("#of_sev", "Moderate"); await setv("#of_loc", "Roof"); await setv("#of_desc", "dent on roof panel"); await setv("#of_est", "4500"); await setv("#osc_r0", "DAMAGED"); await setv("#osc_m0", "30 cm dent"); await setv("#osc_r1", "OK");
  await click("#of_save"); await sleep(2200);
  const sv = (await api("/api/container-surveys")).items;
  check("the survey is saved as a draft with its checklist", sv.length === 1 && sv[0].status === "DRAFT" && sv[0].checklist.length === 2 && sv[0].checklist[0].result === "DAMAGED", JSON.stringify(sv).slice(0, 250));
  await actBtn("surveys", "submit"); await sleep(1500); await actBtn("surveys", "approve"); await sleep(2000);
  const dm = (await api("/api/container-damages")).items;
  check("approving the survey opens a damage record", dm.length === 1 && dm[0].status === "OPEN" && dm[0].survey_id === sv[0].id, JSON.stringify(dm).slice(0, 200));

  // damage -> repair order -> approve -> start -> complete -> QC
  await nav("inv-damages");
  check("the damage register shows it", /dent on roof panel|Roof/.test(await text("#s-inv-damages")));
  await actBtn("damages", "repair"); await sleep(1500);
  await setv("#of_cat", "Welding"); await setv("#of_vendor", "Fix-It Works"); await setv("#of_labour", "1000"); await setv("#of_material", "2000"); await click("#of_save"); await sleep(2500);
  let rp = (await api("/api/container-repairs")).items;
  check("a repair work order is created from the damage with the estimate added up", rp.length === 1 && Number(rp[0].estimated_total) === 3000 && rp[0].damage_id === dm[0].id && rp[0].status === "ESTIMATED", JSON.stringify(rp).slice(0, 250));
  await nav("inv-repairs");
  await actBtn("repairs", "approve"); await sleep(1800);
  check("approval makes the container unavailable (under repair)", (await api("/api/containers/" + c1.id)).availability === "UNDER_REPAIR");
  await actBtn("repairs", "start"); await sleep(1500); await actBtn("repairs", "complete"); await sleep(1200);
  await setv("#of_cost", "3200"); await click("#of_save"); await sleep(2200);
  await actBtn("repairs", "qc"); await sleep(1500); await setv("#of_cond", "Repaired"); await click("#of_save"); await sleep(2200);
  const after = await api("/api/containers/" + c1.id); rp = (await api("/api/container-repairs")).items;
  check("quality approval returns the container to AVAILABLE with its new condition", after.condition === "Repaired" && after.availability === "AVAILABLE" && rp[0].status === "QC_APPROVED" && rp[0].over_approved_amount === true, JSON.stringify({ c: after.condition, a: after.availability, s: rp[0].status }));

  // lease
  await nav("inv-leases");
  await click('[data-ognew="leases"]'); await sleep(1200);
  await setv("#of_ag", "LSE-" + tag); await setv("#of_co", "Global Lessor Ltd"); await setv("#of_start", "2026-01-01"); await setv("#of_end", "2026-12-31"); await setv("#of_day", "1.5"); await setv("#of_free", "5"); await click("#of_save"); await sleep(2200);
  const ls = (await api("/api/container-leases")).items;
  check("the lease is created", ls.length === 1 && ls[0].leasing_company === "Global Lessor Ltd");
  await actBtn("leases", "view"); await sleep(1200); await click('[data-oglease^="on:"]'); await sleep(1000);
  await setv("#of_cn", lc.container_number); await setv("#of_date", "2026-02-01"); await click("#of_save"); await sleep(2200);
  check("the leased container is on hire", (await api("/api/containers/" + lc.id)).lease_status === "ON_HIRE");
  await click('[data-oglease^="off:"]'); await sleep(1000); await setv("#of_date", "2026-02-20"); await click("#of_save"); await sleep(2200);
  const leased = await api("/api/container-leases/" + ls[0].id);
  check("off-hire computes days, free days and rental", leased.items[0].status === "OFF_HIRE" && leased.items[0].days === 20 && leased.items[0].free_days === 5 && leased.items[0].amount === "22.50", JSON.stringify(leased.items[0]));
  await click('.modal-ovl .btn-out, [onclick="closeModal()"]').catch(() => {});

  // stock verification
  await page.evaluate(() => closeModal && closeModal()); await nav("inv-counts");
  await click('[data-ognew="counts"]'); await sleep(1200);
  await setv("#of_depot", "Chennai Depot"); await setv("#of_nums", c1.container_number + "\n" + num("GHOU777001")); await click("#of_save"); await sleep(2500);
  const ct = (await api("/api/container-counts")).items[0];
  check("the count reports the container that was not found and the unregistered one", ct.missing.some(x => x.number === c3.container_number) && ct.unexpected.some(x => x.note === "Not in the register"), JSON.stringify(ct).slice(0, 300));
  await page.evaluate(() => closeModal());

  // EIR form + print layout
  await nav("inv-movements");
  await page.evaluate(id => document.querySelector('[data-ogeir="' + id + '"]').click(), mv1.id); await sleep(1500);
  check("the EIR form follows the printed receipt (seals, tires, chassis, container codes, tire codes)", await page.evaluate(() => { const t = document.body.innerText; return /custom seal/i.test(t) && /tattle caps missing/i.test(t) && /king pin upper coupling device/i.test(t) && /12 needs cleanout/i.test(t) && /20 flat tire/i.test(t); }));
  await setv('[data-eir="load_status"]', "EMPTY"); await setv('[data-eir="custom_seal"]', "CS" + tag); await setv('[data-eir="line_seal"]', "MSC123"); await setv('[data-eirtire="lo_front"]', "OK");
  await page.evaluate(() => { document.querySelector('[data-eirchk="Brakes"]').value = "DEFECTIVE"; document.querySelector('[data-eircc="01"]').checked = true; document.querySelector('[data-eirflag="reposition_for_maintenance"]').checked = true; });
  await click("#og_eirsave"); await sleep(2500);
  const eir = (await j("GET", "/api/container-eirs/by-movement/" + mv1.id, null, token)).body;
  check("the EIR is saved with the form details", eir.details.load_status === "EMPTY" && eir.details.custom_seal === "CS" + tag && eir.details.chassis_check.Brakes === "DEFECTIVE" && eir.details.container_codes[0] === "01" && eir.details.reposition_for_maintenance === true, JSON.stringify(eir.details));
  await page.evaluate(() => { window.__printed = ""; window.open = () => ({ document: { write: h => { window.__printed += h; }, close() {} }, print() {} }); });
  await click("#og_eirprint"); await sleep(900);
  const printed = await page.evaluate(() => window.__printed);
  check("the printed EIR has the layout of the sample: title, IN/OUT/LOAD/EMPTY boxes, seals, tires, chassis table, codes, signature block", /EQUIPMENT INTERCHANGE RECEIPT/.test(printed) && printed.includes("&#9746; IN") && printed.includes("&#9746; EMPTY") && printed.includes("CS" + tag) && /TATTLE CAPS MISSING/.test(printed) && /Chassis \(visual inspection\)/.test(printed) && /TIRE CODES/.test(printed) && /CUSTOMER SIGNATURE/.test(printed) && printed.includes(eir.number) && printed.includes(c1.container_number), printed.slice(0, 200));

  // photos
  fs.writeFileSync("tmp_photo.png", Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64"));
  await page.evaluate(id => { window.INVC.openId = id; go("inv-container-detail"); }, c1.id); await sleep(2800);
  await page.waitForSelector('[data-invtab="7"]', { timeout: 15000 });
  await page.evaluate(() => document.querySelector('[data-invtab="7"]').click());
  const fileInput = await page.$("#ogContPhotos [data-ogfile]"); await fileInput.uploadFile("tmp_photo.png"); await sleep(3000);
  const photos = await api("/api/container-attachments?entity_type=container&entity_id=" + c1.id);
  check("a photo is uploaded from the container's Photos tab and listed with a thumbnail", photos.length === 1 && photos[0].category === "PHOTO" && await page.evaluate(() => document.querySelectorAll("#ogContPhotos [data-ogthumbs] img").length === 1), JSON.stringify(photos).slice(0, 200));
  check("the container has Survey, Damage & Repairs and Lease tabs with its records", await page.evaluate(() => { const t = document.getElementById("s-inv-container-detail").innerText; return /Survey History/.test(t) && /Damage & Repairs/.test(t) && /Lease Details/.test(t); }));
  fs.unlinkSync("tmp_photo.png");

  // expiry register + dashboard alerts
  await j("PATCH", "/api/containers/" + c3.id, { csc_valid_until: new Date(Date.now() + 45 * 864e5).toISOString().slice(0, 10) }, token);
  await j("PATCH", "/api/containers/" + c1.id, { csc_valid_until: new Date(Date.now() - 5 * 864e5).toISOString().slice(0, 10) }, token);
  await nav("inv-expiry");
  const ex = await text("#s-inv-expiry");
  check("the expiry register lists the expired and the due CSC", ex.includes(c1.container_number) && ex.includes(c3.container_number) && /Expired/.test(ex), ex.slice(0, 250));
  await setv("#ogExpDays", "30"); await sleep(1500);
  check("narrowing the window hides what falls due later", !(await text("#s-inv-expiry")).includes(c3.container_number) && (await text("#s-inv-expiry")).includes(c1.container_number));
  await nav("inv-dashboard"); await sleep(1500);
  check("the dashboard shows a Needs attention card with the CSC alert", /Needs attention/.test(await text("#s-inv-dashboard")) && /CSC/.test(await text("#s-inv-dashboard")));
  check("no script errors", errs.length === 0, errs.join(" | ").slice(0, 300));
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
