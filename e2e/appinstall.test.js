// The sign-in card offers "Download eSway App" only where the app can be installed (browser says so, or iPhone / iPad); it disappears once installed.
const { JSDOM } = require("jsdom");
const BASE = "http://127.0.0.1:8010";
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const open = async (ua) => JSDOM.fromURL(BASE + "/erp/login", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, userAgent: ua,
    beforeParse(w) { Object.defineProperty(w.navigator, "userAgent", { value: ua, configurable: true }); w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; } });
  let dom = await open("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"); await sleep(3500);
  let d = dom.window.document;
  check("no install link where the browser cannot install the app", !d.getElementById("loginInstall"));
  const ev = new dom.window.Event("beforeinstallprompt"); ev.prompt = () => { dom.window.__prompted = true; }; ev.userChoice = Promise.resolve({ outcome: "accepted" });
  dom.window.dispatchEvent(ev); await sleep(400);
  const link = d.getElementById("loginInstall");
  check("when the browser says the app is installable the sign-in card shows Download eSway App", !!link && /Download eSway App/.test(link.textContent) && /Android \/ Windows/.test(link.textContent), link && link.textContent);
  link.click(); await sleep(300);
  check("clicking it opens the browser's install prompt", dom.window.__prompted === true);
  await sleep(300);
  check("after installing the link goes away", !d.getElementById("loginInstall"));
  dom = await open("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"); await sleep(3500);
  d = dom.window.document;
  check("on iPhone / iPad the card offers it with the Add to Home Screen hint", !!d.getElementById("loginInstall") && /iPhone \/ iPad/.test(d.getElementById("loginInstall").textContent));
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
