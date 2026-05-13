(() => {
  const BACKEND_URL = (window.BACKEND_URL || "").replace(/\/$/, "");

  // ─── IndexedDB layer for persisting manual entries ───────────────────────
  const DB_NAME = "catalogue-creator-db";
  const DB_VERSION = 1;
  const STORE = "manual_entries";

  function openDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: "key" });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  async function idbGet(key) {
    const db = await openDB();
    return new Promise((res, rej) => {
      const tx = db.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).get(key);
      req.onsuccess = () => res(req.result || null);
      req.onerror = () => rej(req.error);
    });
  }
  async function idbPut(entry) {
    const db = await openDB();
    return new Promise((res, rej) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put(entry);
      tx.oncomplete = () => res();
      tx.onerror = () => rej(tx.error);
    });
  }
  async function idbDelete(key) {
    const db = await openDB();
    return new Promise((res, rej) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).delete(key);
      tx.oncomplete = () => res();
      tx.onerror = () => rej(tx.error);
    });
  }
  async function idbClearAll() {
    const db = await openDB();
    return new Promise((res, rej) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).clear();
      tx.oncomplete = () => res();
      tx.onerror = () => rej(tx.error);
    });
  }

  // ─── DOM refs ───────────────────────────────────────────────────────────
  const fileInput = document.getElementById("file");
  const drop = document.getElementById("drop");
  const dropTitle = document.getElementById("drop-title");
  const dropSub = document.getElementById("drop-sub");

  const priceInput = document.getElementById("price-file");
  const dropPrice = document.getElementById("drop-price");
  const dropPriceTitle = document.getElementById("drop-price-title");
  const dropPriceSub = document.getElementById("drop-price-sub");

  const skipInput = document.getElementById("skip_pages");
  const matchInput = document.getElementById("match_threshold");

  const submit = document.getElementById("submit");
  const form = document.getElementById("form");
  const status = document.getElementById("status");

  const manualSection = document.getElementById("manual-section");
  const manualList = document.getElementById("manual-list");
  const manualSummary = document.getElementById("manual-summary");
  const manualRefresh = document.getElementById("manual-refresh");
  const manualClearAll = document.getElementById("manual-clear-all");
  const manualTpl = document.getElementById("manual-card-tpl");

  let currentFile = null;
  let currentPriceFile = null;
  let manualState = []; // {key, model, dealer_price, enabled, file, features, mrp, card, savedBadge}

  // ─── Helpers ────────────────────────────────────────────────────────────
  function setFile(f) {
    if (!f) return;
    if (!/\.pdf$/i.test(f.name)) return showStatus("Please select a PDF file.", "error");
    currentFile = f;
    dropTitle.textContent = f.name;
    dropSub.textContent = `${(f.size / 1024 / 1024).toFixed(2)} MB · click to change`;
    submit.disabled = false;
    hideStatus();
    maybePreview();
  }
  function setPriceFile(f) {
    if (!f) return;
    if (!/\.(xlsx|xls)$/i.test(f.name)) return showStatus("Price list must be a .xlsx file.", "error");
    currentPriceFile = f;
    dropPriceTitle.textContent = f.name;
    dropPriceSub.textContent = `${(f.size / 1024).toFixed(0)} KB · click to change`;
    hideStatus();
    maybePreview();
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
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("dragover"); });
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
  manualRefresh.addEventListener("click", maybePreview);
  manualClearAll.addEventListener("click", async () => {
    if (!confirm("Wipe all saved manual entries from this browser?")) return;
    await idbClearAll();
    if (currentFile && currentPriceFile) maybePreview();
    else showStatus("All saved manual entries cleared.", "ok");
  });

  // ─── Preview / detection ────────────────────────────────────────────────
  async function maybePreview() {
    if (!currentFile || !currentPriceFile || !BACKEND_URL) return;
    manualSection.classList.remove("hidden");
    manualSummary.textContent = "Detecting…";
    manualList.innerHTML = "";

    const fd = new FormData();
    fd.append("file", currentFile);
    fd.append("price_list", currentPriceFile);
    fd.append("skip_pages", skipInput.value);
    fd.append("match_threshold", matchInput.value);

    try {
      const res = await fetch(`${BACKEND_URL}/preview`, { method: "POST", body: fd });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { const j = await res.json(); if (j.detail) detail = j.detail; } catch {}
        throw new Error(detail);
      }
      const data = await res.json();
      await renderManualList(data);
    } catch (err) {
      manualSummary.innerHTML = `<span class="stat-warn">Detection failed: ${err.message}</span>`;
    }
  }

  // Saving is debounced per-key so rapid typing doesn't hammer IDB
  const saveTimers = new Map();
  function scheduleSave(state) {
    clearTimeout(saveTimers.get(state.key));
    saveTimers.set(state.key, setTimeout(() => persistEntry(state), 250));
  }
  async function persistEntry(state) {
    const hasAny = state.enabled && (state.file || (state.features || "").trim() || state.mrp);
    if (!hasAny) {
      await idbDelete(state.key).catch(() => {});
      state.savedBadge.classList.add("hidden");
      return;
    }
    const entry = {
      key: state.key,
      model: state.model,
      features: state.features || "",
      mrp: state.mrp || 0,
      image: state.file || null,
      imageName: state.file ? state.file.name : null,
      imageType: state.file ? state.file.type : null,
      savedAt: Date.now(),
    };
    try {
      await idbPut(entry);
      state.savedBadge.classList.remove("hidden");
    } catch (e) {
      console.error("idbPut failed", e);
    }
  }

  async function renderManualList(data) {
    manualState = [];
    manualList.innerHTML = "";
    const missing = data.missing_models || [];
    manualSummary.textContent =
      `${data.product_count} products in PDF · ${data.matched_count} matched · ${missing.length} missing — fill below to include them as proper cards.`;

    for (const m of missing) {
      const key = m.normalized || m.model.toLowerCase();
      const saved = await idbGet(key).catch(() => null);

      const node = manualTpl.content.firstElementChild.cloneNode(true);
      node.querySelector(".manual-name").textContent = m.model;
      node.querySelector(".manual-dp").textContent = ` · DP. ₹${Math.round(m.dealer_price).toLocaleString("en-IN")}`;
      const savedBadge = node.querySelector(".manual-saved");

      const enabledChk = node.querySelector(".manual-enabled");
      const imgInput = node.querySelector(".manual-image-input");
      const imgLabel = node.querySelector(".manual-image");
      const imgInner = node.querySelector(".manual-image-inner");
      const featsTa = node.querySelector(".manual-features");
      const mrpInp = node.querySelector(".manual-mrp");
      const clearBtn = node.querySelector(".manual-clear");

      const state = {
        key,
        model: m.model,
        dealer_price: m.dealer_price,
        normalized: m.normalized,
        enabled: false,
        file: null,
        features: "",
        mrp: 0,
        card: node,
        savedBadge,
      };

      const setEnabled = (v) => {
        state.enabled = v;
        node.classList.toggle("is-enabled", v);
        enabledChk.checked = v;
      };

      // Pre-fill from IndexedDB
      if (saved) {
        if (saved.image instanceof Blob) {
          state.file = new File([saved.image], saved.imageName || "image",
                                { type: saved.imageType || saved.image.type });
          imgInner.querySelector("strong").textContent = saved.imageName || "saved image";
          imgInner.querySelector("span").textContent = `${(saved.image.size / 1024).toFixed(0)} KB · click to replace`;
        }
        if (saved.features) {
          state.features = saved.features;
          featsTa.value = saved.features;
        }
        if (saved.mrp) {
          state.mrp = saved.mrp;
          mrpInp.value = saved.mrp;
        }
        setEnabled(true);
        savedBadge.classList.remove("hidden");
      }

      enabledChk.addEventListener("change", () => {
        setEnabled(enabledChk.checked);
        scheduleSave(state);
      });

      imgInput.addEventListener("change", (e) => {
        const f = e.target.files[0];
        if (f) {
          state.file = f;
          imgInner.querySelector("strong").textContent = f.name;
          imgInner.querySelector("span").textContent = `${(f.size / 1024).toFixed(0)} KB · click to replace`;
          if (!state.enabled) setEnabled(true);
          scheduleSave(state);
        }
      });
      imgLabel.addEventListener("dragover", (e) => { e.preventDefault(); imgLabel.classList.add("dragover"); });
      imgLabel.addEventListener("dragleave", () => imgLabel.classList.remove("dragover"));
      imgLabel.addEventListener("drop", (e) => {
        e.preventDefault(); imgLabel.classList.remove("dragover");
        const f = e.dataTransfer.files[0];
        if (f) { imgInput.files = e.dataTransfer.files; imgInput.dispatchEvent(new Event("change")); }
      });

      featsTa.addEventListener("input", () => {
        state.features = featsTa.value;
        if (state.features.trim() && !state.enabled) setEnabled(true);
        scheduleSave(state);
      });
      mrpInp.addEventListener("input", () => {
        state.mrp = parseFloat(mrpInp.value) || 0;
        if (state.mrp && !state.enabled) setEnabled(true);
        scheduleSave(state);
      });

      clearBtn.addEventListener("click", async () => {
        await idbDelete(state.key).catch(() => {});
        state.file = null;
        state.features = "";
        state.mrp = 0;
        featsTa.value = "";
        mrpInp.value = "";
        imgInput.value = "";
        imgInner.querySelector("strong").textContent = "Drop image";
        imgInner.querySelector("span").textContent = "or click";
        setEnabled(false);
        savedBadge.classList.add("hidden");
      });

      manualList.appendChild(node);
      manualState.push(state);
    }

    if (!missing.length) {
      manualList.innerHTML = `<div class="manual-empty">No missing models — every price-list entry matched a product in the PDF.</div>`;
    }
  }

  // ─── Submit ─────────────────────────────────────────────────────────────
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!currentFile) return;
    if (!BACKEND_URL) return showStatus("Backend URL not configured. Edit config.js.", "error");

    const fd = new FormData();
    fd.append("file", currentFile);
    if (currentPriceFile) fd.append("price_list", currentPriceFile);
    fd.append("cols", document.getElementById("cols").value);
    fd.append("rows_per_page", document.getElementById("rows_per_page").value);
    fd.append("scale", document.getElementById("scale").value);
    fd.append("skip_pages", skipInput.value);
    fd.append("divider", document.getElementById("divider").value);
    fd.append("match_threshold", matchInput.value);

    const entries = [];
    let imgCounter = 0;
    for (const s of manualState) {
      const hasAny = s.enabled && (s.file || (s.features || "").trim() || s.mrp);
      if (!hasAny) continue;
      const entry = { model: s.model, features: s.features, mrp: s.mrp };
      if (s.file) {
        entry.image_index = imgCounter;
        fd.append("manual_images", s.file);
        imgCounter += 1;
      }
      entries.push(entry);
    }
    if (entries.length) fd.append("manual_entries", JSON.stringify(entries));

    submit.disabled = true;
    showStatus(`Generating${entries.length ? ` · ${entries.length} manual cells` : ""}… this can take 10–60s.`, "loading");

    try {
      const res = await fetch(`${BACKEND_URL}/generate`, { method: "POST", body: fd });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { const j = await res.json(); if (j.detail) detail = j.detail; } catch {}
        throw new Error(detail);
      }
      const count = res.headers.get("X-Product-Count") || "?";
      const matched = res.headers.get("X-Matched-Count") || "0";
      const missing = res.headers.get("X-Missing-Models-Count") || "0";
      const manual = res.headers.get("X-Manual-Cells-Count") || "0";

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const stem = currentFile.name.replace(/\.pdf$/i, "");
      const a = document.createElement("a");
      a.href = url; a.download = `${stem}_catalogue.pdf`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);

      let summary = `Done · ${count} products from PDF`;
      if (currentPriceFile) {
        summary += ` · <span class="stat-ok">${matched} priced</span>`;
        if (parseInt(manual, 10) > 0) summary += ` · <span class="stat-ok">${manual} manual cards</span>`;
        if (parseInt(missing, 10) > 0) summary += ` · ${missing} listed on Additional page`;
      }
      showStatus(summary, "ok");
    } catch (err) {
      showStatus(`Failed: ${err.message}`, "error");
    } finally {
      submit.disabled = false;
    }
  });
})();
