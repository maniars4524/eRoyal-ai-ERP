// Real Chrome: Vehicle Inventory - fleet dashboard, vehicle register + detail, allocation rules, workshop, preventive plans, tyres by wheel position, fuel, photos.
const puppeteer = require("puppeteer-core");
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
  const org = await j("POST", "/api/organizations", { code: "FL" + tag, name: "Fleet " + tag, admin_email: `fl${tag}@t.test`, admin_username: "fl" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "F" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "fl" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const m = (t, code, name, data) => j("POST", "/api/masters/" + t, { code, name, data: data || {} }, token);
  await m("inv_vehicle_category", "PM", "Prime mover"); await m("inv_vehicle_ownership", "OWN", "Company owned", { company_asset: "Yes" }); await m("inv_axle_config", "AX4", "4 wheel", { number_of_axles: "2", total_wheel_positions: "4", position_labels: "Front-L\nFront-R\nRear-L\nRear-R" });
  await m("inv_document_type", "INS", "Insurance", { applies_to: "Vehicle", has_expiry_date: "Yes", mandatory: "Yes" }); await m("inv_maintenance_category", "PRV", "Preventive service"); await m("inv_tyre_brand", "MIC", "Michelin"); await m("inv_warehouse", "WH", "Main store");
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 950 }); const errs = []; page.on("pageerror", e => errs.push(e.message)); page.on("dialog", d => d.accept("checked"));
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const setv = (sel, val) => page.evaluate((sel, val) => { const e = document.querySelector(sel); e.value = val; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }, sel, val);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const nav = async s => { await click('.tn-row[data-go="' + s + '"]'); await sleep(1800); };
  const api = async p => (await j("GET", p, null, token)).body;
  const act = (grid, a) => page.evaluate((grid, a) => { const x = document.querySelector('[data-ogact^="' + grid + ':' + a + ':"]'); if (x) x.click(); return !!x; }, grid, a);
  const tab = async n => { await page.evaluate(n => document.querySelector('[data-invtab="' + n + '"]').click(), n); await sleep(300); };
  const future = d => new Date(Date.now() + d * 864e5).toISOString().slice(0, 10);

  const goes = await page.evaluate(() => Array.from(document.querySelectorAll('.tn-row[data-go^="inv-"]')).map(r => r.dataset.go));
  check("the Vehicle Inventory menu has Fleet Dashboard, Fleet Inventory, Workshop, Preventive Maintenance, Tyres and Fuel", ["inv-fleet", "inv-vehicles", "inv-workshop", "inv-plans", "inv-tyres", "inv-fuel"].every(x => goes.includes(x)), goes.join());
  await nav("inv-fleet");
  const d0 = (await text("#s-inv-fleet")).replace(/\n/g, " ");
  check("the fleet dashboard starts at zero with no sample figures", /Vehicles\s*0/i.test(d0) && /Nothing needs attention/.test(d0), d0.slice(0, 200));

  // register a vehicle through the form
  await nav("inv-vehicles");
  check("the vehicle list starts empty", /No vehicles registered yet/.test(await text("#s-inv-vehicles")));
  await click('[data-ognew="vehicles"]'); await sleep(1800);
  await setv("#vf_registration_number", "tn 09 ab 1234"); await setv("#vf_category", "Prime mover"); await setv("#vf_make", "Tata"); await setv("#vf_model", "Prima"); await setv("#vf_axle_config", "4 wheel"); await setv("#vf_ownership", "Company owned"); await setv("#vf_odometer", "1000"); await setv("#vf_chassis_number", "CH" + tag);
  await click("#flSave"); await sleep(2800);
  const vs = (await api("/api/vehicles")).items;
  check("the vehicle is saved with a clean registration number and an asset ID", vs.length === 1 && vs[0].registration_number === "TN09AB1234" && !!vs[0].asset_id && vs[0].odometer === 1000 && vs[0].status === "AVAILABLE", JSON.stringify(vs).slice(0, 250));
  const vid = vs[0].id;
  check("after saving it opens the vehicle with its tabs", /Status & Allocation/.test(await text("#s-inv-vehicle-detail")) && /Tyres/.test(await text("#s-inv-vehicle-detail")));

  // allocation is blocked until a mandatory document is on file
  await tab(1);
  check("allocation is refused: the mandatory Insurance is missing, and the reason is shown", /No Insurance on file/.test(await text("#s-inv-vehicle-detail")));
  await tab(2); await click('[data-fl="adddoc"]'); await sleep(1200);
  await setv("#of_t", "Insurance"); await setv("#of_no", "POL-1"); await setv("#of_exp", future(200)); await click("#of_save"); await sleep(2500);
  await tab(1);
  check("with a valid Insurance the vehicle can be allocated", /Yes/.test(await text("#s-inv-vehicle-detail [data-invpanel=\"1\"]")) && !/No Insurance/.test(await text("#s-inv-vehicle-detail")));
  await nav("inv-vehicles"); await act("vehicles", "allocate"); await sleep(1200); await setv("#of_drv", "Ravi"); await click("#of_save"); await sleep(2200);
  check("allocating puts it on a trip with the driver", (await api("/api/vehicles/" + vid)).status === "ON_TRIP" && (await api("/api/vehicles/" + vid)).driver === "Ravi");
  await act("vehicles", "release"); await sleep(1200); await setv("#of_odo", "1250"); await click("#of_save"); await sleep(2200);
  check("ending the trip returns it and records the odometer", (await api("/api/vehicles/" + vid)).status === "AVAILABLE" && (await api("/api/vehicles/" + vid)).odometer === 1250);

  // workshop
  await nav("inv-workshop"); await click('[data-ognew="workshop"]'); await sleep(1300);
  await setv("#of_reg", "TN09AB1234"); await setv("#of_cat", "Preventive service"); await setv("#of_cmp", "oil change"); await click("#of_save"); await sleep(2500);
  await act("workshop", "start"); await sleep(1800);
  check("starting the work order puts the vehicle in the workshop", (await api("/api/vehicles/" + vid)).status === "UNDER_MAINTENANCE");
  await act("workshop", "complete"); await sleep(1200);
  await setv("#of_lab", "500"); await setv("#of_mat", "1500"); await setv("#of_rep", "oil and filter"); await click("#of_save"); await sleep(2500);
  await act("workshop", "close"); await sleep(2500);
  const v2 = await api("/api/vehicles/" + vid), wo = (await api("/api/vehicle-work-orders")).items[0];
  check("approving the finished work order closes it and returns the vehicle, with the cost counted once", wo.status === "CLOSED" && Number(wo.total_cost) === 2000 && v2.status === "AVAILABLE" && Number(v2.costs.maintenance) === 2000, JSON.stringify({ s: wo.status, c: wo.total_cost, v: v2.status }));

  // preventive plan
  await nav("inv-plans"); await click('[data-ognew="plans"]'); await sleep(1300);
  await setv("#of_reg", "TN09AB1234"); await setv("#of_name", "5,000 km service"); await setv("#of_km", "5000"); await click("#of_save"); await sleep(2300);
  check("the plan is created and is not due yet", (await api("/api/vehicle-plans")).items.length === 1 && (await api("/api/vehicle-plans")).items[0].overdue === false);
  await j("POST", "/api/vehicles/" + vid + "/odometer", { odometer: 6300 }, token);
  await nav("inv-plans");
  check("after 5,000 km the plan shows as overdue", /Overdue/.test(await text("#s-inv-plans")));
  await click("#flGenAll"); await sleep(2200); await click("#flGenAll"); await sleep(2200);
  const plan = (await api("/api/vehicle-work-orders")).items.filter(w => w.plan_id);
  check("the work order for the due service is created once, not twice", plan.length === 1, plan.length + "");

  // tyres
  await nav("inv-tyres"); await click('[data-ognew="tyres"]'); await sleep(1300);
  await setv("#of_sn", "tyre-" + tag); await setv("#of_brand", "Michelin"); await setv("#of_size", "295/80R22.5"); await setv("#of_pc", "20000"); await setv("#of_tread", "16"); await setv("#of_wh", "Main store"); await click("#of_save"); await sleep(2300);
  check("the tyre is registered in stock", (await api("/api/tyres")).items.length === 1 && (await api("/api/tyres")).items[0].serial_number === "TYRE-" + tag.toUpperCase());
  await page.evaluate(id => { window.FLEETC.openId = id; go("inv-vehicle-detail"); }, vid); await sleep(2600); await tab(4);
  check("the Tyres tab shows the wheel positions from the vehicle's configuration, all empty", await page.evaluate(() => { const t = document.querySelector('#s-inv-vehicle-detail [data-invpanel="4"]').innerText; return /Front-L/.test(t) && /Front-R/.test(t) && /Rear-L/.test(t) && /Rear-R/.test(t) && (t.match(/empty/g) || []).length === 4; }));
  await page.evaluate(() => Array.from(document.querySelectorAll('[data-fl^="tyre-fit:"]')).find(b => b.dataset.fl.endsWith("Front-L")).click()); await sleep(1500);
  await page.evaluate(() => { const s = document.getElementById("of_ty"); s.value = s.options[1].value; }); await setv("#of_odo", "6400"); await click("#of_save"); await sleep(2500);
  const lay = await api("/api/vehicles/" + vid + "/tyres");
  check("the tyre is fitted to Front-L and the layout shows it", lay.layout[0].tyre && lay.layout[0].tyre.serial_number === "TYRE-" + tag.toUpperCase() && lay.layout[0].tyre.status === "FITTED", JSON.stringify(lay.layout[0]).slice(0, 200));
  await tab(4);
  await page.evaluate(() => document.querySelector('[data-fl^="tyre-remove:"]').click()); await sleep(1300);
  await setv("#of_odo", "11400"); await setv("#of_to", "RETREADING"); await setv("#of_why", "worn, retread"); await setv("#of_cost", "4000"); await click("#of_save"); await sleep(2500);
  const ty = (await api("/api/tyres")).items[0];
  check("removing it for retreading records the kilometres and the retread cost", ty.status === "RETREADING" && ty.total_km === 5000 && Number(ty.total_cost) === 24000 && ty.cost_per_km === "4.8000", JSON.stringify(ty).slice(0, 250));
  await nav("inv-tyres"); await act("tyres", "receive"); await sleep(1300); await setv("#of_tread", "14"); await setv("#of_wh", "Main store"); await click("#of_save"); await sleep(2300);
  check("receiving it back puts it in stock as a retread", (await api("/api/tyres")).items[0].status === "IN_STOCK" && (await api("/api/tyres")).items[0].retread_count === 1);

  // fuel
  await nav("inv-fuel"); await click('[data-ognew="fuel"]'); await sleep(1300);
  await setv("#of_reg", "TN09AB1234"); await setv("#of_odo", "11500"); await setv("#of_l", "100"); await setv("#of_amt", "9000"); await click("#of_save"); await sleep(2300);
  await click('[data-ognew="fuel"]'); await sleep(1300); await setv("#of_reg", "TN09AB1234"); await setv("#of_odo", "11900"); await setv("#of_l", "100"); await setv("#of_amt", "9000"); await click("#of_save"); await sleep(2500);
  const fl = (await api("/api/fuel")).items;
  check("two fills give a km per litre figure from the odometer difference", fl.length === 2 && fl.some(f => f.km_per_litre === "4.00"), JSON.stringify(fl).slice(0, 250));
  check("the fuel screen shows the figures", /4\.00/.test(await text("#s-inv-fuel")) && /TN09AB1234/.test(await text("#s-inv-fuel")));

  // photos
  fs.writeFileSync("tmp_veh.png", Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64"));
  await page.evaluate(id => { window.FLEETC.openId = id; go("inv-vehicle-detail"); }, vid); await sleep(2600); await tab(7);
  const fi = await page.$("#flPhotos [data-ogfile]"); await fi.uploadFile("tmp_veh.png"); await sleep(3000); fs.unlinkSync("tmp_veh.png");
  check("a photo is attached to the vehicle", (await api("/api/container-attachments?entity_type=vehicle&entity_id=" + vid)).length === 1 && await page.evaluate(() => document.querySelectorAll("#flPhotos [data-ogthumbs] img").length === 1));

  // dashboard with real figures and alerts
  await j("POST", "/api/vehicles/" + vid + "/documents", { doc_type: "Insurance", number: "P2", expiry_date: future(10) }, token);
  await nav("inv-fleet");
  const d1 = (await text("#s-inv-fleet")).replace(/\n/g, " ");
  check("the dashboard now shows the vehicle, the maintenance and fuel cost and the insurance expiring", /Vehicles\s*1/i.test(d1) && /Maintenance cost/i.test(d1) && /Insurance of TN09AB1234 expires in 10 day/.test(d1), d1.slice(0, 400));
  check("no script errors", errs.length === 0, errs.join(" | ").slice(0, 300));
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
