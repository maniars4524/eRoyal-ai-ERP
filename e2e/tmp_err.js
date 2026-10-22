const { JSDOM, VirtualConsole } = require("jsdom");
const BASE = "http://127.0.0.1:8010";
const vc = new VirtualConsole();
vc.on("jsdomError", e => console.log("JSDOM ERROR:", String(e.message || e).slice(0, 300), (e.detail && e.detail.stack || "").split("\n").slice(0, 3).join(" | ")));
vc.on("error", e => console.log("console.error:", String(e).slice(0, 200)));
(async () => {
  const dom = await JSDOM.fromURL(BASE + "/erp/login", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc });
  await new Promise(r => setTimeout(r, 4000));
  const d = dom.window.document;
  console.log("panel:", !!d.querySelector(".login-info-panel"), "brand svg:", !!d.querySelector(".lip-brand svg"), "slides:", d.querySelectorAll(".lip-slide").length, "overlay display:", d.getElementById("loginOverlay") && d.getElementById("loginOverlay").style.display);
  process.exit(0);
})();
