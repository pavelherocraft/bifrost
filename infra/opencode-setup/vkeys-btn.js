(function(){
  if(window.__ocVkInj) return;
  window.__ocVkInj = 1;

  var DEBUG = /[?&]ocdebug=1\b/.test(location.search);
  // When true, the floating debug badge is rendered on every VK page even
  // without ?ocdebug=1. Useful when developing / debugging without DevTools.
  // Set to false (or just leave it true and remove via ?ocdebug=0) for prod.
  var SHOW_BADGE_ON_VK = false;
  var SCAN_THROTTLE_MS = 750;

  function log(){
    if(DEBUG && window.console) console.log.apply(console, ["[oc-vk-inject]"].concat([].slice.call(arguments)));
  }
  log("init");

  var UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
  // Match LiteLLM virtual key tokens: sk-XXX or hkg-XXX, base64-url chars,
  // length >= 20 to avoid false positives on visible prefixes like "sk-" alone.
  var VK_RE = /\b(sk-|hkg-)[A-Za-z0-9_-]{20,}/g;
  // LiteLLM Admin UI often MASKS the key in the row: "sk-MINIM...W4yI" or
  // "sk-MINIM\u2026W4yI". We can't recover the full token from this display,
  // so it requires an API round-trip. Detect this case separately so the
  // badge can show we *see* a masked token but don't have the full string.
  var VK_MASKED_RE = /\bsk-[A-Za-z0-9_-]{2,12}(?:\u2026|\.\.\.|[\.\u00B7\u2022\u2014]|\s*[A-Za-z0-9_-]{2,6})\b/;

  function currentIsVkPage(){
    // LiteLLM Admin UI uses /litellm/ui/api-keys/ as the virtual keys page
    // (TanStack path), but /?page=virtual-keys is also valid for older
    // versions. Match any of:
    //   /api-keys/                  — LiteLLM Admin current path
    //   /virtual-keys               — TanStack old / future SPA path
    //   ?page=virtual-keys          — legacy query-string routing
    //   ?page=virtual_keys          — underscored variant
    //   ?page=api-keys              — alias some installations use
    return /\/api-keys\/?(\?|$|#)/.test(location.pathname) ||
      /\/virtual-keys/.test(location.pathname) ||
      /[?&]page=virtual[_-]keys\b/.test(location.search) ||
      /[?&]page=api[_-]keys\b/.test(location.search);
  }

  function findTokenInRow(row){
    // 1) data-row-key (TanStack Table pattern; LiteLLM uses it on rows).
    var drk = row.getAttribute && row.getAttribute("data-row-key");
    if(drk && /^(sk-|hkg-)/.test(drk)) return drk;
    // 2) data-* attributes (data-key, data-id, data-row-id, data-token, data-testid).
    var attrs = row.attributes;
    if(attrs){
      for(var i = 0; i < attrs.length; i++){
        var n = attrs[i].name;
        if(n === "data-row-key" || n === "data-key" || n === "data-token" ||
           n === "data-id" || n === "data-row-id"){
          var v = attrs[i].value;
          if(v && /^(sk-|hkg-)/.test(v)) return v;
        }
      }
    }
    // 3) Visible <code> / <span> elements holding the token. Common in antd
    //    tables where a long string is rendered inside a monospace tag.
    var nodes = row.querySelectorAll("code, [data-testid*='key' i], [data-testid*='token' i]");
    for(var k = 0; k < nodes.length; k++){
      var t = (nodes[k].textContent || "").match(VK_RE);
      if(t && t.length) return t[0];
    }
    // 4) Plain text fallback — search the whole row, take the FIRST token.
    var txt = row.textContent || "";
    var m = txt.match(VK_RE);
    if(m && m.length) return m[0];
    return null;
  }

  // Find the SHA-256 hash that LiteLLM uses as a stable identifier for the
  // row's key. LiteLLM Admin puts it in data-row-key (64-hex), or copies
  // it onto the system clipboard via the row's copy icon. Returns null
  // if no hash can be found.
  function findKeyHashInRow(row){
    // 1) data-row-key — by far the most common case in LiteLLM Admin.
    var drk = row.getAttribute && row.getAttribute("data-row-key");
    if(drk && /^[0-9a-f]{64}$/i.test(drk)) return drk;
    // 2) Check descendant data-* attrs (LiteLLM wraps in <td data-row-key>).
    if(row.querySelectorAll){
      var inner = row.querySelectorAll("[data-row-key]");
      for(var i = 0; i < inner.length; i++){
        var v = inner[i].getAttribute("data-row-key");
        if(v && /^[0-9a-f]{64}$/i.test(v)) return v;
      }
    }
    // 3) As a last resort, look for a 64-hex string in row text.
    var t = (row.textContent || "").match(/[0-9a-f]{64}/i);
    return t ? t[0] : null;
  }

  function findUserIdInRow(row){
    // Many VK tables display the owning user's UUID as a tag/tooltip in the
    // user_id column. Best-effort regex on visible text.
    var t = row.textContent || "";
    var m = t.match(UUID_RE);
    return m ? m[0] : null;
  }

  function pickActionCell(row){
    if(!row || !row.children || row.children.length === 0) return null;
    var cells = row.children;
    for(var i = cells.length - 1; i >= 0; i--){
      var c = cells[i];
      if(c.querySelector && c.querySelector("a, button, [role='button']")) return c;
    }
    return cells[cells.length - 1];
  }

  function addRowButton(tr, info){
    // Validate VK format — skip rows where extraction failed.
    if(!info.vk || !/^(sk-|hkg-)/.test(info.vk)) return;
    // ALWAYS strip existing button first. Next.js + TanStack pagination
    // reuses <tr> elements: old buttons pointing at the wrong VK must not
    // survive a data swap (same pattern as users-btn.js).
    var existing = tr.querySelectorAll("[data-oc-vk-row-btn]");
    for(var e = 0; e < existing.length; e++){
      if(existing[e].parentNode) existing[e].parentNode.removeChild(existing[e]);
    }
    var cell = pickActionCell(tr);
    if(!cell) return;
    var a = document.createElement("a");
    a.setAttribute("data-oc-vk-row-btn", "1");
    a.setAttribute("data-oc-vk-row-token", info.vk);
    a.href = "/setup-opencode/?vk=" + encodeURIComponent(info.vk);
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "\uD83E\uDDF0 OpenCode";
    a.title = "OpenCode setup for this virtual key\nvk: " + info.vk.substring(0, 12) + "\u2026"
      + (info.uid ? "\nuser: " + info.uid : "");
    a.style.cssText = "display:inline-flex;align-items:center;padding:4px 10px;background:#4f46e5;color:#fff;border-radius:4px;font-size:12px;font-weight:600;text-decoration:none;cursor:pointer;white-space:nowrap;margin-left:6px;";
    cell.appendChild(a);
  }

  // Add an OpenCode button that uses ?vkh=<key-hash> instead of the raw
  // token. The hash is stable per VK (LiteLLM stores it as the table key)
  // and the opencode-setup API accepts it on /key/info — so this works
  // even when the actual sk-/hkg- token is masked or unavailable to JS.
  function addHashButton(tr, info){
    if(!info.vkh || !/^[0-9a-f]{64}$/i.test(info.vkh)) return;
    // Strip any existing button to avoid duplicates across re-renders.
    var existing = tr.querySelectorAll("[data-oc-vk-row-btn]");
    for(var e = 0; e < existing.length; e++){
      if(existing[e].parentNode) existing[e].parentNode.removeChild(existing[e]);
    }
    var cell = pickActionCell(tr);
    if(!cell) return;
    var a = document.createElement("a");
    a.setAttribute("data-oc-vk-row-btn", "1");
    a.setAttribute("data-oc-vk-row-vkh", info.vkh);
    a.href = "/setup-opencode/?vkh=" + encodeURIComponent(info.vkh);
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "\uD83E\uDDF0 OpenCode";
    a.title = "OpenCode setup for this virtual key\nhash: " + info.vkh.substring(0, 12) + "\u2026"
      + (info.uid ? "\nuser: " + info.uid : "");
    a.style.cssText = "display:inline-flex;align-items:center;padding:4px 10px;background:#4f46e5;color:#fff;border-radius:4px;font-size:12px;font-weight:600;text-decoration:none;cursor:pointer;white-space:nowrap;margin-left:6px;";
    cell.appendChild(a);
  }

  // Add a "Copy & Open" button to every body row that has a copy-style icon
  // (LiteLLM Admin uses a clipboard-copy SVG per row). On click we intercept
  // the copy action to capture the FULL token, then open /setup-opencode/?vk=
  // This is needed because LiteLLM masks the token in the cell text.
  function addCopyAndOpenButton(tr){
    if(tr.querySelector("[data-oc-vk-copy-btn]")) return;
    // Find a copy icon in the row. Common selectors: button with svg.iconify,
    // a button that copies text, etc.
    var copyBtn = tr.querySelector(
      "button[aria-label*='Copy' i], button[title*='Copy' i], " +
      "button svg[class*='copy' i], button[class*='copy' i], " +
      "button[data-testid*='copy' i], button[aria-label='Copy to clipboard']"
    );
    if(!copyBtn){
      // Fall back: look for ANY button in the row that could be a copy.
      var btns = tr.querySelectorAll("button");
      for(var b = 0; b < btns.length; b++){
        var svg = btns[b].querySelector("svg");
        if(svg && (svg.className.baseVal || svg.getAttribute("class") || "").match(/copy/i)){
          copyBtn = btns[b]; break;
        }
      }
    }
    var cell = pickActionCell(tr);
    if(!cell) return;
    var a = document.createElement("a");
    a.setAttribute("data-oc-vk-copy-btn", "1");
    a.href = "#";
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "\uD83D\uDCCB Copy VK";
    a.title = "Copy virtual key from row and open OpenCode Setup";
    a.style.cssText = "display:inline-flex;align-items:center;padding:4px 10px;background:#0e7490;color:#fff;border-radius:4px;font-size:12px;font-weight:600;text-decoration:none;cursor:pointer;white-space:nowrap;margin-left:6px;";

    a.addEventListener("click", function(ev){
      ev.preventDefault();
      ev.stopPropagation();
      // Hook navigator.clipboard.writeText ONCE — capture the value the row
      // is about to put on the clipboard, then redirect.
      var captured = null;
      var origWrite = navigator.clipboard && navigator.clipboard.writeText;
      if(navigator.clipboard && origWrite){
        navigator.clipboard.writeText = function(t){
          captured = t;
          // Forward to the real implementation; ignore its Promise so our
          // hook works regardless of whether the real one resolves.
          try { return origWrite.call(this, t); } catch(e){ return Promise.resolve(); }
        };
      }
      // Also listen for legacy 'copy' events (some LiteLLM versions use
      // execCommand('copy') which fires a 'copy' event).
      function onCopy(e){
        if(e.clipboardData){
          captured = e.clipboardData.getData("text/plain") || captured;
        }
      }
      document.addEventListener("copy", onCopy, true);

      function cleanup(){
        document.removeEventListener("copy", onCopy, true);
        if(navigator.clipboard && origWrite) navigator.clipboard.writeText = origWrite;
      }
      function openIfCaptured(){
        if(captured && /^(sk-|hkg-)/.test(captured)){
          cleanup();
          window.open("/setup-opencode/?vk=" + encodeURIComponent(captured), "_blank", "noopener");
          return true;
        }
        return false;
      }

      // Trigger the row's copy button (if found) — this fires the clipboard
      // write we'll intercept above.
      if(copyBtn){
        try { copyBtn.click(); } catch(e){}
      } else {
        // Fallback: select cell text and exec copy
        try {
          var sel = window.getSelection();
          var range = document.createRange();
          range.selectNodeContents(tr);
          sel.removeAllRanges();
          sel.addRange(range);
          document.execCommand("copy");
          sel.removeAllRanges();
        } catch(e){}
      }

      // Poll for the captured value for up to 1.5s (clipboard.writeText
      // returns a Promise that resolves AFTER our hook runs, so we can't
      // rely on a single sync check). Then fall back to reading the
      // actual clipboard via navigator.clipboard.readText as a last resort.
      var polls = 0;
      var poller = setInterval(function(){
        polls++;
        if(openIfCaptured()){ clearInterval(poller); return; }
        if(polls >= 15){
          // 15 * 100ms = 1.5s elapsed; try reading clipboard directly.
          clearInterval(poller);
          if(navigator.clipboard && navigator.clipboard.readText){
            navigator.clipboard.readText().then(function(txt){
              if(txt && /^(sk-|hkg-)/.test(txt)){
                window.open("/setup-opencode/?vk=" + encodeURIComponent(txt), "_blank", "noopener");
              }
              cleanup();
            }).catch(function(){ cleanup(); });
          } else {
            cleanup();
          }
        }
      }, 100);
    });
    cell.appendChild(a);
  }

  var lastScanAt = 0;
  var scanPending = false;
  var lastUrl = null;
  var scanDiagLogged = false;
  var idleHandle = 0;
  var idle = window.requestIdleCallback || function(cb){
    return setTimeout(function(){ cb({didTimeout:false, timeRemaining:function(){return 16;}}); }, 0);
  };

  function scan(){
    try {
      if(typeof document !== "undefined" && document.hidden) return;
      var root = document.body || document.documentElement;
      if(!root) return;

      if(!currentIsVkPage()){
        if(observer){ observer.disconnect(); observer = null; }
        return;
      }

      var scope = root.querySelector("main, [role='main']") || root;
      var scopeKind = scope === root ? "body" : (scope.tagName || "?").toLowerCase();
      // LiteLLM Admin UI uses Tremor table:
      //   - Header rows:  <tr class="..."> ... <th class="tremor-TableHeaderCell-root ...">
      //   - Body rows:    <tr class="..."> ... <td class="..."> (no special class we know)
      // Tokens in body rows are MASKED by default ("sk-XXXX…ABCD"). User must
      // click the row's "show/copy" icon to reveal the full token — only then
      // does our scan() find a full sk-/hkg- pattern.
      // Selectors are deliberately broad: tbody tr / .tremor-TableBody-row / any
      // <tr> that contains <td> (skips <th>-only header rows).
      var candidates = scope.querySelectorAll(
        "tbody tr, tr.tremor-TableBody-row, [class*='TableBody-row'], tr td," +
        " tr[class*='TableRow-row'], tr, [role='row'], [data-row-key]"
      );
      // Filter out header rows: any <tr> whose first cell is a <th>, OR that
      // contains tremor-TableHeaderCell-root.
      var bodyRows = [];
      for(var b = 0; b < candidates.length; b++){
        var el = candidates[b];
        // Skip if element IS a <td> (we want the surrounding <tr>).
        if(el.tagName === "TD" || el.tagName === "TH"){
          if(el.parentElement && el.parentElement.tagName === "TR" &&
             bodyRows.indexOf(el.parentElement) === -1){
            bodyRows.push(el.parentElement);
          }
          continue;
        }
        // For <tr> elements, check that it's not a header row.
        if(el.tagName === "TR"){
          var isHeader = el.querySelector(".tremor-TableHeaderCell-root, [class*='TableHeaderCell']");
          if(isHeader) continue;
          // Reject <tr> that has only <th> children.
          var hasTd = el.querySelector("td");
          if(!hasTd) continue;
          bodyRows.push(el);
        } else {
          // Any other row-shaped element (role=row, data-row-key, etc.).
          bodyRows.push(el);
        }
      }
      var processed = 0;
      var skipped = 0;
      var curUrl = location.pathname + location.search;
      var urlChanged = lastUrl !== null && lastUrl !== curUrl;
      lastUrl = curUrl;
      if(urlChanged) scanDiagLogged = false;

      for(var i = 0; i < bodyRows.length; i++){
        var row = bodyRows[i];
        // Skip if we've already attached a button to this exact row (and
        // the VK token is unchanged) — avoids re-processing on observer ticks.
        var existingBtn = row.querySelector("[data-oc-vk-row-btn]");
        if(existingBtn){
          var prevVk = existingBtn.getAttribute("data-oc-vk-row-token");
          var prevVkh = existingBtn.getAttribute("data-oc-vk-row-vkh");
          var newVk = findTokenInRow(row);
          var newVkh = findKeyHashInRow(row);
          if(prevVk === newVk && prevVkh === newVkh){ skipped++; continue; }
        }
        var vk  = findTokenInRow(row);
        var vkh = findKeyHashInRow(row);

        // On URL change, also drop pre-existing buttons so we never show a
        // stale VK pointing at a row that now holds a different token.
        if(urlChanged){
          var stale = row.querySelectorAll("[data-oc-vk-row-btn]");
          for(var s = 0; s < stale.length; s++){
            if(stale[s].parentNode) stale[s].parentNode.removeChild(stale[s]);
          }
        }
        var uid = findUserIdInRow(row);

        if(vk){
          addRowButton(row, {vk: vk, uid: uid});
          processed++;
        } else if(vkh){
          // Masked row — use the SHA-256 hash, which LiteLLM exposes via
          // data-row-key and accepts on /key/info. No clipboard interception
          // needed: this is the SAME identifier the Admin UI uses internally.
          addHashButton(row, {vkh: vkh, uid: uid});
          processed++;
        } else {
          // Last resort: try the clipboard interceptor (works on some
          // LiteLLM versions that put the full token in clipboard on click).
          addCopyAndOpenButton(row);
        }
      }

      // FALLBACK: if no row-shaped candidates yielded a VK, scan the entire
      // <main> subtree for tokens in any descendant text node, then attach
      // the button to that token's nearest enclosing cell/block.
      if(!processed){
        var walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
          acceptNode: function(n){
            if(!n.nodeValue) return NodeFilter.FILTER_REJECT;
            return VK_RE.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
          }
        });
        var tn;
        while((tn = walker.nextNode())){
          var m = tn.nodeValue.match(VK_RE);
          if(!m) continue;
          var host = tn.parentNode;
          // Walk up until we find something resembling a "cell" — has a
          // sibling that is interactive, or contains our token plus other
          // useful data (alias / spend / expiry).
          var attempts = 0;
          while(host && host !== scope && attempts < 10){
            if(host.parentNode && host.parentNode.children && host.parentNode.children.length >= 2){
              // has at least one sibling — likely a cell inside a flex row
              break;
            }
            host = host.parentNode;
            attempts++;
          }
          if(!host || host === scope) host = tn.parentNode;
          var existing = host.querySelector && host.querySelector("[data-oc-vk-row-btn]");
          if(existing){ skipped++; continue; }
          if(urlChanged){
            var stale2 = host.querySelectorAll("[data-oc-vk-row-btn]");
            for(var s2 = 0; s2 < stale2.length; s2++){
              if(stale2[s2].parentNode) stale2[s2].parentNode.removeChild(stale2[s2]);
            }
          }
          var uid2 = findUserIdInRow(host);
          addRowButton(host, {vk: m[0], uid: uid2});
          processed++;
        }
      }

      if(DEBUG && !scanDiagLogged){
        log("vk-page diag: pathname=", location.pathname,
            "search=", location.search,
            "scopeChildren=", scope.querySelectorAll("*").length,
            "bodyRows=", bodyRows.length,
            "rowsWithVk=", processed, "rowsSkipped=", skipped,
            "urlChanged=", urlChanged);
        scanDiagLogged = true;
      }
      if(processed) log("vk rows processed:", processed);
      if(processed !== lastProcessed || !scanDiagLogged){
        lastProcessed = processed;
        collectDebugInfo(scope);
        showDebugBadge(processed, scopeKind, bodyRows.length);
      }

      if(typeof ensureObserver === "function") ensureObserver();
    } catch(e){
      log("scan error:", e && e.message);
    }
  }

  function throttledScan(){
    var now = Date.now();
    if (now - lastScanAt < SCAN_THROTTLE_MS) {
      if (!scanPending) {
        scanPending = true;
        setTimeout(function(){
          scanPending = false;
          lastScanAt = Date.now();
          scan();
        }, SCAN_THROTTLE_MS - (now - lastScanAt));
      }
      return;
    }
    lastScanAt = now;
    scan();
  }

  var observer = null;
  var debugBadge = null;
  var lastProcessed = 0;

  function showDebugBadge(processed, scopeKind, candidates){
    if(!DEBUG && !(SHOW_BADGE_ON_VK && currentIsVkPage())) return;
    if(!debugBadge){
      debugBadge = document.createElement("div");
      debugBadge.setAttribute("data-oc-vk-debug", "1");
      debugBadge.style.cssText = "position:fixed!important;left:18px!important;bottom:60px!important;z-index:2147483647!important;background:rgba(15,17,21,.92)!important;color:#a5b4fc!important;padding:8px 14px!important;border-radius:8px!important;font:600 12px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif!important;font-family:monospace!important;box-shadow:0 4px 14px rgba(0,0,0,.4)!important;max-width:520px!important;line-height:1.5!important;white-space:pre-wrap!important;cursor:move!important;max-height:70vh!important;overflow:auto!important;";
      // Make it draggable so the user can move it out of the way.
      (function(){
        var dragging = false, dx = 0, dy = 0;
        debugBadge.addEventListener("mousedown", function(e){
          dragging = true;
          dx = e.clientX - debugBadge.getBoundingClientRect().left;
          dy = e.clientY - debugBadge.getBoundingClientRect().top;
          e.preventDefault();
        });
        document.addEventListener("mousemove", function(e){
          if(!dragging) return;
          debugBadge.style.left = (e.clientX - dx) + "px";
          debugBadge.style.top = (e.clientY - dy) + "px";
          debugBadge.style.bottom = "auto";
        });
        document.addEventListener("mouseup", function(){ dragging = false; });
      })();
      document.body && document.body.appendChild(debugBadge);
    }
    var path = location.pathname + location.search;
    debugBadge.textContent =
      "[oc-vk] " + (currentIsVkPage() ? "ON" : "off")
      + " | btns=" + processed
      + " | cands=" + candidates
      + " | scope=" + scopeKind
      + "\npath=" + path
      + "\nmask=" + debugInfo.maskedSamples
      + "\ndatalist=" + debugInfo.dataRowKeys
      + "\nsample=" + debugInfo.firstRowSample;
  }

  // Collected once per scan to enrich the badge with diagnostic info.
  var debugInfo = {maskedSamples: "n/a", dataRowKeys: "n/a", firstRowSample: "n/a"};
  function collectDebugInfo(scope){
    // 1) count of "sk-..." patterns (full or masked like "sk-...ABCD")
    var allText = (scope && scope.textContent) || "";
    var masked = (allText.match(/sk-[.\u2026][A-Za-z0-9]{2,}/g) || []).length;
    var full   = (allText.match(/(sk-|hkg-)[A-Za-z0-9_-]{20,}/g) || []).length;
    debugInfo.maskedSamples = full + " full, " + masked + " masked";
    // 2) collect a few data-row-key / data-row-id values
    try {
      var drk = scope ? scope.querySelectorAll("[data-row-key], [data-row-id], [data-key], [data-id]") : [];
      var uniq = {};
      var samples = [];
      for(var i = 0; i < drk.length && samples.length < 6; i++){
        var v = drk[i].getAttribute("data-row-key") || drk[i].getAttribute("data-row-id") ||
                drk[i].getAttribute("data-key") || drk[i].getAttribute("data-id") || "";
        if(v && !uniq[v]){ uniq[v] = 1; samples.push(v.substring(0, 36)); }
      }
      debugInfo.dataRowKeys = samples.length ? samples.join(" | ") : "(none)";
    } catch(e){ debugInfo.dataRowKeys = "err"; }
    // 3) sample HTML from the first row candidate
    try {
      var first = scope && scope.querySelector("tr, [role='row'], [data-row-key]");
      if(first){
        var html = first.outerHTML || "";
        debugInfo.firstRowSample = html.length > 240 ? html.substring(0, 240) + "..." : html;
      } else {
        debugInfo.firstRowSample = "(no rows found)";
      }
    } catch(e){ debugInfo.firstRowSample = "err"; }
  }

  function ensureObserver(){
    if(currentIsVkPage() && !observer){
      observer = new MutationObserver(function(){
        if(idleHandle) return;
        idleHandle = idle(function(){
          idleHandle = 0;
          throttledScan();
        }, {timeout: 100});
      });
      var target = (document.body && document.body.querySelector("main")) || document.body || document.documentElement;
      try { observer.observe(target, {childList: true, subtree: true}); }
      catch(e){ log("observer init error:", e && e.message); }
    } else if(!currentIsVkPage() && observer){
      observer.disconnect();
      observer = null;
    }
  }

  function start(){
    scan();
    var probes = [500, 1500, 3000, 6000, 10000];
    probes.forEach(function(t){ setTimeout(scan, t); });
    ensureObserver();

    if(typeof document !== "undefined"){
      document.addEventListener("visibilitychange", function(){
        if(!document.hidden){ lastScanAt = 0; scan(); }
      }, {passive: true});
    }

    if(window.addEventListener){
      window.addEventListener("popstate", function(){ lastScanAt = 0; scan(); ensureObserver(); });
      window.addEventListener("hashchange", function(){ lastScanAt = 0; scan(); ensureObserver(); });
    }
    try {
      var _push = history.pushState;
      var _replace = history.replaceState;
      if(_push && !_push.__ocPatched){
        history.pushState = function(){
          var ret = _push.apply(this, arguments);
          lastScanAt = 0;
          setTimeout(function(){ scan(); ensureObserver(); }, 50);
          return ret;
        };
        history.pushState.__ocPatched = 1;
      }
      if(_replace && !_replace.__ocPatched){
        history.replaceState = function(){
          var ret = _replace.apply(this, arguments);
          lastScanAt = 0;
          setTimeout(function(){ scan(); ensureObserver(); }, 50);
          return ret;
        };
        history.replaceState.__ocPatched = 1;
      }
    } catch(e){ log("history patch error:", e && e.message); }
  }

  // Normalise /litellm/litellm/ double-prefix + strip login=success.
  function normaliseUrl(){
    try {
      var path = location.pathname;
      var stripped = path.replace(/^\/litellm\/litellm\//, "/litellm/");
      var search = location.search.replace(/([?&])login=success(&|$)/, function(m, p, tail){
        return tail === "&" ? p : "";
      });
      if (stripped !== path || search !== location.search) {
        history.replaceState(null, "", stripped + search + location.hash);
      }
    } catch(e) {}
  }
  normaliseUrl();

  if(document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();