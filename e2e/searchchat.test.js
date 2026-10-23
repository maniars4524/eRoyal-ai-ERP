// Global search finds records across modules and opens them; the chat opens from a header button next to the search box and its button / window can be dragged and keep their place.
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
  const org = await j("POST", "/api/organizations", { code: "SC" + tag.slice(-6).toUpperCase(), name: "Search " + tag, admin_email: `sc${tag}@t.test`, admin_username: "sc" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Sam" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "sc" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const word = "Qx" + tag;
  await j("POST", "/api/customers", { code: word.slice(0, 8).toUpperCase(), name: word + " Exports", roles: ["customer"], data: {} }, token);
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(7000);
  const box = d.getElementById("globalSearch");
  box.value = word; box.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(1500);
  const res = d.getElementById("gsResults");
  check("typing a keyword lists matching records grouped by module", res.classList.contains("on") && /Organizations/.test(res.textContent) && res.textContent.includes(word + " Exports"), res.textContent.slice(0, 200));
  box.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })); await sleep(1800);
  check("Enter opens the first result: the Organizations list filtered to it", d.getElementById("s-org-list").classList.contains("on") && !res.classList.contains("on"), d.querySelector(".scr.on") && d.querySelector(".scr.on").id);
  box.value = "zzzz-nothing-" + tag; box.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(1500);
  check("a keyword with no match says so", /No result/.test(res.textContent), res.textContent.slice(0, 120));
  box.value = ""; res.classList.remove("on");
  // chat
  const hb = d.getElementById("hdrChatBtn");
  check("a chat button sits right after the search box in the header", !!hb && d.querySelector(".hdr-search").nextElementSibling === hb);
  hb.click(); await sleep(300);
  check("the chat button opens the chat window", d.getElementById("chatPanel").style.display === "flex");
  const fire = (el, type, x, y) => { const e = new w.Event(type, { bubbles: true, cancelable: true }); Object.assign(e, { clientX: x, clientY: y, button: 0, pointerId: 1 }); el.dispatchEvent(e); };
  const L = d.getElementById("chatLauncher"); L.setPointerCapture = () => {};
  fire(L, "pointerdown", 30, 700); fire(L, "pointermove", 300, 400); fire(L, "pointerup", 300, 400);
  check("the chat button can be dragged and the new place is remembered", L.style.left !== "" && L.style.top !== "" && /launcher/.test(w.localStorage.getItem("esway_chat_pos") || ""), L.style.left + "," + L.style.top);
  const H = d.getElementById("chatHead"), P = d.getElementById("chatPanel"); H.setPointerCapture = () => {};
  fire(H, "pointerdown", 100, 100); fire(H, "pointermove", 250, 160); fire(H, "pointerup", 250, 160);
  check("the chat window can be dragged by its title bar and keeps its place", P.style.left !== "" && /panel/.test(w.localStorage.getItem("esway_chat_pos") || ""), P.style.left);
  hb.click(); await sleep(200);
  check("the header button closes the chat window again", P.style.display === "none");
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
