import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const code = readFileSync(new URL('../content.js', import.meta.url), 'utf8');

function environment(media = [], hidden = false) {
  const messages = [], listeners = new Map();
  let heartbeat;
  const document = {hidden, querySelectorAll(selector) {
    assert.equal(selector, 'audio, video');
    return media;
  }, addEventListener(event, listener) { listeners.set(event, listener); }};
  vm.runInNewContext(code, {document, chrome: {runtime: {id: 'extension', sendMessage(message) {
    messages.push(JSON.parse(JSON.stringify(message))); return Promise.resolve();
  }}}, setInterval(callback) { heartbeat = callback; }});
  return {document, media, messages, listeners, heartbeat};
}

test('unknown pages report no guessed media', () => {
  assert.deepEqual(environment().messages[0], {type: 'media', detected: false, playing: false});
});

test('playing, paused, stalled, seeking and ended states are distinguished', () => {
  const element = {paused: false, ended: false, seeking: false, readyState: 4};
  const context = environment([element]);
  assert.equal(context.messages.at(-1).playing, true);
  element.paused = true; context.listeners.get('pause')();
  assert.equal(context.messages.at(-1).playing, false);
  element.paused = false; element.readyState = 2; context.listeners.get('waiting')();
  assert.equal(context.messages.at(-1).playing, false);
  element.readyState = 4; element.seeking = true; context.heartbeat();
  assert.equal(context.messages.at(-1).playing, false);
  element.seeking = false; element.ended = true; context.listeners.get('ended')();
  assert.equal(context.messages.at(-1).playing, false);
});

test('hidden documents stop heartbeat activity and clear playing state', () => {
  const context = environment([{paused: false, ended: false, seeking: false, readyState: 4}]);
  context.document.hidden = true;
  context.listeners.get('visibilitychange')();
  assert.equal(context.messages.at(-1).playing, false);
  const count = context.messages.length;
  context.heartbeat(); // The scheduled callback is guarded outside report().
  assert.equal(context.messages.length, count);
});
