/**
 * Node-тесты state machine реконнекта (frontend/vox-connection.js).
 * Запуск: node tests/js/test_vox_connection.js
 * Использует поддельные таймеры и поддельный WebSocket — без браузера.
 */
const assert = require('assert');
const path = require('path');
const { VoxConnection, STATES } = require(path.join(__dirname, '..', '..', 'frontend', 'vox-connection.js'));

// --- Поддельные часы/таймеры ---
class Clock {
  constructor() { this.t = 0; this.id = 1; this.timeouts = new Map(); this.intervals = new Map(); }
  now() { return this.t; }
  setTimeout(fn, ms) { const id = this.id++; this.timeouts.set(id, { fn, at: this.t + ms }); return id; }
  clearTimeout(id) { this.timeouts.delete(id); }
  setInterval(fn, ms) { const id = this.id++; this.intervals.set(id, { fn, ms, next: this.t + ms }); return id; }
  clearInterval(id) { this.intervals.delete(id); }
  tick(ms) {
    const target = this.t + ms;
    for (;;) {
      let earliest = null, kind = null, key = null;
      for (const [id, o] of this.timeouts) if (o.at <= target && (earliest === null || o.at < earliest)) { earliest = o.at; kind = 'to'; key = id; }
      for (const [id, o] of this.intervals) if (o.next <= target && (earliest === null || o.next < earliest)) { earliest = o.next; kind = 'iv'; key = id; }
      if (earliest === null) break;
      this.t = earliest;
      if (kind === 'to') { const o = this.timeouts.get(key); this.timeouts.delete(key); o.fn(); }
      else { const o = this.intervals.get(key); o.next = earliest + o.ms; o.fn(); }
    }
    this.t = target;
  }
}

// --- Поддельный WebSocket ---
class FakeWS {
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; this.binaryType = ''; FakeWS.instances.push(this); }
  send(d) { this.sent.push(d); }
  close(code, reason) { if (this.readyState === 3) return; this.readyState = 3; if (this.onclose) this.onclose({ code: code || 1000, reason: reason || '' }); }
  _open() { this.readyState = 1; if (this.onopen) this.onopen(); }
  _msg(data) { if (this.onmessage) this.onmessage({ data }); }
  _serverClose(code, reason) { if (this.readyState === 3) return; this.readyState = 3; if (this.onclose) this.onclose({ code, reason: reason || '' }); }
}

function makeConn(extra) {
  FakeWS.instances = [];
  const clock = new Clock();
  const states = [];
  const conn = new VoxConnection(Object.assign({
    url: 'ws://test/ws',
    WebSocketImpl: FakeWS,
    timers: {
      setTimeout: (fn, ms) => clock.setTimeout(fn, ms),
      clearTimeout: (id) => clock.clearTimeout(id),
      setInterval: (fn, ms) => clock.setInterval(fn, ms),
      clearInterval: (id) => clock.clearInterval(id),
    },
    now: () => clock.now(),
    isOnline: () => true,
    random: () => 0.5,
    onState: (s) => states.push(s),
    buildOpenMessages: () => [{ type: 'auth', token: 'T' }, { type: 'config', target_lang: 'de' }],
    heartbeatInterval: 15000,
    staleTimeout: 35000,
    baseDelay: 500,
    maxDelay: 15000,
    maxRetries: 6,
  }, extra || {}));
  return { conn, clock, states, FakeWS };
}

let passed = 0;
function test(name, fn) {
  try { fn(); console.log('  ok -', name); passed++; }
  catch (e) { console.error('  FAIL -', name, '\n', e && e.stack || e); process.exitCode = 1; }
}

// 1. Подключение и повторная отправка auth/config
test('connect -> connected resends auth+config', () => {
  const { conn, FakeWS } = makeConn();
  conn.start();
  assert.strictEqual(FakeWS.instances.length, 1);
  assert.strictEqual(conn.state, STATES.CONNECTING);
  FakeWS.instances[0]._open();
  assert.strictEqual(conn.state, STATES.CONNECTED);
  const sent = FakeWS.instances[0].sent.map(JSON.parse);
  assert.ok(sent.some(m => m.type === 'auth'));
  assert.ok(sent.some(m => m.type === 'config'));
});

// 2. Single-connection guard
test('start twice keeps single socket', () => {
  const { conn, FakeWS } = makeConn();
  conn.start();
  conn.start();
  FakeWS.instances[0]._open();
  conn.start();
  assert.strictEqual(FakeWS.instances.length, 1);
});

// 3. Heartbeat шлёт ping
test('heartbeat sends ping', () => {
  const { conn, clock, FakeWS } = makeConn();
  conn.start();
  FakeWS.instances[0]._open();
  FakeWS.instances[0].sent.length = 0;
  clock.tick(15000);
  const sent = FakeWS.instances[0].sent.map(JSON.parse);
  assert.ok(sent.some(m => m.type === 'ping'), 'ping expected');
});

// 4. Stale watchdog -> форс-реконнект (новый сокет)
test('stale watchdog forces reconnect', () => {
  const { conn, clock, FakeWS } = makeConn();
  conn.start();
  FakeWS.instances[0]._open();
  // нет входящих сообщений дольше staleTimeout
  clock.tick(40000);          // сработает watchdog -> закрытие + reconnect-таймер
  clock.tick(20000);          // отрабатывает backoff -> новый connect
  assert.ok(FakeWS.instances.length >= 2, 'expected a new socket after stale');
});

// 5. Любое сообщение обновляет активность (нет ложного stale)
test('incoming message resets staleness', () => {
  const { conn, clock, FakeWS } = makeConn();
  conn.start();
  FakeWS.instances[0]._open();
  for (let i = 0; i < 5; i++) { clock.tick(10000); FakeWS.instances[0]._msg(JSON.stringify({ type: 'transcript', text: 'x' })); }
  assert.strictEqual(FakeWS.instances.length, 1, 'no reconnect while active');
  assert.strictEqual(conn.state, STATES.CONNECTED);
});

// 6. Реконнект с backoff и лимитом попыток -> closed
test('reconnect backoff stops at max retries', () => {
  const { conn, clock, FakeWS } = makeConn();
  conn.start();
  FakeWS.instances[0]._open();
  // сервер закрывает; каждый новый сокет тоже закрывается сервером
  let guard = 0;
  while (conn.state !== STATES.CLOSED && guard++ < 50) {
    const last = FakeWS.instances[FakeWS.instances.length - 1];
    if (last.readyState !== 3) last._serverClose(1006, 'drop');
    clock.tick(20000); // продвинуть backoff-таймер
  }
  assert.strictEqual(conn.state, STATES.CLOSED);
  assert.ok(FakeWS.instances.length >= 6, 'should have retried ~maxRetries times');
});

// 7. stop() отменяет таймеры и не реконнектит
test('stop cancels reconnect', () => {
  const { conn, clock, FakeWS } = makeConn();
  conn.start();
  FakeWS.instances[0]._open();
  conn.stop('logout');
  assert.strictEqual(conn.state, STATES.CLOSED);
  const n = FakeWS.instances.length;
  clock.tick(120000);
  assert.strictEqual(FakeWS.instances.length, n, 'no new sockets after stop');
});

// 8. Терминальное состояние insufficient_balance не реконнектит
test('terminal insufficient_balance: no reconnect', () => {
  const { conn, clock, FakeWS } = makeConn();
  conn.start();
  FakeWS.instances[0]._open();
  conn.markTerminal(STATES.INSUFFICIENT_BALANCE, { reason: 'no_balance' });
  assert.strictEqual(conn.state, STATES.INSUFFICIENT_BALANCE);
  const n = FakeWS.instances.length;
  clock.tick(120000);
  assert.strictEqual(FakeWS.instances.length, n);
});

// 9. backoff растёт и в пределах [50%..100%] экспоненты
test('backoff increases within jitter bounds', () => {
  const { conn } = makeConn();
  const d0 = conn._backoffDelay(0);
  const d1 = conn._backoffDelay(1);
  const d2 = conn._backoffDelay(2);
  assert.ok(d0 <= d1 && d1 <= d2, 'monotonic');
  // attempt 2: exp=min(15000, 500*4)=2000; диапазон [1000..2000]
  assert.ok(d2 >= 1000 && d2 <= 2000, 'within bounds: ' + d2);
});

// 10. offline при попытке reconnect -> состояние offline
test('reconnect while offline -> offline state', () => {
  const { conn, clock, FakeWS, states } = makeConn({ isOnline: () => false });
  conn.start();
  assert.strictEqual(conn.state, STATES.OFFLINE);
  assert.strictEqual(FakeWS.instances.length, 0);
});

console.log(`\nvox-connection: ${passed} tests passed`);
