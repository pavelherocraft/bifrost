(function() {
  if (window.__glmSwapBtnInjected) return;
  window.__glmSwapBtnInjected = true;
  function inject() {
    if (document.getElementById('glm-swap-btn')) return;
    var a = document.createElement('a');
    a.id = 'glm-swap-btn';
    a.href = '/litellm/admin/glm-swap/';
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = 'GLM Swap';
    a.title = 'Open GLM credentials swap tool';
    a.style.cssText = [
      'position:fixed', 'top:12px', 'right:12px', 'z-index:2147483647',
      'background:#2563eb', 'color:#fff', 'padding:6px 12px',
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