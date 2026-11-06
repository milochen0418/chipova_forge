/* Chipova Forge in the browser (from Chipova's ide.js):
 *   - [data-ago="<unix seconds>"] shows "5 min ago", kept current;
 *   - [data-autoscroll] stays scrolled to the bottom while new lines come in, unless you
 *     scrolled up to read;
 *   - a[download] links download: the page's router would otherwise take a same-origin link
 *     (behind re-ddns the backend's downloads are on the page's own host) as a page change;
 *   - window.__forgeReveal(line) shows a line in the editor (a build problem's).
 */
(() => {
  if (window.__forgeLoaded) return;
  window.__forgeLoaded = true;

  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[download]");
    if (a) e.stopImmediatePropagation();
  }, true);

  window.__forgeReveal = (line) => {
    const tryReveal = (n) => {
      const eds = window.monaco && window.monaco.editor && window.monaco.editor.getEditors ? window.monaco.editor.getEditors() : [];
      if (eds.length) {
        const ed = eds[eds.length - 1];
        ed.revealLineInCenter(line);
        ed.setPosition({ lineNumber: line, column: 1 });
        ed.focus();
      } else if (n > 0) {
        setTimeout(() => tryReveal(n - 1), 150);
      }
    };
    tryReveal(20);
  };

  const ago = (ts) => {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (!ts) return "";
    if (s < 45) return "just now";
    const units = [[86400 * 30, "mo"], [86400, "d"], [3600, "h"], [60, "min"]];
    for (const [size, unit] of units) if (s >= size) return `${Math.floor(s / size)} ${unit} ago`;
    return "1 min ago";
  };

  const tick = () => {
    document.querySelectorAll("[data-ago]").forEach((el) => {
      const ts = parseFloat(el.getAttribute("data-ago"));
      const prefix = el.__forgePrefix ?? (el.__forgePrefix = el.textContent.replace(/\s*(just now|\d+ (mo|d|h|min) ago)$/, ""));
      const text = (prefix ? prefix + " " : "") + ago(ts);
      if (el.textContent !== text) el.textContent = text;
      el.title = ts ? new Date(ts * 1000).toLocaleString() : "";
    });
    document.querySelectorAll("[data-autoscroll]").forEach((el) => {
      const atBottom = el.__forgeAtBottom !== false;
      if (atBottom && el.scrollHeight - el.clientHeight - el.scrollTop > 2) el.scrollTop = el.scrollHeight;
      if (!el.__forgeWatch) {
        el.__forgeWatch = true;
        el.addEventListener("scroll", () => {
          el.__forgeAtBottom = el.scrollHeight - el.clientHeight - el.scrollTop < 30;
        });
      }
    });
  };
  setInterval(tick, 500);
  if (document.readyState !== "loading") tick();
  else document.addEventListener("DOMContentLoaded", tick);
})();
