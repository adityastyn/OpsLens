/**
 * Small polyfills for the older Chromium/WebKit browsers built into HMI panels
 * and EcoStruxure Operator Terminal Expert's Web Browser object.
 *
 * The code uses a few newer built-ins (Array#at, String#replaceAll, ...). On a
 * browser without them the chart code threw while drawing, so the trend charts
 * and the knowledge graph did not appear. Each one is added only if missing.
 * Import this file first, before anything else.
 */
/* eslint-disable no-extend-native */
const define = (proto, name, fn) => {
  if (proto && typeof proto[name] !== 'function') {
    Object.defineProperty(proto, name, { value: fn, writable: true, configurable: true, enumerable: false });
  }
};

function at(index) {
  const length = this.length >>> 0;
  let i = Math.trunc(Number(index)) || 0;
  if (i < 0) i += length;
  return i < 0 || i >= length ? undefined : this[i];
}
define(Array.prototype, 'at', at);
define(String.prototype, 'at', at);
if (typeof Int8Array !== 'undefined') define(Object.getPrototypeOf(Int8Array.prototype), 'at', at);

define(String.prototype, 'replaceAll', function replaceAll(search, replacement) {
  if (search instanceof RegExp) {
    if (!search.global) throw new TypeError('replaceAll must be called with a global RegExp');
    return this.replace(search, replacement);
  }
  return this.split(String(search)).join(typeof replacement === 'function' ? replacement(String(search)) : String(replacement));
});

define(Array.prototype, 'flat', function flat(depth = 1) {
  const out = [];
  const walk = (arr, d) => arr.forEach((v) => (Array.isArray(v) && d > 0 ? walk(v, d - 1) : out.push(v)));
  walk(this, Math.trunc(depth) || 0);
  return out;
});
define(Array.prototype, 'flatMap', function flatMap(fn, thisArg) {
  return Array.prototype.map.call(this, fn, thisArg).flat(1);
});
define(Array.prototype, 'findLast', function findLast(fn, thisArg) {
  for (let i = this.length - 1; i >= 0; i -= 1) if (fn.call(thisArg, this[i], i, this)) return this[i];
  return undefined;
});
define(Array.prototype, 'findLastIndex', function findLastIndex(fn, thisArg) {
  for (let i = this.length - 1; i >= 0; i -= 1) if (fn.call(thisArg, this[i], i, this)) return i;
  return -1;
});

if (typeof Object.fromEntries !== 'function') {
  Object.fromEntries = (entries) => {
    const out = {};
    for (const [k, v] of entries) out[k] = v;
    return out;
  };
}
if (typeof Object.hasOwn !== 'function') {
  Object.hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
}

if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout !== 'function' && typeof AbortController !== 'undefined') {
  AbortSignal.timeout = (ms) => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), ms);
    return controller.signal;
  };
}

if (typeof Promise.allSettled !== 'function') {
  Promise.allSettled = (items) => Promise.all(Array.from(items, (p) => Promise.resolve(p).then(
    (value) => ({ status: 'fulfilled', value }),
    (reason) => ({ status: 'rejected', reason }),
  )));
}
