/* =========================================================================
   LCCA-IAS — Shared frontend utilities
   API client, toast notifications, formatters, sidebar behaviour.
   ========================================================================= */

const LCCA = (() => {
  const API_BASE = "/api";

  async function request(method, path, body) {
    const opts = {
      method,
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
    };
    if (body !== undefined) opts.body = JSON.stringify(body);

    const res = await fetch(`${API_BASE}${path}`, opts);

    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("Not authenticated");
    }

    if (!res.ok) {
      let detail = `Request failed (${res.status})`;
      try {
        const data = await res.json();
        if (data.detail) detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      } catch (_) { /* ignore parse errors */ }
      throw new Error(detail);
    }

    if (res.status === 204) return null;
    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/json")) return res.json();
    return res;
  }

  const api = {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
    put: (path, body) => request("PUT", path, body),
    del: (path) => request("DELETE", path),
  };

  function currency(value) {
    const n = Number(value || 0);
    return "N$ " + n.toLocaleString("en-NA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function dateShort(value) {
    if (!value) return "-";
    const d = new Date(value + (typeof value === "string" && value.length === 10 ? "T00:00:00" : ""));
    if (isNaN(d.getTime())) return value;
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  }

  function dateTimeShort(value) {
    if (!value) return "-";
    const d = new Date(value);
    if (isNaN(d.getTime())) return value;
    return d.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function timeAgo(value) {
    if (!value) return "";
    const d = new Date(value);
    const diffMs = Date.now() - d.getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}d ago`;
    return dateShort(value);
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function initials(name) {
    if (!name) return "?";
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  // ---- Toasts -----------------------------------------------------------
  function ensureToastContainer() {
    let el = document.getElementById("lcca-toast-container");
    if (!el) {
      el = document.createElement("div");
      el.id = "lcca-toast-container";
      el.style.position = "fixed";
      el.style.top = "20px";
      el.style.right = "20px";
      el.style.zIndex = "1080";
      el.style.display = "flex";
      el.style.flexDirection = "column";
      el.style.gap = "10px";
      document.body.appendChild(el);
    }
    return el;
  }

  function toast(message, type = "success") {
    const container = ensureToastContainer();
    const colors = {
      success: { text: "#1c8a5e", icon: "bi-check-circle-fill" },
      error: { text: "#c23a3a", icon: "bi-exclamation-circle-fill" },
      info: { text: "#2563a8", icon: "bi-info-circle-fill" },
    };
    const c = colors[type] || colors.info;

    const el = document.createElement("div");
    el.style.background = "#fff";
    el.style.borderLeft = `4px solid ${c.text}`;
    el.style.borderRadius = "10px";
    el.style.boxShadow = "0 8px 24px rgba(10,26,58,0.16)";
    el.style.padding = "13px 16px";
    el.style.minWidth = "260px";
    el.style.maxWidth = "380px";
    el.style.display = "flex";
    el.style.alignItems = "flex-start";
    el.style.gap = "10px";
    el.style.fontSize = "0.88rem";
    el.style.opacity = "0";
    el.style.transform = "translateX(20px)";
    el.style.transition = "opacity .2s, transform .2s";
    el.innerHTML = `
      <i class="bi ${c.icon}" style="color:${c.text}; font-size:1.05rem; margin-top:1px;"></i>
      <div style="color:#1c2333; line-height:1.4;">${escapeHtml(message)}</div>
    `;
    container.appendChild(el);
    requestAnimationFrame(() => {
      el.style.opacity = "1";
      el.style.transform = "translateX(0)";
    });
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateX(20px)";
      setTimeout(() => el.remove(), 200);
    }, 4200);
  }

  // ---- Sidebar (mobile toggle) --------------------------------------------
  function initSidebar() {
    const sidebar = document.querySelector(".sidebar");
    const overlay = document.querySelector(".sidebar-overlay");
    const toggleBtns = document.querySelectorAll(".mobile-toggle");
    if (!sidebar) return;

    function open() {
      sidebar.classList.add("open");
      overlay?.classList.add("show");
    }
    function close() {
      sidebar.classList.remove("open");
      overlay?.classList.remove("show");
    }
    toggleBtns.forEach((btn) => btn.addEventListener("click", open));
    overlay?.addEventListener("click", close);
  }

  // ---- Logout ---------------------------------------------------------------
  function initLogout() {
    document.querySelectorAll(".logout-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await api.post("/auth/logout");
        } catch (_) { /* ignore */ }
        window.location.href = "/login";
      });
    });
  }

  function debounce(fn, wait = 300) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), wait);
    };
  }

  document.addEventListener("DOMContentLoaded", () => {
    initSidebar();
    initLogout();
  });

  return { api, currency, dateShort, dateTimeShort, timeAgo, escapeHtml, initials, toast, debounce };
})();
