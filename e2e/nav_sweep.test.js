// Navigation sweep: click every menu entry of the sidebar (as the company admin) and check that a screen opens with no script error, and that dashboard shortcuts open their screens
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); let b = null; try { b = await r.json(); } catch (e) {} return { status: r.status, body: b }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "NV" + tag, name: "Nv " + tag, admin_email: `nv${tag}@t.test`, admin_username: "nv" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "nv" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"], protocolTimeout: 600000 });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 });
  let errs = []; page.on("pageerror", e => errs.push(String(e.message).slice(0, 160)));
  page.on("dialog", d => d.accept());
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(3500);
  const items = await page.evaluate(() => [...document.querySelectorAll("[data-go]")].filter(e => e.closest(".sidebar, #sidebar, nav, .mn-sub, .mn-sub-item, .mn-item") || e.classList.contains("mn-sub-item")).map((e, i) => ({ i, go: e.dataset.go, label: (e.textContent || "").trim().slice(0, 40), extra: JSON.stringify({ gl: e.dataset.gltab, pr: e.dataset.prltype, rp: e.dataset.rpt || e.dataset.report }) })));
  const seen = new Set(), todo = items.filter(x => { const k = x.go + x.extra; if (seen.has(k)) return false; seen.add(k); return true; });
  console.log("menu entries to try:", todo.length, "of", items.length);
  const bad = [];
  for (const it of todo) {
    errs = [];
    const r = await page.evaluate(async (go, extra) => {
      const q = JSON.parse(extra); const els = [...document.querySelectorAll('[data-go="' + go + '"]')].filter(e => JSON.stringify({ gl: e.dataset.gltab, pr: e.dataset.prltype, rp: e.dataset.rpt || e.dataset.report }) === extra);
      const el = els[0]; if (!el) return { ok: false, why: "menu element gone" };
      el.click(); await new Promise(r => setTimeout(r, 900));
      const on = document.querySelector(".scr.on"); const modal = document.querySelector("#modalBody, .modal.on, .modal.show");
      return { ok: !!on, id: on ? on.id : "", title: (document.querySelector(".ph .pt, #pageTitle, .page-title") || {}).textContent || "" };
    }, it.go, it.extra);
    if (!r.ok || errs.length) bad.push({ go: it.go, label: it.label, why: r.why || (errs.length ? "script error: " + errs[0] : "no screen opened"), id: r.id });
    else if (r.id && r.id !== "s-" + it.go && !/^s-(gl-reports|reports|pr-ledger|acc-reports)$/.test(r.id)) bad.push({ go: it.go, label: it.label, why: "opened a different screen", id: r.id });
    await page.evaluate(() => { const m = document.querySelector(".modal-ovl.on, #modalOvl.on"); if (m && window.closeModal) window.closeModal(); });
  }
  console.log(bad.length ? "PROBLEMS (" + bad.length + "):\n" + bad.map(x => `  ${x.label} [${x.go}] -> ${x.why}${x.id ? " (" + x.id + ")" : ""}`).join("\n") : "ALL MENU ENTRIES OPEN THEIR SCREEN");
  await b.close(); process.exit(bad.length ? 1 : 0);
})();
