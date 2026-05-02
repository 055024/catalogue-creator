(() => {
  const BACKEND_URL = (window.BACKEND_URL || "").replace(/\/$/, "");

  const fileInput = document.getElementById("file");
  const drop = document.getElementById("drop");
  const dropTitle = document.getElementById("drop-title");
  const dropSub = document.getElementById("drop-sub");

  const priceInput = document.getElementById("price-file");
  const dropPrice = document.getElementById("drop-price");
  const dropPriceTitle = document.getElementById("drop-price-title");
  const dropPriceSub = document.getElementById("drop-price-sub");

  const submit = document.getElementById("submit");
  const form = document.getElementById("form");
  const status = document.getElementById("status");

  let currentFile = null;
  let currentPriceFile = null;

  function setFile(f) {
    if (!f) return;
    if (!/\.pdf$/i.test(f.name)) {
      showStatus("Please select a PDF file.", "error");
      return;
    }
    currentFile = f;
    dropTitle.textContent = f.name;
    dropSub.textContent = `${(f.size / 1024 / 1024).toFixed(2)} MB · click to change`;
    submit.disabled = false;
    hideStatus();
  }

  function setPriceFile(f) {
    if (!f) return;
    if (!/\.(xlsx|xls)$/i.test(f.name)) {
      showStatus("Price list must be a .xlsx file.", "error");
      return;
    }
    currentPriceFile = f;
    dropPriceTitle.textContent = f.name;
    dropPriceSub.textContent = `${(f.size / 1024).toFixed(0)} KB · click to change`;
    hideStatus();
  }

  function showStatus(html, kind = "loading") {
    status.innerHTML = html;
    status.className = `status ${kind}`;
  }

  function hideStatus() {
    status.className = "status hidden";
    status.innerHTML = "";
  }

  function bindDrop(zone, input, setter) {
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
      setter(e.dataTransfer.files[0]);
    });
    input.addEventListener("change", (e) => setter(e.target.files[0]));
  }

  bindDrop(drop, fileInput, setFile);
  bindDrop(dropPrice, priceInput, setPriceFile);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!currentFile) return;
    if (!BACKEND_URL) {
      showStatus("Backend URL not configured. Edit config.js.", "error");
      return;
    }

    const fd = new FormData();
    fd.append("file", currentFile);
    if (currentPriceFile) fd.append("price_list", currentPriceFile);
    fd.append("cols", document.getElementById("cols").value);
    fd.append("rows_per_page", document.getElementById("rows_per_page").value);
    fd.append("scale", document.getElementById("scale").value);
    fd.append("skip_pages", document.getElementById("skip_pages").value);
    fd.append("divider", document.getElementById("divider").value);
    fd.append("match_threshold", document.getElementById("match_threshold").value);

    submit.disabled = true;
    showStatus("Generating… this can take 10–60s for large PDFs.", "loading");

    try {
      const res = await fetch(`${BACKEND_URL}/generate`, { method: "POST", body: fd });

      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { const j = await res.json(); if (j.detail) detail = j.detail; } catch {}
        throw new Error(detail);
      }

      const count = res.headers.get("X-Product-Count") || "?";
      const matched = res.headers.get("X-Matched-Count") || "0";
      const unmatched = res.headers.get("X-Unmatched-Count") || "0";
      const missing = res.headers.get("X-Missing-Models-Count") || "0";

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const stem = currentFile.name.replace(/\.pdf$/i, "");
      const a = document.createElement("a");
      a.href = url;
      a.download = `${stem}_catalogue.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);

      let summary = `Done · ${count} products extracted.`;
      if (currentPriceFile) {
        summary += `<br><span class="stat-ok">${matched} matched with dealer prices</span>`;
        if (parseInt(unmatched, 10) > 0) summary += ` · <span class="stat-warn">${unmatched} unmatched</span>`;
        if (parseInt(missing, 10) > 0) summary += `<br>${missing} models from price list added as "Additional Models" page`;
      }
      showStatus(summary, "ok");
    } catch (err) {
      showStatus(`Failed: ${err.message}`, "error");
    } finally {
      submit.disabled = false;
    }
  });
})();
