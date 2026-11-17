const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (m, p, b, t) => { const r = await fetch(BASE + p, { method: m, headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) }, body: b ? JSON.stringify(b) : undefined }); let x = null; try { x = await r.json(); } catch (e) {} return { status: r.status, body: x }; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "SP" + tag, name: "Sp " + tag, admin_email: `sp${tag}@t.test`, admin_username: "sp" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "M", admin_last_name: "R" }, dev.body.access_token);
  const t = (await j("POST", "/api/auth/login", { identifier: "sp" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const d = (await j("POST", "/api/documents", { doc_type: "invoice", party_name: "ZED " + tag, supply_type: "intra", lines: [{ charge_name: "X", qty: "1", rate: "1000", tax_type: "Taxable", tax_pct: "18" }] }, t)).body; await j("POST", `/api/documents/${d.id}/post`, null, t);
  const tb = (await j("GET", "/api/gl/trial-balance", null, t)).body; const g = tb.rows.find(r => r.kind === "group" && r.name === "SUNDRY DEBTORS");
  console.log(JSON.stringify(g)); const gs = (await j("GET", "/api/gl/trial-balance?group_id=" + g.id, null, t)).body; console.log(JSON.stringify(gs.rows.slice(0, 4)), JSON.stringify(gs.totals));
  const lid = gs.rows.find(r => r.kind === "ledger" && /ZED/.test(r.name)); console.log(JSON.stringify(lid)); const l = (await j("GET", "/api/gl/ledger?ledger_id=" + lid.id, null, t)).body; console.log(JSON.stringify(l).slice(0, 500));
})();
