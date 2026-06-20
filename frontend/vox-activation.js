/**
 * VOX — Account activation onboarding (self-contained).
 *
 * Один общий механизм активации аккаунта для всех платных host-режимов
 * (Solo / Duo one-device / Duo remote host / Room host) и для лендинга.
 *
 * Назначение:
 *   - server-authoritative проверка статуса через GET /api/account/status;
 *   - корректное различение состояний: неподтверждённый email,
 *     недостаточный баланс, недоступный billing, реальная ошибка сети;
 *   - модалка активации с повторной отправкой письма (cooldown 60с) и
 *     кнопкой «Я підтвердив email — перевірити статус»;
 *   - НЕ открывать WebSocket / микрофон / прогресс-бар до готовности аккаунта.
 *
 * Подключение:  <script src="/vox-activation.js"></script>
 * Использование: if (!(await VoxActivation.ensureReady())) return;
 */
(function (root) {
  'use strict';

  var TOKEN_KEY = 'vox_token';
  var COOLDOWN_SEC = 60;
  var _injected = false;
  var _cooldownTimer = null;
  var _autoRefreshBound = false;
  var _lastStatus = null;

  function getToken() { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (_) { return ''; } }

  function dispatchUpdate(status) {
    _lastStatus = status;
    try {
      root.dispatchEvent(new CustomEvent('vox:account-updated', { detail: status }));
    } catch (_) {}
  }

  // ── Server-authoritative статус ───────────────────────────────────────────
  function fetchStatus() {
    var token = getToken();
    if (!token) return Promise.resolve({ authenticated: false });
    return fetch('/api/account/status', { headers: { Authorization: 'Bearer ' + token } })
      .then(function (r) {
        if (r.status === 401) return { authenticated: false };
        if (!r.ok) return null;           // техническая ошибка → null
        return r.json();
      })
      .catch(function () { return null; }); // сеть недоступна → null
  }

  // ── DOM / стили ───────────────────────────────────────────────────────────
  function injectOnce() {
    if (_injected) return;
    _injected = true;

    var style = document.createElement('style');
    style.textContent = [
      '.vxa-overlay{position:fixed;inset:0;background:rgba(9,8,15,.82);backdrop-filter:blur(4px);',
      'display:none;align-items:center;justify-content:center;z-index:99999;padding:20px}',
      '.vxa-overlay.open{display:flex}',
      '.vxa-card{width:100%;max-width:440px;background:#15121f;border:1px solid #2a2340;',
      'border-radius:20px;padding:28px 26px;color:#f0eeff;font-family:Arial,Helvetica,sans-serif;',
      'box-shadow:0 24px 60px rgba(0,0,0,.5);text-align:center}',
      '.vxa-badge{font-size:34px;margin-bottom:6px}',
      '.vxa-title{font-size:20px;font-weight:700;margin:6px 0 12px;line-height:1.3}',
      '.vxa-text{font-size:14px;color:#9b93c4;line-height:1.6;margin:0 0 10px}',
      '.vxa-note{font-size:12px;color:#5e5880;margin:0 0 18px}',
      '.vxa-msg{font-size:13px;min-height:18px;margin:0 0 12px}',
      '.vxa-msg.err{color:#ff8a8a}.vxa-msg.ok{color:#2dd4a0}',
      '.vxa-btn{display:block;width:100%;box-sizing:border-box;border:0;border-radius:12px;',
      'padding:14px 18px;font-size:15px;font-weight:700;cursor:pointer;margin-top:10px}',
      '.vxa-btn-primary{background:linear-gradient(135deg,#9b8aff,#7c6aff);color:#fff}',
      '.vxa-btn-primary:disabled{opacity:.5;cursor:not-allowed}',
      '.vxa-btn-ghost{background:transparent;color:#a594ff;border:1px solid #2a2340}',
      '.vxa-btn-close{background:transparent;color:#5e5880;border:0;font-size:13px;margin-top:14px;cursor:pointer}',
    ].join('');
    document.head.appendChild(style);

    var ov = document.createElement('div');
    ov.className = 'vxa-overlay';
    ov.id = 'vxaOverlay';
    ov.innerHTML = [
      '<div class="vxa-card" role="dialog" aria-modal="true">',
      '  <div class="vxa-badge" id="vxaBadge">📧</div>',
      '  <h2 class="vxa-title" id="vxaTitle"></h2>',
      '  <p class="vxa-text" id="vxaText"></p>',
      '  <p class="vxa-note" id="vxaNote"></p>',
      '  <p class="vxa-msg" id="vxaMsg"></p>',
      '  <button class="vxa-btn vxa-btn-primary" id="vxaPrimary"></button>',
      '  <button class="vxa-btn vxa-btn-ghost" id="vxaSecondary"></button>',
      '  <button class="vxa-btn-close" id="vxaClose">Закрити</button>',
      '</div>',
    ].join('');
    document.body.appendChild(ov);

    document.getElementById('vxaClose').addEventListener('click', hide);
    ov.addEventListener('click', function (e) { if (e.target === ov) hide(); });
  }

  function $(id) { return document.getElementById(id); }
  function setMsg(text, kind) {
    var el = $('vxaMsg');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'vxa-msg' + (kind ? ' ' + kind : '');
  }

  function hide() {
    var ov = $('vxaOverlay');
    if (ov) ov.classList.remove('open');
    if (_cooldownTimer) { clearInterval(_cooldownTimer); _cooldownTimer = null; }
  }

  function isOpen() { var ov = $('vxaOverlay'); return !!(ov && ov.classList.contains('open')); }

  // ── Конкретные экраны ──────────────────────────────────────────────────────
  function showActivation(status) {
    injectOnce();
    bindAutoRefresh();
    $('vxaBadge').textContent = '📧';
    $('vxaTitle').textContent = 'Підтвердіть email, щоб активувати акаунт';
    $('vxaText').textContent =
      'Ми надіслали лист на вашу пошту. Відкрийте його та перейдіть за посиланням — ' +
      'після підтвердження ви отримаєте $3 для тестування VOX.';
    $('vxaNote').textContent = 'Не бачите листа? Перевірте папку «Спам».';
    setMsg('', '');

    var primary = $('vxaPrimary');
    primary.textContent = 'Надіслати лист повторно';
    primary.onclick = doResend;

    var secondary = $('vxaSecondary');
    secondary.style.display = 'block';
    secondary.textContent = 'Я підтвердив email — перевірити статус';
    secondary.onclick = doCheckStatus;

    $('vxaOverlay').classList.add('open');

    // Синхронизируем cooldown с сервером (resend_cooldown_remaining).
    var remaining = status && status.resend_cooldown_remaining;
    if (remaining && remaining > 0) startCooldown(remaining);
    else stopCooldown();
  }

  function showBalance(status) {
    injectOnce();
    $('vxaBadge').textContent = '💳';
    $('vxaTitle').textContent = 'Недостатній баланс';
    var bal = status && typeof status.balance === 'number' ? ('$' + status.balance.toFixed(2)) : '';
    $('vxaText').textContent =
      'На вашому рахунку ' + bal + '. Поповніть баланс, щоб запустити сесію.';
    $('vxaNote').textContent = '';
    setMsg('', '');

    var primary = $('vxaPrimary');
    primary.textContent = 'Поповнити баланс';
    primary.disabled = false;
    primary.onclick = function () {
      hide();
      if (typeof root.openTopupModal === 'function') root.openTopupModal();
    };
    $('vxaSecondary').style.display = 'none';
    $('vxaOverlay').classList.add('open');
  }

  function showError(kind) {
    injectOnce();
    var billing = kind === 'billing';
    $('vxaBadge').textContent = billing ? '🛠️' : '📡';
    $('vxaTitle').textContent = billing ? 'Сервіс тимчасово недоступний' : 'Проблема зі з’єднанням';
    $('vxaText').textContent = billing
      ? 'Сервіс оплати тимчасово недоступний. Спробуйте ще раз за хвилину.'
      : 'Не вдалося перевірити стан акаунта. Перевірте інтернет і спробуйте ще раз.';
    $('vxaNote').textContent = '';
    setMsg('', '');

    var primary = $('vxaPrimary');
    primary.textContent = 'Спробувати ще раз';
    primary.disabled = false;
    primary.onclick = function () { ensureReady(); };
    $('vxaSecondary').style.display = 'none';
    $('vxaOverlay').classList.add('open');
  }

  // ── Cooldown повторной отправки ────────────────────────────────────────────
  function stopCooldown() {
    if (_cooldownTimer) { clearInterval(_cooldownTimer); _cooldownTimer = null; }
    var btn = $('vxaPrimary');
    if (btn) { btn.disabled = false; btn.textContent = 'Надіслати лист повторно'; }
  }

  function startCooldown(seconds) {
    var btn = $('vxaPrimary');
    if (!btn) return;
    var left = Math.max(1, Math.floor(seconds));
    btn.disabled = true;
    btn.textContent = 'Надіслати повторно (' + left + ')';
    if (_cooldownTimer) clearInterval(_cooldownTimer);
    _cooldownTimer = setInterval(function () {
      left -= 1;
      if (left <= 0) { stopCooldown(); return; }
      btn.textContent = 'Надіслати повторно (' + left + ')';
    }, 1000);
  }

  // ── Действия ───────────────────────────────────────────────────────────────
  function doResend() {
    var token = getToken();
    if (!token) return;
    setMsg('', '');
    fetch('/api/auth/resend-verification', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + token },
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { return { r: r, d: d }; });
      })
      .then(function (res) {
        var r = res.r, d = res.d;
        if (r.status === 429) { startCooldown(d.retry_after || COOLDOWN_SEC); return; }
        if (r.ok && d.status === 'already_verified') { doCheckStatus(); return; }
        if (r.ok && d.status === 'sent') {
          setMsg(d.message || 'Лист надіслано.', 'ok');
          startCooldown(COOLDOWN_SEC);
          return;
        }
        setMsg('Не вдалося надіслати лист. Спробуйте ще раз пізніше.', 'err');
      })
      .catch(function () {
        setMsg('Не вдалося надіслати лист. Спробуйте ще раз пізніше.', 'err');
      });
  }

  function doCheckStatus() {
    setMsg('Перевіряємо…', '');
    return fetchStatus().then(function (s) {
      if (!s) { setMsg('Не вдалося перевірити. Перевірте інтернет.', 'err'); return; }
      dispatchUpdate(s);
      if (s.start_status === 'ready' || (s.email_verified && s.start_status !== 'email_verification_required')) {
        hide();
        // Email подтверждён — баланс мог обновиться (бонус $3).
        if (s.start_status !== 'ready' && s.start_status === 'insufficient_balance') {
          showBalance(s);
        }
        return;
      }
      if (s.start_status === 'email_verification_required') {
        setMsg('Email ще не підтверджено. Перейдіть за посиланням у листі.', 'err');
        return;
      }
      setMsg('', '');
    });
  }

  // Тихое обновление при возврате в приложение (не мешает, если уже ready).
  function silentRefresh() {
    if (!isOpen()) return;
    fetchStatus().then(function (s) {
      if (!s) return;
      dispatchUpdate(s);
      if (s.start_status === 'ready' || (s.email_verified && s.start_status !== 'email_verification_required')) {
        hide();
      }
    });
  }

  function bindAutoRefresh() {
    if (_autoRefreshBound) return;
    _autoRefreshBound = true;
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') silentRefresh();
    });
    root.addEventListener('pageshow', silentRefresh);
    root.addEventListener('focus', silentRefresh);
  }

  // ── Публичное API ───────────────────────────────────────────────────────────
  function ensureReady() {
    return fetchStatus().then(function (s) {
      if (!s) { showError('network'); return false; }
      if (!s.authenticated) return true; // анонимный/не-залогинен — поведение как раньше
      dispatchUpdate(s);
      switch (s.start_status) {
        case 'ready':
          hide();
          return true;
        case 'email_verification_required':
          showActivation(s);
          return false;
        case 'insufficient_balance':
          showBalance(s);
          return false;
        default:
          showError('billing');
          return false;
      }
    });
  }

  // Вызов из WS terminal-обработчика (на случай, если сервер всё же закрыл сессию).
  function handleTerminal(code, status) {
    if (code === 'email_verification_required') { showActivation(status || _lastStatus); return; }
    if (code === 'insufficient_balance' || code === 'no_balance') { showBalance(status || _lastStatus); return; }
    if (code === 'billing_unavailable') { showError('billing'); return; }
  }

  // При входе/загрузке: если аккаунт не подтверждён — сразу показать активацию.
  function checkOnLoad() {
    return fetchStatus().then(function (s) {
      if (!s || !s.authenticated) return;
      dispatchUpdate(s);
      if (s.start_status === 'email_verification_required') showActivation(s);
    });
  }

  root.VoxActivation = {
    ensureReady: ensureReady,
    refreshStatus: fetchStatus,
    showActivation: function () { return fetchStatus().then(function (s) { showActivation(s || {}); }); },
    showBalance: showBalance,
    handleTerminal: handleTerminal,
    checkOnLoad: checkOnLoad,
    hide: hide,
  };
})(typeof window !== 'undefined' ? window : this);
