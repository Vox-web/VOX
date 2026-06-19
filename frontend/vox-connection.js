/**
 * VOX — WebSocket reconnect state machine.
 *
 * Решает главный симптом нестабильности на Android: после сворачивания/смены
 * сети сокет может остаться "полуоткрытым" (readyState OPEN, но сервер уже мёртв)
 * — аудио уходит в никуда, перевод "молчит". Здесь:
 *   - явные состояния соединения;
 *   - экспоненциальный backoff с jitter и лимитом попыток;
 *   - запрет нескольких параллельных сокетов;
 *   - heartbeat ping + stale-watchdog (нет активности → форс-реконнект);
 *   - повторная отправка config/audio_meta после reconnect (buildOpenMessages);
 *   - отмена всех таймеров при stop()/logout;
 *   - терминальные состояния auth_required / insufficient_balance / microphone_error.
 *
 * Зависимости инъектируются (WebSocket, таймеры, now) — модуль юнит-тестируется
 * в Node без браузера. В браузере подключается как <script> и кладёт класс в window.
 */
(function (root, factory) {
  const mod = factory();
  if (typeof module === 'object' && module.exports) module.exports = mod;
  else root.VoxConnection = mod.VoxConnection, root.VOX_CONN_STATES = mod.STATES;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const STATES = Object.freeze({
    IDLE: 'idle',
    CONNECTING: 'connecting',
    CONNECTED: 'connected',
    RECONNECTING: 'reconnecting',
    OFFLINE: 'offline',
    AUTH_REQUIRED: 'auth_required',
    INSUFFICIENT_BALANCE: 'insufficient_balance',
    MICROPHONE_ERROR: 'microphone_error',
    CLOSED: 'closed',
  });

  // Терминальные состояния — авто-reconnect НЕ выполняется.
  const TERMINAL = new Set([
    STATES.AUTH_REQUIRED,
    STATES.INSUFFICIENT_BALANCE,
    STATES.CLOSED,
  ]);

  class VoxConnection {
    constructor(opts) {
      opts = opts || {};
      this.url = opts.url;
      this._WS = opts.WebSocketImpl || (typeof WebSocket !== 'undefined' ? WebSocket : null);
      this._timers = opts.timers || {
        setTimeout: (...a) => setTimeout(...a),
        clearTimeout: (...a) => clearTimeout(...a),
        setInterval: (...a) => setInterval(...a),
        clearInterval: (...a) => clearInterval(...a),
      };
      this._now = opts.now || (() => Date.now());
      this._isOnline = opts.isOnline || (() => (typeof navigator !== 'undefined' ? navigator.onLine : true));

      this.maxRetries = opts.maxRetries != null ? opts.maxRetries : 6;
      this.baseDelay = opts.baseDelay != null ? opts.baseDelay : 500;
      this.maxDelay = opts.maxDelay != null ? opts.maxDelay : 15000;
      this.heartbeatInterval = opts.heartbeatInterval != null ? opts.heartbeatInterval : 15000;
      this.staleTimeout = opts.staleTimeout != null ? opts.staleTimeout : 35000;
      this._rand = opts.random || Math.random;

      this.onState = opts.onState || function () {};
      this.onMessage = opts.onMessage || function () {};
      this.buildOpenMessages = opts.buildOpenMessages || function () { return []; };
      this.isPong = opts.isPong || function (d) { return d && d.type === 'pong'; };

      this.binaryType = opts.binaryType || 'arraybuffer';

      this.state = STATES.IDLE;
      this.ws = null;
      this.retries = 0;
      this._stopped = false;
      this._reconnectTimer = null;
      this._heartbeatTimer = null;
      this._lastActivity = null; // null = активности ещё не было (важно: 0 — валидное время)
      this._lastCloseCode = null;
      this._lastCloseReason = '';
      this._lastError = null;
      this._connId = null;
    }

    // --- Публичный API ---
    start() {
      this._stopped = false;
      if (this.ws && (this.ws.readyState === 0 /*CONNECTING*/ || this.ws.readyState === 1 /*OPEN*/)) {
        return; // single-connection guard: уже подключаемся/подключены
      }
      this._connect();
    }

    stop(reason) {
      this._stopped = true;
      this._clearReconnect();
      this._stopHeartbeat();
      this._closeSocket(1000, reason || 'client_stop');
      this._setState(STATES.CLOSED, { reason: reason || 'client_stop' });
    }

    /** Внешний сигнал: сеть вернулась / приложение вернулось из фона. */
    resume(reason) {
      if (this._stopped) return;
      if (this.state === STATES.CONNECTED && !this._isStale()) return;
      this._clearReconnect();
      this.retries = 0;
      this._forceReconnect(reason || 'resume');
    }

    notifyOnline() { this.resume('online'); }
    notifyOffline() {
      if (this._stopped) return;
      this._setState(STATES.OFFLINE, { reason: 'offline' });
    }

    markMicrophoneError(detail) {
      this._lastError = { type: 'microphone', detail: detail || null };
      this._setState(STATES.MICROPHONE_ERROR, { detail });
    }

    /** Сервер прислал терминальную ошибку (закрываем без авто-reconnect). */
    markTerminal(state, info) {
      if (!TERMINAL.has(state) && state !== STATES.MICROPHONE_ERROR) return;
      this._stopped = true;
      this._clearReconnect();
      this._stopHeartbeat();
      this._closeSocket(1000, info && info.reason || state);
      this._setState(state, info || {});
    }

    send(data) {
      if (this.ws && this.ws.readyState === 1) {
        this.ws.send(data);
        return true;
      }
      return false;
    }

    getDiagnostics() {
      return {
        connId: this._connId,
        state: this.state,
        retries: this.retries,
        lastCloseCode: this._lastCloseCode,
        lastCloseReason: this._lastCloseReason,
        lastError: this._lastError,
        lastActivityAgoMs: this._lastActivity != null ? (this._now() - this._lastActivity) : null,
        wsReadyState: this.ws ? this.ws.readyState : null,
      };
    }

    // --- Внутреннее ---
    _setState(state, info) {
      if (this.state === state) return;
      this.state = state;
      try { this.onState(state, info || {}); } catch (_) {}
    }

    _connect() {
      if (!this._WS) return;
      if (!this._isOnline()) {
        this._setState(STATES.OFFLINE, { reason: 'offline_at_connect' });
        return;
      }
      this._connId = 'c' + Math.floor(this._rand() * 1e9).toString(36) + this._now().toString(36);
      this._setState(this.retries > 0 ? STATES.RECONNECTING : STATES.CONNECTING, { attempt: this.retries });

      let ws;
      try {
        ws = new this._WS(this.url);
      } catch (e) {
        this._lastError = { type: 'ws_construct', detail: String(e) };
        this._scheduleReconnect();
        return;
      }
      try { ws.binaryType = this.binaryType; } catch (_) {}
      this.ws = ws;

      ws.onopen = () => {
        this.retries = 0;
        this._lastActivity = this._now();
        this._setState(STATES.CONNECTED, { connId: this._connId });
        // Повторная отправка auth/config/audio_meta после (ре)коннекта.
        let msgs = [];
        try { msgs = this.buildOpenMessages() || []; } catch (_) { msgs = []; }
        for (const m of msgs) {
          try { ws.send(typeof m === 'string' ? m : JSON.stringify(m)); } catch (_) {}
        }
        this._startHeartbeat();
      };

      ws.onmessage = (ev) => {
        this._lastActivity = this._now();
        let data = ev.data;
        if (typeof data === 'string') {
          try { data = JSON.parse(data); } catch (_) { /* не-JSON */ }
        }
        if (this.isPong(data)) return; // pong только обновляет активность
        try { this.onMessage(data, ev); } catch (_) {}
      };

      ws.onerror = (e) => { this._lastError = { type: 'ws_error', detail: e && e.message || 'ws_error' }; };

      ws.onclose = (ev) => {
        this._lastCloseCode = ev ? ev.code : null;
        this._lastCloseReason = ev ? (ev.reason || '') : '';
        this._stopHeartbeat();
        if (this.ws === ws) this.ws = null;
        if (this._stopped || TERMINAL.has(this.state) || this.state === STATES.MICROPHONE_ERROR) return;
        this._scheduleReconnect();
      };
    }

    _startHeartbeat() {
      this._stopHeartbeat();
      this._heartbeatTimer = this._timers.setInterval(() => {
        // Stale-watchdog: нет ни одного сообщения дольше staleTimeout → сокет мёртв.
        if (this._isStale()) {
          this._forceReconnect('stale_watchdog');
          return;
        }
        try { if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify({ type: 'ping' })); } catch (_) {}
      }, this.heartbeatInterval);
    }

    _stopHeartbeat() {
      if (this._heartbeatTimer) { this._timers.clearInterval(this._heartbeatTimer); this._heartbeatTimer = null; }
    }

    _isStale() {
      if (this._lastActivity == null) return false;
      return (this._now() - this._lastActivity) > this.staleTimeout;
    }

    _forceReconnect(reason) {
      this._stopHeartbeat();
      this._closeSocket(4001, reason);
      this.ws = null;
      this._scheduleReconnect(reason);
    }

    _scheduleReconnect(reason) {
      this._clearReconnect();
      if (this._stopped) return;
      if (!this._isOnline()) { this._setState(STATES.OFFLINE, { reason: 'offline' }); return; }
      if (this.retries >= this.maxRetries) {
        this._setState(STATES.CLOSED, { reason: 'max_retries' });
        return;
      }
      const delay = this._backoffDelay(this.retries);
      this.retries += 1;
      this._setState(STATES.RECONNECTING, { attempt: this.retries, delay, reason: reason || null });
      this._reconnectTimer = this._timers.setTimeout(() => {
        this._reconnectTimer = null;
        this._connect();
      }, delay);
    }

    _backoffDelay(attempt) {
      const exp = Math.min(this.maxDelay, this.baseDelay * Math.pow(2, attempt));
      const jitter = exp * 0.5 * this._rand(); // до 50% jitter
      return Math.round(exp * 0.5 + jitter);   // [50%..100%] от exp
    }

    _clearReconnect() {
      if (this._reconnectTimer) { this._timers.clearTimeout(this._reconnectTimer); this._reconnectTimer = null; }
    }

    _closeSocket(code, reason) {
      const ws = this.ws;
      if (!ws) return;
      try {
        if (ws.readyState === 1 || ws.readyState === 0) ws.close(code, reason);
      } catch (_) {}
    }
  }

  return { VoxConnection, STATES, TERMINAL };
});
