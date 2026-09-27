(function() {
  if (window.__payloadLogsBtnInjected) return;
  window.__payloadLogsBtnInjected = true;
  function inject() {
    if (document.getElementById('payload-logs-btn')) return;
    var a = document.createElement('a');
    a.id = 'payload-logs-btn';
    a.href = '/litellm/admin/payload-logs/';
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = 'Payload Logs';
    a.title = 'Open per-user request payload logs';
    a.style.cssText = [
      'position:fixed', 'top:12px', 'right:110px', 'z-index:2147483647',
      'background:#059669', 'color:#fff', 'padding:6px 12px',
      'border-radius:4px', 'font:13px/1 system-ui,-apple-system,sans-serif',
      'text-decoration:none', 'box-shadow:0 2px 6px rgba(0,0,0,0.3)',
      'opacity:0.85', 'transition:opacity 0.15s'
    ].join(';');
    a.addEventListener('mouseenter', function(){ a.style.opacity = '1'; });
    a.addEventListener('mouseleave', function(){ a.style.opacity = '0.85'; });
    document.body.appendChild(a);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
  // Re-inject after SPA route changes
  setTimeout(inject, 500);
  setTimeout(inject, 1500);
})();
