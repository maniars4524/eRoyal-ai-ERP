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

  const rd = (await j("GET", `/api/jobs/${job.job_number}/report-data`, null, token)).body;
  check("documents get the saved details", rd.details && rd.details.bl && rd.details.bl.hblNo === code + "MTO001MAASIN" && rd.details.containers.length === 1, JSON.stringify(rd.details).slice(0, 200));
  console.log(failures ? failures + " FAILED" : "ALL PASSED");
  process.exit(failures ? 1 : 0);
})();
