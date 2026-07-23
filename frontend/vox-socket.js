/** WebSocket-compatible adapter backed by VoxConnection. */
(function (root, factory) {
  const api = typeof module === 'object' && module.exports
    ? require('./vox-connection.js')
    : { VoxConnection: root.VoxConnection, STATES: root.VOX_CONN_STATES };
  const exported = factory(api, root);
  if (typeof module === 'object' && module.exports) module.exports = exported;
  else root.VoxSocket = exported.VoxSocket;
})(typeof self !== 'undefined' ? self : globalThis, function (api, root) {
  'use strict';

  const { VoxConnection, STATES } = api;

  class VoxSocket {
    constructor(url, options) {
      options = options || {};
      this.url = url;
      this.binaryType = 'arraybuffer';
      this.onopen = null;
      this.onmessage = null;
      this.onerror = null;
      this.onclose = null;
      this.onstatechange = null;
      this.connectionState = STATES.IDLE;
      this._closedEventSent = false;
      this._lifecycle = [];

      const connectionOptions = Object.assign({}, options.connectionOptions || {}, {
        url,
        binaryType: this.binaryType,
        buildOpenMessages: options.buildOpenMessages || function () { return []; },
        onState: (state, info) => this._handleState(state, info),
        onMessage: (data, event) => this._handleMessage(data, event),
      });
      this.connection = new VoxConnection(connectionOptions);
      this._installLifecycle();

      if (options.autoStart !== false) {
        const defer = options.defer || (fn => setTimeout(fn, 0));
        defer(() => this.connection.start());
      }
    }

    get readyState() {
      if (this.connectionState === STATES.CONNECTED) return VoxSocket.OPEN;
      if (this.connectionState === STATES.CONNECTING || this.connectionState === STATES.RECONNECTING) return VoxSocket.CONNECTING;
      return VoxSocket.CLOSED;
    }

    send(data) { return this.connection.send(data); }
    resume(reason) { this.connection.resume(reason || 'manual_resume'); }
    getDiagnostics() { return this.connection.getDiagnostics(); }

    close(code, reason) {
      void code;
      this._removeLifecycle();
      this.connection.stop(reason || 'client_stop');
    }

    _handleState(state, info) {
      this.connectionState = state;
      if (root && root.__voxActiveSocket === this) root.__voxActiveSocket = this;
      if (typeof this.onstatechange === 'function') {
        try { this.onstatechange({ state, detail: info || {} }); } catch (_) {}
      }
      if (state === STATES.CONNECTED) {
        this._closedEventSent = false;
        if (root) root.__voxActiveSocket = this;
        if (typeof this.onopen === 'function') {
          try { this.onopen({ type: 'open' }); } catch (_) {}
        }
      } else if (state === STATES.CLOSED && !this._closedEventSent) {
        this._closedEventSent = true;
        if (typeof this.onclose === 'function') {
          try { this.onclose({ code: info && info.code || 1000, reason: info && info.reason || '' }); } catch (_) {}
        }
      }
    }

    _handleMessage(data, event) {
      const code = data && typeof data === 'object' && (data.code || data.reason);
      const msgType = data && typeof data === 'object' && data.type;
      if (typeof this.onmessage === 'function') {
        const payload = data instanceof ArrayBuffer || typeof data === 'string'
          ? data
          : JSON.stringify(data);
        try { this.onmessage({ data: payload, originalEvent: event }); } catch (_) {}
      }
      if (code === 'insufficient_balance' || code === 'no_balance') {
        this.connection.markTerminal(STATES.INSUFFICIENT_BALANCE, { reason: code });
      } else if (code === 'email_verification_required') {
        this.connection.markTerminal(STATES.EMAIL_VERIFICATION_REQUIRED, { reason: code });
      } else if (code === 'auth_required') {
        this.connection.markTerminal(STATES.AUTH_REQUIRED, { reason: code });
      } else if (code === 'billing_unavailable') {
        this.connection.markTerminal(STATES.BILLING_UNAVAILABLE, { reason: code });
      } else if (msgType === 'host_left') {
        // Хост явно завершил сессию — reconnect не нужен
        this.connection.markTerminal(STATES.CLOSED, { reason: 'host_left' });
      }
    }

    _listen(target, name, handler) {
      if (!target || !target.addEventListener) return;
      target.addEventListener(name, handler);
      this._lifecycle.push([target, name, handler]);
    }

    _installLifecycle() {
      const win = root && root.addEventListener ? root : null;
      const doc = root && root.document;
      this._listen(win, 'online', () => this.connection.notifyOnline());
      this._listen(win, 'offline', () => this.connection.notifyOffline());
      this._listen(win, 'pageshow', () => this.connection.resume('pageshow'));
      this._listen(doc, 'visibilitychange', () => {
        if (doc.visibilityState === 'visible') this.connection.resume('visibilitychange');
      });
    }

    _removeLifecycle() {
      for (const [target, name, handler] of this._lifecycle) {
        try { target.removeEventListener(name, handler); } catch (_) {}
      }
      this._lifecycle.length = 0;
    }
  }

  VoxSocket.CONNECTING = 0;
  VoxSocket.OPEN = 1;
  VoxSocket.CLOSING = 2;
  VoxSocket.CLOSED = 3;

  if (root) {
    root._voxConnDiag = root._voxConnDiag || function () {
      return root.__voxActiveSocket ? root.__voxActiveSocket.getDiagnostics() : {};
    };
  }

  return { VoxSocket };
});
