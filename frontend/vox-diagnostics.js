/**
 * VOX — Лёгкая диагностика соединения (без записи аудио).
 *
 * Помогает понять, что именно сломалось: сеть, авторизация, баланс, WebSocket,
 * service worker или микрофон. Показывает версии и текущее состояние.
 *
 * Открыть панель:
 *   - URL ?diag=1
 *   - 5 быстрых тапов в левый верхний угол экрана
 *   - window.VoxDiagnostics.toggle()
 *
 * Источник данных — необязательная функция window._voxConnDiag(), которую
 * страница может определить, плюс глобальные версии и состояние микрофона
 * window._voxMicState.
 */
(function () {
  'use strict';

  function val(x) {
    if (x === null || x === undefined) return '—';
    if (typeof x === 'object') { try { return JSON.stringify(x); } catch (_) { return String(x); } }
    return String(x);
  }

  function rsName(rs) {
    return ({ 0: 'CONNECTING', 1: 'OPEN', 2: 'CLOSING', 3: 'CLOSED' })[rs] || val(rs);
  }

  let panel, timer;

  function collect() {
    const d = (typeof window._voxConnDiag === 'function') ? (window._voxConnDiag() || {}) : {};
    return {
      'Frontend': window.VOX_FRONTEND_VERSION,
      'Service Worker': window.VOX_SW_VERSION,
      'Online': navigator.onLine,
      'Соединение (state)': d.state,
      'Connection ID': d.connId,
      'WS solo': d.soloWs != null ? rsName(d.soloWs) : null,
      'WS duo': d.duoWs != null ? rsName(d.duoWs) : null,
      'WS room': d.roomWs != null ? rsName(d.roomWs) : null,
      'Reconnect attempts': d.retries,
      'Last close': d.lastCloseCode != null ? (d.lastCloseCode + ' ' + (d.lastCloseReason || '')) : null,
      'Активность назад (мс)': d.lastActivityAgoMs,
      'Sample rate': d.sampleRate,
      'Last error': d.lastError,
      'Микрофон': window._voxMicState,
    };
  }

  function render() {
    if (!panel) return;
    const data = collect();
    const rows = Object.keys(data)
      .map(k => `<div style="display:flex;justify-content:space-between;gap:12px;padding:3px 0;border-bottom:1px solid #221c33">
        <span style="color:#8b83b0">${k}</span><span style="color:#f0eeff;text-align:right;word-break:break-all">${val(data[k])}</span></div>`)
      .join('');
    panel.querySelector('[data-body]').innerHTML = rows;
  }

  function build() {
    if (panel) return;
    panel = document.createElement('div');
    panel.id = 'voxDiagPanel';
    panel.style.cssText =
      'position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:100000;width:min(420px,92vw);' +
      'max-height:80vh;overflow:auto;background:#0f0d1a;border:1px solid #2a2340;border-radius:16px;' +
      'padding:16px 18px;font-family:Arial,sans-serif;font-size:13px;box-shadow:0 20px 60px rgba(0,0,0,.6);display:none';
    panel.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
      '<strong style="color:#a594ff;font-size:15px">Диагностика соединения</strong>' +
      '<button data-close style="background:#2a2340;color:#fff;border:0;border-radius:8px;padding:4px 10px;cursor:pointer">✕</button></div>' +
      '<div data-body></div>' +
      '<div style="margin-top:12px;text-align:center;color:#5e5880;font-size:11px">обновляется автоматически</div>';
    document.body.appendChild(panel);
    panel.querySelector('[data-close]').addEventListener('click', hide);
  }

  function show() { build(); panel.style.display = 'block'; render(); clearInterval(timer); timer = setInterval(render, 1000); }
  function hide() { if (panel) panel.style.display = 'none'; clearInterval(timer); timer = null; }
  function toggle() { (panel && panel.style.display === 'block') ? hide() : show(); }

  // 5 быстрых тапов в левый верхний угол
  function installCornerTap() {
    let taps = [], hotspot = document.createElement('div');
    hotspot.style.cssText = 'position:fixed;left:0;top:0;width:48px;height:48px;z-index:99998;background:transparent';
    hotspot.addEventListener('click', () => {
      const now = Date.now();
      taps = taps.filter(t => now - t < 1500); taps.push(now);
      if (taps.length >= 5) { taps = []; toggle(); }
    });
    document.body.appendChild(hotspot);
  }

  function init() {
    installCornerTap();
    try {
      if (new URLSearchParams(location.search).get('diag') === '1') show();
    } catch (_) {}
  }

  window.VoxDiagnostics = { show, hide, toggle };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
