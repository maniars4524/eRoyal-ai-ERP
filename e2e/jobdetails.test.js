// Headless UI check: Save / Save Changes on the job tabs, restored on reopen, HBL number company+type+running+POL+POD.
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
  const org = await j("POST", "/api/organizations", { code: "RTS" + tag.slice(-4).toUpperCase(), name: "JobDetails " + tag, admin_email: `jd${tag}@t.test`, admin_username: "jd" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "J" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "jd" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  for (const [c, n, r] of [["SH1", "Acme Exports", ["shipper"]], ["CN1", "Solvay Consignee", ["consignee"]], ["AG1", "Hamburg Agent GmbH", ["agent"]], ["CH1", "Fast CHA", ["cha"]], ["TR1", "Road Movers", ["transporter"]]]) await j("POST", "/api/customers", { code: c + tag.slice(-3).toUpperCase(), name: n, roles: r, data: {} }, token);
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "SGSIN" }, token)).body;
  const open = async () => {
    const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
      beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
    await sleep(3000); dom.window.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
    return dom.window;
  };
  let w = await open(), d = w.document;
  const q = s => d.querySelector(s), btn = tab => q(`#${tab} [data-jd-save]`);
  check("every tab has a Save button before anything is saved", ["jtpc", "jt2", "jt3"].every(t => btn(t) && btn(t).textContent === "Save"), ["jtpc", "jt2", "jt3"].map(t => btn(t) && btn(t).textContent).join(","));
  check("the POL / POD code column shows the job's codes", q("#jobBl_polpod").value === "MAA / SIN", q("#jobBl_polpod").value);

  const opts = id => Array.from(q("#" + id).options).map(x => x.value).filter(Boolean);
  check("Shipper lists only shipper-category parties", JSON.stringify(opts("pc_shipper")) === JSON.stringify(["Acme Exports"]), opts("pc_shipper").join("|"));
  check("Consignee lists only consignees", JSON.stringify(opts("pc_consignee")) === JSON.stringify(["Solvay Consignee"]), opts("pc_consignee").join("|"));
  check("Agents (origin / delivery / co-loader) list only agents", ["pc_originAgent", "pc_deliveryAgent", "pc_coloader"].every(i => JSON.stringify(opts(i)) === JSON.stringify(["Hamburg Agent GmbH"])), opts("pc_originAgent").join("|"));
  check("CHA and Transport list only their own categories", JSON.stringify(opts("pc_cha")) === JSON.stringify(["Fast CHA"]) && JSON.stringify(opts("pc_transport")) === JSON.stringify(["Road Movers"]), opts("pc_cha").join("|") + " / " + opts("pc_transport").join("|"));
  // Parties & cargo
  q("#pc_commodity").value = "Garments"; q("#pc_commodity").dispatchEvent(new w.Event("input", { bubbles: true }));
  q("#pc_pkgs").value = "120"; q("#pc_pkgs").dispatchEvent(new w.Event("input", { bubbles: true }));
  q("#pc_gwt").value = "2400.5"; q("#pc_gwt").dispatchEvent(new w.Event("input", { bubbles: true }));
  btn("jtpc").click(); await sleep(1500);
  check("saving Parties & Cargo flips the button to Save Changes", btn("jtpc").textContent === "Save Changes", btn("jtpc").textContent);
  const after = (await j("GET", "/api/shipments", null, token)).body.find(x => x.job_number === job.job_number);
  check("the saved cargo reaches the job record (lists and reports)", after.commodity === "Garments" && after.packages === 120 && Number(after.gross_weight) === 2400.5, JSON.stringify(after).slice(0, 200));

  // Containers: one row only even after pressing Add once, and a bad number is refused
  q("#addContainerBtn").click(); await sleep(200);
  check("Add Container adds exactly one row", d.querySelectorAll("#containerTableBody tr[data-jd-cont]").length === 1, d.querySelectorAll("#containerTableBody tr").length);
  const row = q("#containerTableBody tr[data-jd-cont]"), no = row.querySelector('[data-k="container_no"]');
  no.value = "BAD1"; no.dispatchEvent(new w.Event("input", { bubbles: true }));
  btn("jt2").click(); await sleep(1200);
  check("an invalid container number is refused and the button stays Save", btn("jt2").textContent === "Save", btn("jt2").textContent);
  no.value = "msku1234567"; no.dispatchEvent(new w.Event("input", { bubbles: true }));
  row.querySelector('[data-k="seal_no"]').value = "SL001";
  for (const [k, v] of [["gross_wt", "13451.780"], ["net_wt", "12347.080"]]) { const i = row.querySelector(`[data-k="${k}"]`); i.value = v; i.dispatchEvent(new w.Event("input", { bubbles: true })); }
  check("container weights add up into the totals", q("#jc_gwt").value === "13451.780" && q("#jc_nwt").value === "12347.080" && q("#jc_gwt").readOnly, q("#jc_gwt").value);
  q("#jc_marks").value = "1-877 CE NOS 1-877"; q("#jc_desc").value = "EIGHT HUNDRED CARTONS ONLY";
  btn("jt2").click(); await sleep(1500);
  check("a valid container saves (Save Changes)", btn("jt2").textContent === "Save Changes" && no.value === "MSKU1234567", btn("jt2").textContent + " " + no.value);

  // House BL
  q("#jobBlHbltype").value = "MTO"; q("#jobBlHbltype").dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(800);
  check("the next House BL number is previewed", /RTS.*MTO001MAASIN/.test(q("#jobBl_hblNo").placeholder), q("#jobBl_hblNo").placeholder);
  q("#jobBl_genHblBtn").click(); await sleep(1800);
  const code = org.body.organization.code.slice(0, 3);
  check("Generate HBL issues company code + type + running no + POL + POD", q("#jobBl_hblNo").value === code + "MTO001MAASIN", q("#jobBl_hblNo").value);
  check("the button is locked after issuing", q("#jobBl_genHblBtn").disabled === true);
  q("#jobBl_mblNo").value = "MAEU123456"; q("#jobBl_place").value = "Chennai";
  btn("jt3").click(); await sleep(1500);
  check("saving the BL tab keeps the House BL number", btn("jt3").textContent === "Save Changes" && q("#jobBl_hblNo").value === code + "MTO001MAASIN", btn("jt3").textContent + q("#jobBl_hblNo").value);

  // reopen: everything is restored
  w.close(); w = await open(); d = w.document;
  check("reopening the job restores containers, cargo and the House BL", d.querySelector('#containerTableBody [data-k="container_no"]') && d.querySelector('#containerTableBody [data-k="container_no"]').value === "MSKU1234567" && d.querySelector("#pc_commodity").value === "Garments" && d.querySelector("#jobBl_hblNo").value === code + "MTO001MAASIN" && d.querySelector("#jobBl_mblNo").value === "MAEU123456",
    [d.querySelector("#pc_commodity").value, d.querySelector("#jobBl_hblNo").value, d.querySelector("#jobBl_mblNo").value].join("|"));
  check("buttons read Save Changes after reopening", ["jtpc", "jt2", "jt3"].every(t => d.querySelector(`#${t} [data-jd-save]`).textContent === "Save Changes"));

  const bar = Array.from(d.querySelectorAll("#s-job-detail .ph .pa button")).map(b => b.textContent.trim());
  check("the job header has no Create Invoice button", !bar.some(t => /Create Invoice/.test(t)), bar.join("|"));
  check("Tracking sits after Close Job in the header", bar.findIndex(t => /Tracking/.test(t)) > bar.findIndex(t => /Close Job/.test(t)) && bar.findIndex(t => /Close Job/.test(t)) >= 0, bar.join("|"));
  check("the stage strip shows Draft, Confirmed, On Transit, Closed, Re-open", Array.from(d.querySelectorAll("#s-job-detail .sf .sfn")).map(n => n.textContent.trim()).join(",") === "Draft,Confirmed,On Transit,Closed,Re-open", Array.from(d.querySelectorAll("#s-job-detail .sf .sfn")).map(n => n.textContent.trim()).join(","));


  // ---- BL document, stages and Out Docs ----
  await j("PUT", "/api/company/doc-profile", { reg_mto: "OTI LICENCE NO. 019654N", address: "Chennai, India", phone: "+91 44 1234", signatory: "Authorised Signatory" }, token);
  check("the job header has no BL Draft button and the BL tab has no View BL Draft", !bar.some(t => /BL Draft/.test(t)) && !/View BL Draft/.test(d.getElementById("jt3").textContent));
  check("Out Docs shows only the job's own documents (no four-way filter row)", d.getElementById("odModeRow").style.display === "none" && d.getElementById("odSeaExportDocs").style.display !== "none" && d.getElementById("odSeaImportDocs").style.display === "none" && d.getElementById("odAirExportDocs").style.display === "none", [d.getElementById("odSeaExportDocs").style.display, d.getElementById("odSeaImportDocs").style.display].join("|"));
  q("#jobBl_confirmBtn").click(); await sleep(1500);
  check("Confirm BL moves the stage to Confirmed", q("#jobBl_stage").value === "Confirmed", q("#jobBl_stage").value);
  q("#jobBl_relType").value = "Original"; q("#jobBl_releaseBtn").click(); await sleep(1500);
  check("Original release before payment is stopped, and the admin is offered the override with a reason", q("#jobBl_stage").value === "Confirmed" && d.getElementById("blOvReason") !== null && /full payment/.test(d.getElementById("modalBody").textContent), q("#jobBl_stage").value);
  d.getElementById("blOvGo").click(); await sleep(500);
  check("the override needs a reason", q("#jobBl_stage").value === "Confirmed");
  d.getElementById("blOvReason").value = "Credit customer approved by MD"; d.getElementById("blOvGo").click(); await sleep(1800);
  check("with a reason the Original BL is released", q("#jobBl_stage").value === "Released", q("#jobBl_stage").value);
  const act = (await j("GET", `/api/jobs/${job.job_number}/activity?limit=50`, null, token)).body.items;
  check("the override and its reason are in the job history", act.some(e => e.event_type === "BL_RELEASE_OVERRIDDEN" && /MD/.test(e.reason || "")), act.map(e => e.event_type).join(","));
  d.getElementById("s-bl-draft"); w.go("bl-draft"); await sleep(2500);
  const doc = d.querySelector("#blDraftPrintZone .bldoc");
  check("net weight above gross is refused", (await j("PUT", `/api/jobs/${job.job_number}/details/containers`, { data: [{ container_no: "MSKU1234567", gross_wt: "10", net_wt: "20" }] }, token)).status === 422);
  check("the BL document is produced in the MTD layout with the company registration, number, shipper and container", doc && /MULTIMODAL TRANSPORT DOCUMENT/.test(doc.textContent) && /OTI LICENCE NO\. 019654N/.test(doc.textContent) && doc.textContent.includes(code + "MTO001MAASIN") && /MSKU1234567/.test(doc.textContent) && /ORIGINAL/.test(doc.textContent) && /GR\. WT: 13451\.780/.test(doc.textContent) && /EIGHT HUNDRED/.test(doc.textContent) && /CE NOS 1-877/.test(doc.textContent), doc ? doc.textContent.slice(0, 200) : "no document");
  check("the document screen has Print, PDF and Send to customer", ["print", "pdf", "mail"].every(k => d.querySelector(`#s-bl-draft [data-bldoc="${k}"]`)));
  w.go("job-detail", { jobNo: job.job_number }); await sleep(3000);
  d.querySelector('#s-job-detail [data-jl="tracking"]').click(); await sleep(700);
  check("one click on Tracking opens the milestones as a pop-up (the page does not change tab)", d.getElementById("modalOvl").classList.contains("on") && /Operational Milestones/.test(d.getElementById("modalBody").textContent) && !d.getElementById("jt6").classList.contains("on"));
  d.getElementById("modalOvl").classList.remove("on");
  d.querySelector('#s-job-detail [data-jl="tracking"]').dispatchEvent(new w.MouseEvent("dblclick", { bubbles: true })); await sleep(500);
  check("double-click opens the job's tracking page", d.getElementById("jt6").classList.contains("on") && !d.getElementById("modalOvl").classList.contains("on"));

  const rd = (await j("GET", `/api/jobs/${job.job_number}/report-data`, null, token)).body;
  check("marks, description and weights are saved and shown on the BL", rd.details.cargo.marks.includes("CE NOS") && rd.details.cargo.cargoDesc.includes("EIGHT HUNDRED") && rd.details.containers[0].gross_wt === "13451.780", JSON.stringify(rd.details.cargo));
  check("documents get the saved details", rd.details && rd.details.bl && rd.details.bl.hblNo === code + "MTO001MAASIN" && rd.details.containers.length === 1, JSON.stringify(rd.details).slice(0, 200));
  console.log(failures ? failures + " FAILED" : "ALL PASSED");
  process.exit(failures ? 1 : 0);
})();
