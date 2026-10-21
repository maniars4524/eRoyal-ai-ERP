// Headless UI check: Charge Entry (job) and the quotation show Sell / Revenue on the left and Buy / Cost on the right with a Flip button; one charge head serves both sides;
// Client comes from the job's parties, Vendor from the Organization Master vendors; an AP-only charge has no sell side and an AR-only charge no buy side.
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
  const org = await j("POST", "/api/organizations", { code: "FL" + tag.slice(-5).toUpperCase(), name: "Flip " + tag, admin_email: `fl${tag}@t.test`, admin_username: "fl" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "F" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "fl" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const shipper = (await j("POST", "/api/customers", { code: "FSH1", name: "Flip Shipper", roles: ["shipper"], data: {} }, token)).body;
  await j("POST", "/api/customers", { code: "FST1", name: "Flip Stranger", roles: ["shipper"], data: {} }, token);
  await j("POST", "/api/customers", { code: "FCR1", name: "Flip Carrier", roles: ["carrier"], data: {} }, token);
  await j("POST", "/api/masters/charge", { code: "FLAP", name: "Flip AP Charge", data: { ar_ap: "AP", hsn_code: "996511", gst_percent: "0" } }, token);
  await j("POST", "/api/masters/charge", { code: "FLAR", name: "Flip AR Charge", data: { ar_ap: "AR", hsn_code: "996511", gst_percent: "0" } }, token);
  await j("POST", "/api/masters/charge", { code: "FLBO", name: "Flip Both Charge", data: { ar_ap: "Both", hsn_code: "996511", gst_percent: "0" } }, token);
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "SGSIN", customer_id: shipper.id }, token)).body;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s), qa = s => Array.from(d.querySelectorAll(s));
  await sleep(3500); w.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
  q('.tbb[data-tab="jt5"]').click(); await sleep(800);
  q("#jc_addRowBtn").click(); await sleep(800);
  const order = () => { const top = qa("#jc_table thead tr:first-child th").map(t => t.textContent.trim()).filter(t => /SELL|BUY/.test(t)); const cells = qa("#jc_tbody tr:first-child td").map(t => t.className); return { head: top, firstSide: (cells.find(c => /jc-col-(buy|sell)/.test(c)) || "").match(/jc-col-(buy|sell)/)[1] }; };
  let o = order();
  check("job charges: Sell / Revenue is on the left by default", /SELL/.test(o.head[0]) && /BUY/.test(o.head[1]) && o.firstSide === "sell", JSON.stringify(o));
  // ---- parties
  const clientOpts = Array.from(q('[data-jc-f="sell.client"]').options).map(x => x.value).filter(Boolean), vendorOpts = Array.from(q('[data-jc-f="buy.vendor"]').options).map(x => x.value).filter(Boolean);
  check("Client lists only the job's own parties", clientOpts.join() === "Flip Shipper", clientOpts.join());
  check("Vendor lists the vendor-category parties of the Organization Master", vendorOpts.includes("Flip Carrier") && !vendorOpts.includes("Flip Shipper") && !vendorOpts.includes("Flip Stranger"), vendorOpts.join());
  check("both are pick-lists (a name cannot be typed freely)", q('[data-jc-f="sell.client"]').tagName === "SELECT" && q('[data-jc-f="buy.vendor"]').tagName === "SELECT");
  // ---- buy / sell applicability
  const setCharge = async name => { const sel = q('[data-jc-f="chargeName"]'); sel.value = name; sel.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(400); };
  await setCharge("Flip AP Charge");
  check("an AP-only charge has no sell side: sell fields are disabled, buy fields stay open", q('[data-jc-f="sell.rate"]').disabled === true && q('[data-jc-f="sell.client"]').disabled === true && q('[data-jc-f="buy.rate"]').disabled === false);
  await setCharge("Flip AR Charge");
  check("an AR-only charge has no buy side", q('[data-jc-f="buy.rate"]').disabled === true && q('[data-jc-f="sell.rate"]').disabled === false);
  await setCharge("Flip Both Charge");
  check("a charge for both sides opens both", q('[data-jc-f="buy.rate"]').disabled === false && q('[data-jc-f="sell.rate"]').disabled === false);
  // ---- flip
  const sell = q('[data-jc-f="sell.rate"]'); sell.value = "500"; sell.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(300);
  q("#jc_flipBtn").click(); await sleep(500);
  o = order();
  check("Flip puts Buy / Cost on the left", /BUY/.test(o.head[0]) && /SELL/.test(o.head[1]) && o.firstSide === "buy", JSON.stringify(o));
  check("the entered rate survives the flip and amounts still calculate", q('[data-jc-f="sell.rate"]').value === "500" && /500/.test(qa("#jc_tbody td.jc-col-sell.mono")[0].textContent), qa("#jc_tbody td.jc-col-sell.mono").map(x => x.textContent).join("|"));
  const buy = q('[data-jc-f="buy.rate"]'); buy.value = "300"; buy.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(300);
  check("buy amounts are updated in their own cells while flipped", /300/.test(qa("#jc_tbody td.jc-col-buy.mono")[0].textContent), qa("#jc_tbody td.jc-col-buy.mono").map(x => x.textContent).join("|"));
  check("S.No and the charge name come before both blocks in either order", /^1$/.test(qa("#jc_tbody tr:first-child td")[1].textContent.trim()) && !!qa("#jc_tbody tr:first-child td")[2].querySelector('[data-jc-f="chargeName"]'));
  check("the choice is remembered", w.localStorage.getItem("esway_jc_sell_first") === "0");
  q("#jc_flipBtn").click(); await sleep(500);
  o = order();
  check("Flip again restores Sell on the left", /SELL/.test(o.head[0]) && o.firstSide === "sell" && w.localStorage.getItem("esway_jc_sell_first") === "1", JSON.stringify(o));
  // ---- quotation: one charge head
  w.go("quotation-new"); await sleep(2000);
  const pick = async (gridSel, value) => { const card = value ? q(`${gridSel} .wiz-card[data-pick="${value}"]`) : q(`${gridSel} .wiz-card`); card.click(); await sleep(250); };
  await pick("#quotWizModeGrid", "Sea"); q('#quotWizDirGrid .wiz-dir-pill[data-pick="Export"]').click(); await sleep(250); await pick("#quotWizTypeGrid", "FCL"); await pick("#quotWizQtypeGrid"); await pick("#quotWizCarrierGrid", "single");
  q("#quotWizNext").click(); await sleep(900);
  const blocks = () => qa("table.qk-t.pair thead tr:first-child th[class*=qk-h-]").map(t => t.className.replace("qk-h-", ""));
  check("quotation: Sell (Revenue) block on the left, Buy (Cost) on the right, one Particulars column", blocks().join() === "sell,buy" && /SELL \(REVENUE\)/.test(q("th.qk-h-sell").textContent) && /BUY \(COST\)/.test(q("th.qk-h-buy").textContent) && qa('[data-ql$=":charge_name"]').length === 1, blocks().join());
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  setf(q('[data-ql="sell:0:charge_name"]'), "Flip AP Charge"); await sleep(300);
  check("quotation: an AP-only charge has no sell side", q('[data-ql="sell:0:rate"]').disabled === true && q('[data-ql="buy:0:rate"]').disabled === false);
  setf(q('[data-ql="sell:0:charge_name"]'), "Flip Both Charge"); await sleep(300);
  check("quotation: a charge for both sides opens both, and the buy line took the same charge", q('[data-ql="sell:0:rate"]').disabled === false && q('[data-ql="buy:0:rate"]').disabled === false && w.QK.buy[0].charge_name === "Flip Both Charge");
  q('[data-qa="add"]').click(); await sleep(600);
  check("Add Charge adds one row for both sides", qa("tr[data-key^='pair:']").length === 2 && w.QK.sell.length === 2 && w.QK.buy.length === 2);
  q("[data-qk-flip]").click(); await sleep(600);
  check("quotation Flip swaps the blocks and remembers it", blocks().join() === "buy,sell" && w.localStorage.getItem("esway_qk_sell_first") === "0", blocks().join());
  q("[data-qk-flip]").click(); await sleep(600);
  check("and back", blocks().join() === "sell,buy");
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
