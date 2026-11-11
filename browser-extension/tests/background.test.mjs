import assert from 'node:assert/strict';
import {test} from 'node:test';

test('worker snapshots, navigation, media, disconnect and reconnection use the protocol', async () => {
  const timers = [], intervals = [], ports = [];
  const event = () => ({listeners: [], addListener(callback) { this.listeners.push(callback); }});
  const port = () => ({onMessage: event(), onDisconnect: event(), messages: [], postMessage(value) { this.messages.push(value); }});
  let windows = [{id: 7, type: 'normal', focused: true, incognito: false,
    tabs: [{id: 11, active: true, incognito: false, url: 'https://example.com/private?secret=1', title: 'Example'}]}];
  let documentId = 'document';
  globalThis.chrome = {
    action: {setBadgeText() {}, setBadgeBackgroundColor() {}, setTitle() {}, onClicked: event()},
    runtime: {id: 'a'.repeat(32), onMessage: event(), onStartup: event(), onInstalled: event(),
      connectNative(name) { assert.equal(name, 'com.tocktail.bridge'); const value = port(); ports.push(value); return value; }, openOptionsPage() {}},
    windows: {getAll: async (options) => { assert.equal(options.populate, false); return structuredClone(windows); },
      onFocusChanged: event(), onCreated: event(), onRemoved: event()},
    tabs: {query: async (options) => { assert.equal(options.active, true); assert.equal(options.windowId, 7);
      return windows[0].tabs; }, onActivated: event(), onUpdated: event(), onRemoved: event(), onReplaced: event()},
    alarms: {onAlarm: event(), create() {}},
    webNavigation: {getAllFrames: async () => [{frameId: 0, documentId}]}
  };
  const realTimeout = globalThis.setTimeout, realInterval = globalThis.setInterval;
  globalThis.setTimeout = callback => { timers.push(callback); return timers.length; };
  globalThis.setInterval = callback => { intervals.push(callback); return intervals.length; };
  const flush = async () => {
    while (timers.length) { timers.shift()(); await Promise.resolve(); await Promise.resolve(); }
  };
  try {
    await import('../background.js');
    await flush();
    assert.equal(ports[0].messages[0].page.origin, 'https://example.com');
    const first = ports[0].messages.at(-1).page.navigation;
    chrome.tabs.onUpdated.listeners[0](11, {url: 'https://example.com/new?token=secret'});
    await flush();
    assert.notEqual(ports[0].messages.at(-1).page.navigation, first);
    const sender = {id: chrome.runtime.id, tab: windows[0].tabs[0], frameId: 0,
      url: 'https://example.com/private', documentId: 'document', documentLifecycle: 'active'};
    chrome.runtime.onMessage.listeners[0]({type: 'media', detected: true, playing: true}, sender, () => {});
    await flush();
    assert.equal(ports[0].messages.at(-1).page.media.playing, true);
    documentId = 'new-document';
    intervals[0]();
    await flush();
    assert.equal(ports[0].messages.at(-1).page.media.playing, false);
    windows = [{...windows[0], focused: false}];
    chrome.windows.onFocusChanged.listeners[0](-1);
    await flush();
    assert.equal(ports[0].messages.at(-1).page, null);
    ports[0].onDisconnect.listeners[0]();
    await flush();
    assert.equal(ports.length, 2);
    assert.equal(ports[1].messages.length, 1);
    assert.equal(JSON.stringify(ports.flatMap(item => item.messages)).includes('secret'), false);
  } finally {
    globalThis.setTimeout = realTimeout; globalThis.setInterval = realInterval;
    delete globalThis.chrome;
  }
});
