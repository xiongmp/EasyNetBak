window.NB = window.NB || {};

(function () {
  "use strict";

  function createWatermark(text) {
    const canvas = document.createElement("canvas");
    canvas.width = 350;
    canvas.height = 250;
    const context = canvas.getContext("2d");
    if (!context) return "";

    context.translate(canvas.width / 2, canvas.height / 2);
    context.rotate(-30 * Math.PI / 180);
    context.font = 'bold 22px system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
    context.fillStyle = "rgba(130, 130, 130, 1)";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(text, 0, 0);
    return canvas.toDataURL("image/png");
  }

  window.NB.ready(function initWatermark() {
    const config = window.NB.readJson("nb-watermark-config", {});
    const text = String(config.text || "").trim();
    if (!text || document.querySelector(".nb-watermark")) return;

    const image = createWatermark(text);
    if (!image) return;

    const watermark = document.createElement("div");
    watermark.className = "nb-watermark";
    watermark.setAttribute("aria-hidden", "true");
    Object.assign(watermark.style, {
      pointerEvents: "none",
      position: "fixed",
      inset: "0",
      width: "100vw",
      height: "100vh",
      zIndex: "9999",
      opacity: "0.15",
      backgroundImage: `url(${image})`,
      backgroundRepeat: "repeat",
    });
    document.body.appendChild(watermark);
  }, { name: "watermark" });
})();
