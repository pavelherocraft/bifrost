(function(){
  if(window.__ocInj) return;
  window.__ocInj = 1;

  var LS_KEY = "bifrost_opencode_user_id";
  var DEBUG = /[?&]ocdebug=1\b/.test(location.search);

  // Safari detection: navigator.userAgent contains "Safari" but NOT "Chrome"/"Chromium"
  // WebKit UA is shared with Chrome on iOS/macOS — distinguish by absence of "Chrome"
  var IS_SAFARI = typeof navigator !== "undefined" &&
    /Safari/.test(navigator.userAgent) &&
    !/Chrome|CriOS|Chromium|Edg|OPR/.test(navigator.userAgent);

  // Throttle: Safari hydration is slow on Next.js apps, give it more headroom.
  var SCAN_THROTTLE_MS = IS_SAFARI ? 1500 : 750;

  function log(){
    if(DEBUG && window.console) console.log.apply(console, ["[oc-inject]"].concat([].slice.call(arguments)));
  }
  log("init, IS_SAFARI=", IS_SAFARI, "throttle=", SCAN_THROTTLE_MS);

  function readToken(){
    try {
      var parts = document.cookie.split("; ");
      for (var i = 0; i < parts.length; i++) {
        if (parts[i].indexOf("token=") === 0) {
          try { return decodeURIComponent(parts[i].split("=").slice(1).join("=")); } catch(e){}
        }
      }
    } catch(e){}
    try { return sessionStorage.getItem("token"); } catch(e){ return null; }
  }

  function uidFromJWT(){
    var t = readToken();
    if (!t || typeof t !== "string") return null;
    var parts = t.split(".");
    if (parts.length !== 3) return null;
    try {
      var payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      var json = decodeURIComponent(atob(payload).split("").map(function(c){
        return "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2);
      }).join(""));
      var claims = JSON.parse(json);
      // Validate UUID format — JWTs may contain key/user_email/sub claims that
      // are NOT user_ids. If user_id or sub is not a UUID, fall through to DOM.
      var candidate = claims.user_id || claims.sub || null;
      if(candidate && UUID_RE.test(candidate)) return candidate;
      log("jwt: rejected non-UUID candidate:", candidate);
      return null;
    } catch(e){
      log("jwt parse error:", e && e.message);
      return null;
    }
  }

  var UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

  function uidFromDOM(){
    var root = document.body;
    if (!root) return null;
    // CSS attribute case-insensitive flag ([attr*="x" i]) is unsupported in
    // Safari < 16.4 — entire selector becomes invalid, querySelectorAll returns
    // empty. Split into two separate selectors (CI + CS) to maximise coverage.
    var sels = [
      '[data-testid*="user" i]',
      '[data-testid*="user"]',
      '[class*="profile" i]',
      '[class*="Profile" i]',
      '[class*="profile"]',
      '[class*="Profile"]',
      '[href*="/user/"]',
      'header',
      'aside'
    ];
    var candidates = root.querySelectorAll(sels.join(","));
    for (var i = 0; i < candidates.length; i++) {
      var txt = candidates[i].textContent || "";
      if (!txt) continue;
      var m = txt.match(UUID_RE);
      if (m) return m[0];
    }
    return null;
  }

  var cachedUid = null;
  var cacheValid = false;

  function detectUid(force){
    if (cacheValid && !force) return cachedUid;
    var jwt = uidFromJWT();
    if (jwt && UUID_RE.test(jwt)) { cachedUid = jwt; cacheValid = true; log("uid from JWT:", jwt); return jwt; }
    var dom = uidFromDOM();
    if (dom && UUID_RE.test(dom)) { cachedUid = dom; cacheValid = true; log("uid from DOM:", dom); return dom; }
    // Don't poison the cache with `null` — keep retrying on subsequent
    // MutationObserver ticks (Next.js hydration on macOS Safari is slow
    // and the auth/profile block often appears 2-5s after first paint).
    cachedUid = null;
    return null;
  }

  function getSavedUid(){
    try { return localStorage.getItem(LS_KEY) || ""; } catch(e){ return ""; }
  }
  function saveUid(uid){
    if(uid && !UUID_RE.test(uid)){
      log("saveUid: refused non-UUID uid:", uid);
      return;
    }
    try { localStorage.setItem(LS_KEY, uid); } catch(e){}
  }
  function clearUid(){
    try { localStorage.removeItem(LS_KEY); } catch(e){}
  }

  function openSetup(uid){
    if(!uid || !UUID_RE.test(uid)){
      log("openSetup: refused non-UUID uid:", uid);
      return;
    }
    window.open("/setup-opencode/?user=" + encodeURIComponent(uid), "_blank", "noopener");
  }

  // ---- FAB (floating action button) ----
  // Persist reference so we can re-attach after Next.js SPA navigation
  // unmounts our node (Next.js replaces <body> children on route change,
  // detaching our previously-appended FAB).
  var fabNode = null;

  function buildFab(){
    var saved = getSavedUid();
    var detected = detectUid(true);

    var fab = document.createElement("div");
    fab.id = "oc-floating";
    fab.setAttribute("data-oc-injected", "1");
    fab.style.cssText = "position:fixed!important;right:18px!important;bottom:60px!important;z-index:2147483647!important;display:flex!important;flex-direction:column!important;gap:6px!important;align-items:flex-end!important;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif!important;pointer-events:auto!important;";

    var btnRow = document.createElement("div");
    btnRow.style.cssText = "display:flex!important;align-items:center!important;gap:6px!important;";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "\uD83E\uDDF0 Мои настройки";
    btn.style.cssText = "background:#4f46e5!important;color:#fff!important;border:0!important;padding:10px 16px!important;border-radius:999px!important;font-size:13px!important;font-weight:600!important;cursor:pointer!important;box-shadow:0 4px 14px rgba(0,0,0,.4)!important;white-space:nowrap!important;font-family:inherit!important;";
    btnRow.appendChild(btn);

    var clear = document.createElement("button");
    clear.type = "button";
    clear.textContent = "\u2715";
    clear.title = "Забыть сохранённый user_id";
    clear.style.cssText = "background:#2a2f3a!important;color:#fff!important;border:0!important;width:24px!important;height:24px!important;border-radius:50%!important;font-size:12px!important;line-height:1!important;cursor:pointer!important;display:none!important;align-items:center!important;justify-content:center!important;font-family:inherit!important;";
    btnRow.appendChild(clear);

    fab.appendChild(btnRow);

    var uidLabel = document.createElement("div");
    uidLabel.style.cssText = "background:rgba(15,17,21,.85)!important;color:#a5b4fc!important;padding:3px 10px!important;border-radius:12px!important;font-size:11px!important;font-family:inherit!important;display:none!important;max-width:280px!important;overflow:hidden!important;text-overflow:ellipsis!important;white-space:nowrap!important;";
    fab.appendChild(uidLabel);

    function showUid(value){
      if(value){
        uidLabel.textContent = "user: " + value.substring(0, 8) + "…";
        uidLabel.title = value;
        uidLabel.style.display = "block!important";
      } else {
        uidLabel.style.display = "none!important";
      }
    }

    function showClear(visible){
      clear.style.display = visible ? "inline-flex!important" : "none!important";
    }

    if(saved){
      showUid(saved);
      showClear(true);
    } else if(detected){
      showUid(detected);
      showClear(false);
    }

    btn.addEventListener("click", function(ev){
      ev.preventDefault();
      ev.stopPropagation();
      var currentSaved = getSavedUid();
      cacheValid = false;
      var currentDetected = detectUid(true);
      var finalUid = currentSaved || currentDetected;
      if(finalUid){
        if(!currentSaved){
          saveUid(finalUid);
          showClear(true);
        }
        openSetup(finalUid);
      } else {
        var input = prompt("Не удалось автоматически определить твой user_id.\nВведи его вручную (UUID). Его можно найти в правом верхнем углу LiteLLM — блок с профилем пользователя.");
        if(input && input.trim().length >= 8){
          input = input.trim();
          saveUid(input);
          showUid(input);
          showClear(true);
          openSetup(input);
        }
      }
    });

    clear.addEventListener("click", function(ev){
      ev.preventDefault();
      ev.stopPropagation();
      clearUid();
      cacheValid = false;
      var detectedNow = detectUid(true);
      if(detectedNow){
        showUid(detectedNow);
        showClear(false);
      } else {
        showUid("");
        showClear(false);
      }
    });

    return fab;
  }

  function ensureFloating(){
    // Cheap path: still in DOM and our cached ref is alive
    if(fabNode && document.body && document.body.contains(fabNode)){
      return fabNode;
    }
    // Cached ref stale (Next.js detached it on route change). Drop it.
    if(fabNode && fabNode.parentNode){
      try { fabNode.parentNode.removeChild(fabNode); } catch(e){}
    }
    fabNode = null;

    var host = document.body || document.documentElement;
    if(!host) return null;

    // Avoid duplicate if a stale #oc-floating still exists with no cached ref
    var stale = document.getElementById("oc-floating");
    if(stale && stale.parentNode){
      try { stale.parentNode.removeChild(stale); } catch(e){}
    }

    fabNode = buildFab();
    host.appendChild(fabNode);
    log("fab (re)appended to", host.nodeName);
    return fabNode;
  }

  function refreshUidLabel(){
    var fab = ensureFloating();
    if(!fab) return;
    var saved = getSavedUid();
    var detected = cachedUid;
    var finalUid = saved || detected;
    var uidLabel = fab.children[1];
    var btnRow = fab.children[0];
    var clearBtn = btnRow.children[1];
    if(uidLabel){
      if(finalUid){
        uidLabel.textContent = "user: " + finalUid.substring(0, 8) + "…";
        uidLabel.title = finalUid;
        uidLabel.style.display = "block!important";
      } else {
        uidLabel.style.display = "none!important";
      }
    }
    if(clearBtn){
      clearBtn.style.display = saved ? "inline-flex!important" : "none!important";
    }
  }

  // ---- Universal Users-table parser ----
  // Resilient to data-testid renames across LiteLLM versions. Finds any
  // table on /ui/users (or wherever Internal Users renders) and extracts
  // UUIDs from row text. Adds a per-row "🛠 OpenCode" button.
  // Scan a node for a UUID in any text node OR attribute value.
  // (Legacy — superseded by uuidInSubtree in the universal parser.)
  // Kept here in case anything else still references it.
  function uidInNode(node){
    if(!node) return null;
    if(node.nodeType === 3){
      var m = node.nodeValue && node.nodeValue.match(UUID_RE);
      return m ? m[0] : null;
    }
    var attrNames = ["data-user-id", "data-row-key", "data-key", "data-id",
                     "data-row-id", "data-uuid", "data-user", "data-testid",
                     "id", "href", "data-href"];
    for(var i = 0; i < attrNames.length; i++){
      var v = node.getAttribute && node.getAttribute(attrNames[i]);
      if(v && v.length < 1024){
        var m = v.match(UUID_RE);
        if(m) return m[0];
      }
    }
    if(node.childNodes && node.childNodes.length){
      for(var j = 0; j < node.childNodes.length; j++){
        var found = uidInNode(node.childNodes[j]);
        if(found) return found;
      }
    }
    return null;
  }

  // Walk descendants, find each element whose own subtree contains a UUID,
  // and return the *innermost* such element per UUID (the row-shaped one).
  // Then group by common parent so we add one button per row, not per cell.
  //
  // Performance: instead of querySelectorAll('*') (which enumerates EVERY
  // node — thousands on a hydrated Next.js page), narrow scope to row-shaped
  // candidates first, then validate each one with a small subtree walk.
  function collectRowCandidates(root){
    // (A) legacy data-testid pattern — cheap, takes priority
    // data-testid format: "user-status-<UUID>" on antd <Tag> in Status column
    // (see chunk 0lstohw6r.qs..js: `data-testid`:`user-status-${e.original.user_id}`)
    var byTestid = root.querySelectorAll('[data-testid*="user-status-"]');
    if(byTestid.length){
      var rows = [];
      for(var i = 0; i < byTestid.length; i++){
        var r = byTestid[i].closest("tr, [role='row']");
        if(!r) continue;
        // Validate UUID strictly: don't push if data-testid suffix is not a UUID
        // (e.g. matches "user-status-filter" elsewhere). UUID_RE is global.
        var tid = byTestid[i].getAttribute("data-testid") || "";
        var uidMatch = tid.match(/^user-status-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i);
        if(uidMatch) rows.push({uid: uidMatch[1], row: r});
      }
      if(rows.length) return rows;
    }

    // (B) Narrow to row-shaped candidates only. Anything matching one of
    //     these selectors MIGHT be a row — we then validate.
    var rowSels = "tr, [role='row'], [data-testid*='user-status-'], [data-user-id], [data-row-key]";
    var candidates = root.querySelectorAll(rowSels);
    var out = [];
    var seenRows = {};

    for(var k = 0; k < candidates.length; k++){
      var el = candidates[k];
      if(seenRows[el]) continue;

      // Resolve to actual row element (data-user-id might be on a child cell,
      // not on the row itself)
      var row = el.closest("tr, [role='row']");
      if(!row) row = el; // last resort: the data-user-id element itself
      if(seenRows[row]) continue;
      seenRows[row] = 1;

      // Extract UUID from the row's subtree, capped at 150 nodes
      var uid = uuidInSubtree(row, 150);
      if(!uid) continue;

      out.push({uid: uid, row: row});
    }
    return out;
  }

  // Cap-bounded UUID lookup in a subtree. Skips element attributes whose
  // values look like big URLs/blobs. Returns first UUID found or null.
  function uuidInSubtree(root, maxNodes){
    var n = 0;
    var stack = [root];
    while(stack.length && n < maxNodes){
      var node = stack.pop();
      n++;
      if(node.nodeType === 3){
        if(node.nodeValue){
          var m = node.nodeValue.match(UUID_RE);
          if(m) return m[0];
        }
      } else if(node.nodeType === 1){
        var attrs = node.attributes;
        if(attrs){
          for(var i = 0; i < attrs.length; i++){
            var av = attrs[i].value;
            // Only check attribute names that plausibly carry a UUID
            var an = attrs[i].name;
            if(av && av.length < 256 && (
              an === "id" || an === "href" || an === "data-user-id" ||
              an === "data-row-key" || an === "data-key" || an === "data-id" ||
              an === "data-row-id" || an === "data-uuid" || an === "data-user" ||
              an.indexOf("data-testid") === 0)){
              var m2 = av.match(UUID_RE);
              if(m2) return m2[0];
            }
          }
        }
        // Push children in reverse so first child is processed first
        if(node.childNodes && node.childNodes.length){
          for(var c = node.childNodes.length - 1; c >= 0; c--){
            stack.push(node.childNodes[c]);
          }
        }
      }
    }
    return null;
  }

  function pickActionCell(row){
    // Prefer last cell containing interactive descendants.
    // Walk up to nearest "row-like" container, then take its last child.
    if(!row || !row.children || row.children.length === 0) return null;
    var cells = row.children;
    for(var i = cells.length - 1; i >= 0; i--){
      var c = cells[i];
      if(c.querySelector && c.querySelector("a, button, [role='button']")) return c;
    }
    return cells[cells.length - 1];
  }

  function addRowButton(tr, uid){
    // Validate uid is a real UUID — skip rows where extraction failed
    if(!uid || !UUID_RE.test(uid)) return;
    // CRITICAL: ALWAYS remove existing button first (don't early-return).
    // Next.js reuses <tr> elements when paginating users list: row DOM stays
    // put while data-testid and inner content are swapped to a new user.
    // A previous scan's button (with old uid) would otherwise stick around
    // pointing at the wrong user_id — exactly the bug on page 2 of Internal Users.
    var existing = tr.querySelectorAll("[data-oc-row-btn]");
    for(var e = 0; e < existing.length; e++){
      if(existing[e].parentNode) existing[e].parentNode.removeChild(existing[e]);
    }
    var actionCell = pickActionCell(tr);
    if(!actionCell) return;
    var btn = document.createElement("a");
    btn.setAttribute("data-oc-row-btn", "1");
    btn.setAttribute("data-oc-row-uid", uid);
    btn.href = "/setup-opencode/?user=" + encodeURIComponent(uid);
    btn.target = "_blank";
    btn.rel = "noopener";
    btn.textContent = "\uD83E\uDDF0 OpenCode";
    btn.style.cssText = "display:inline-flex;align-items:center;padding:4px 10px;background:#4f46e5;color:#fff;border-radius:4px;font-size:12px;font-weight:600;text-decoration:none;cursor:pointer;white-space:nowrap;margin-left:6px;";
    actionCell.appendChild(btn);
  }

  var lastScanAt = 0;
  var scanPending = false;
  var lastUrl = null;
  var scanDiagLogged = false;

  function scan(){
    try {
      // Skip work entirely when tab is hidden — saves CPU on background tabs
      // and avoids fighting Next.js hydration when window regains focus.
      if(typeof document !== "undefined" && document.hidden) return;

      var root = document.body || document.documentElement;
      if(!root) return;

      // FAB: always re-ensure (handles Next.js SPA navigation detaching our node)
      ensureFloating();
      refreshUidLabel();

      // Detect SPA navigation: if URL changed, force re-scan of per-row buttons.
      // Next.js soft-navigates — DOM changes without full reload, so cached
      // `data-oc-row-btn` markers live but the table rows themselves are new.
      var curUrl = location.pathname + location.search;
      var urlChanged = lastUrl !== null && lastUrl !== curUrl;
      lastUrl = curUrl;
      if(urlChanged) scanDiagLogged = false;

      // Only try per-row buttons on users-like pages
      var isUsersPage = currentIsUsersPage();
      if(isUsersPage){
        // Scope: prefer <main>/[role="main"] so we don't enumerate sidebar/header
        var scope = root.querySelector("main, [role='main']") || root;
        var candidates = collectRowCandidates(scope);
        if(DEBUG && !scanDiagLogged){
          log("users-page diag: pathname=", location.pathname,
              "search=", location.search,
              "scopeChildren=", scope.querySelectorAll("*").length,
              "candidates=", candidates.length,
              "urlChanged=", urlChanged);
          scanDiagLogged = true;
        }
        for(var i = 0; i < candidates.length; i++){
          var row = candidates[i].row;
          var uid = candidates[i].uid;
          // On URL change, drop existing row buttons so we don't keep stale UUIDs
          if(urlChanged){
            var stale = row.querySelectorAll("[data-oc-row-btn]");
            for(var s = 0; s < stale.length; s++){
              if(stale[s].parentNode) stale[s].parentNode.removeChild(stale[s]);
            }
          }
          addRowButton(row, uid);
        }
        if(candidates.length) log("users-page rows processed:", candidates.length);
      }

      // Toggle MutationObserver based on current page. Cheap on most pages
      // (no-op if already in correct state); toggling on navigation between
      // /logs ↔ /users keeps CPU usage proportional to what we actually need.
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

  // Track whether we currently need MutationObserver active.
  // We only need it on Users-like pages (where row content changes without
  // a navigation event — e.g. filtering, pagination). On logs/keys/etc.
  // MutationObserver on a 5k-row virtualised table just melts the CPU.
  var observer = null;
  var observingMain = false;
  var idleHandle = 0;

  function ensureObserver(){
    var isUsersPage = currentIsUsersPage();
    if(isUsersPage && !observer){
      observer = new MutationObserver(function(){
        if(idleHandle) return; // already scheduled
        idleHandle = setTimeout(function(){
          idleHandle = 0;
          throttledScan();
        }, 100);
      });
      // Observe <main>/<body> rather than documentElement to skip sidebar/header
      var target = (document.body && document.body.querySelector("main")) || document.body || document.documentElement;
      try {
        observer.observe(target, {childList: true, subtree: true});
        observingMain = !!target;
      } catch(e){ log("observer init error:", e && e.message); }
    } else if(!isUsersPage && observer){
      observer.disconnect();
      observer = null;
      observingMain = false;
    }
  }

  // Reused by scan() and visibility handler
  function currentIsUsersPage(){
    return /[?&]page=users\b/.test(location.search) ||
      /\/users(\/|$|\?)/.test(location.pathname) ||
      /\/internal-users/.test(location.pathname);
  }

  function start(){
    scan();
    // Initial burst: cover Next.js hydration on first paint.
    // Safari needs longer windows (see IS_SAFARI branch above).
    var probes = IS_SAFARI
      ? [500, 1500, 3500, 6000, 10000, 15000]
      : [500, 1500, 3000, 6000, 10000];
    probes.forEach(function(t){ setTimeout(scan, t); });

    // MutationObserver: opt-in only when on Users-like pages. Avoids
    // drowning in mutation records from virtualised lists elsewhere.
    var idle = window.requestIdleCallback || function(cb){
      return setTimeout(function(){ cb({didTimeout:false, timeRemaining:function(){return 16;}}); }, 0);
    };
    var cancelIdle = window.cancelIdleCallback || clearTimeout;
    var idleHandle = 0;
    ensureObserver();

    // Pause work when tab is hidden; resume + force a scan on return.
    if(typeof document !== "undefined"){
      document.addEventListener("visibilitychange", function(){
        if(!document.hidden){
          lastScanAt = 0;
          scan();
        }
      }, {passive: true});
    }

    // SPA navigation hooks. These always fire regardless of page type, so
    // history navigation between pages re-establishes the right observer.
    if(window.addEventListener){
      window.addEventListener("popstate", function(){
        lastScanAt = 0;
        scan();
        ensureObserver();
      });
      window.addEventListener("hashchange", function(){
        lastScanAt = 0;
        scan();
        ensureObserver();
      });
    }
    // Monkey-patch pushState/replaceState so we notice soft navigations.
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
    } catch(e){
      log("history patch error:", e && e.message);
    }
  }

  // Strip LiteLLM's post-login sentinel param so it doesn't leak into browser
  // history, shareable URLs, or confuse downstream logic. Replaces current
  // history entry instead of pushing a new one (no extra back-button entry).
  // Also normalises the double-prefix path that sometimes appears after the
  // /v2/login redirect (e.g. /litellm/litellm/ui/?login=success&...) so the
  // URL bar always shows the canonical /litellm/ui/...
  function normaliseUrl(){
    try {
      var path = location.pathname;
      var stripped = path.replace(/^\/litellm\/litellm\//, "/litellm/");
      var changed = stripped !== path;
      var search = location.search.replace(/([?&])login=success(&|$)/, function(m, p, tail){
        return tail === "&" ? p : "";
      });
      var newSearch = (search === location.search) ? location.search : search;
      if (changed || newSearch !== location.search) {
        history.replaceState(null, "", stripped + newSearch + location.hash);
        log("normalised url:", stripped + newSearch);
      }
    } catch(e) {
      log("normaliseUrl error:", e && e.message);
    }
  }
  normaliseUrl();

  if(document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
