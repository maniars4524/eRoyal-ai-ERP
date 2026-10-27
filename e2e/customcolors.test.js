// Your own colours: three choices (Background, Titles, Accent) with a colour picker; applied at once, kept on the account, kept readable, removable.
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
  const org = await j("POST", "/api/organizations", { code: "CC" + tag.slice(-6).toUpperCase(), name: "Colours " + tag, admin_email: `cc${tag}@t.test`, admin_username: "cc" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Cleo" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "cc" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const open = async () => { const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } }); await sleep(6500); return dom.window; };
  let w = await open(); let d = w.document;
  w.go("themes"); await sleep(800);
  check("the Themes screen has three own-colour choices: Background, Titles & header, Accent", ["bg", "title", "accent"].every(k => d.querySelector(`[data-cc-pick="${k}"]`)));
  const pick = async (key, hex) => { d.querySelector(`button[data-cc-pick="${key}"]`).click(); await sleep(300);
    const h = d.getElementById("ccHex"); h.value = hex; h.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(100); d.getElementById("ccOk").click(); await sleep(300); };
  check("the picker offers the colour spectrum, hex, red / green / blue, basic colours and custom colour slots", (d.querySelector(`button[data-cc-pick="bg"]`).click(), await sleep(300), !!d.getElementById("ccNative") && !!d.getElementById("ccR") && !!d.getElementById("ccB") && d.querySelectorAll("#modalBody [data-cc-set]").length >= 48 && d.querySelectorAll("#ccCustom > *").length === 12));
  w.closeModal();
  await pick("bg", "#FFF4E0"); await pick("title", "#7A1F3D"); await pick("accent", "#0E8A5F");
  const cs = () => { const st = w.getComputedStyle(d.documentElement); return { g0: st.getPropertyValue("--g0").trim().toUpperCase(), n: st.getPropertyValue("--n").trim().toUpperCase(), bl: st.getPropertyValue("--bl").trim().toUpperCase() }; };
  const css = d.getElementById("esway-custom-colors") ? d.getElementById("esway-custom-colors").textContent : "";
  check("choosing colours changes the application's colours at once", /--g0:#FFF4E0/i.test(css) && /--n:#7A1F3D/i.test(css) && /--bl:#0E8A5F/i.test(css), css.slice(0, 200));
  d.getElementById("ccSave").click(); await sleep(1500);
  const me = (await j("GET", "/api/auth/me", null, token)).body;
  check("Apply and save keeps the three colours on the account", me.custom_colors && me.custom_colors.bg === "#FFF4E0" && me.custom_colors.accent === "#0E8A5F", JSON.stringify(me.custom_colors));
  check("a bad colour is refused by the server", (await j("PUT", "/api/auth/preferences", { custom_colors: { bg: "red", title: "#000000", accent: "#111111" } }, token)).status === 422);
  w.close();
  w = await open(); d = w.document;
  check("the colours come back after signing in again (any device)", /--n:#7A1F3D/i.test((d.getElementById("esway-custom-colors") || {}).textContent || ""));
  // readability: a very light title colour is darkened so the white header text can be read
  const fixed = w.EswayColors.fix({ bg: "#101010", title: "#FFFFFF", accent: "#FFEE00" });
  check("unreadable choices are corrected (white header text, dark page text)", w.EswayColors.contrast(fixed.colors.title, "#FFFFFF") >= 4.5 && w.EswayColors.contrast(fixed.colors.bg, "#1E293B") >= 8 && fixed.notes.length === 2, JSON.stringify(fixed));
  w.go("themes"); await sleep(600);
  d.getElementById("ccReset").click(); await sleep(1200);
  const me2 = (await j("GET", "/api/auth/me", null, token)).body;
  check("Back to the theme colours removes them everywhere", !d.getElementById("esway-custom-colors") && !me2.custom_colors, JSON.stringify(me2.custom_colors));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
