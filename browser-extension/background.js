import {buildSnapshot, siteOrigin} from './logic.js';

const HOST = 'com.tocktail.bridge';
const frames = new Map();
const navigations = new Map();
let port = null;
let sequence = 0;
let connected = false;
let scheduled = false;
let inFlight = false;
let dirty = false;

function boundedSet(map, key, value) {
  if (!map.has(key) && map.size >= 256) map.delete(map.keys().next().value);
  map.set(key, value);
}

function token() { return crypto.randomUUID().replaceAll('-', ''); }
function navigation(tabId) {
  if (!navigations.has(tabId)) boundedSet(navigations, tabId, token());
  return navigations.get(tabId);
}

function status(value) {
  connected = value;
  chrome.action.setBadgeText({text: value ? '' : '!'});
  chrome.action.setBadgeBackgroundColor({color: '#7764da'});
  chrome.action.setTitle({title: value ? 'Tocktail — desktop connected' : 'Tocktail — waiting for desktop connection'});
}

async function publish() {
  if (!port) return;
  if (inFlight) { dirty = true; return; }
  inFlight = true;
  const current = port;
  try {
    const windows = await chrome.windows.getAll({populate: false, windowTypes: ['normal']});
    const focused = windows.filter(window => window.type === 'normal' && window.focused && !window.incognito);
    let currentFrames = [];
    if (focused.length === 1) {
      focused[0].tabs = await chrome.tabs.query({active: true, windowId: focused[0].id});
      const tab = focused[0].tabs[0];
      if (tab && !tab.incognito && siteOrigin(tab.url)) {
        try {
          const documents = await chrome.webNavigation.getAllFrames({tabId: tab.id});
          const current = new Map((documents || []).map(frame => [frame.frameId, frame.documentId]));
          currentFrames = [...frames.values()].filter(frame => frame.tabId === tab.id
            && current.get(frame.frameId) === frame.documentId);
        } catch { /* Unknown document identity cannot supply PASSIVE evidence. */ }
      }
    }
    if (port === current) current.postMessage(buildSnapshot(windows, {
      sequence: sequence++, now: Date.now(), navigation, frames: currentFrames
    }));
  } catch {
    if (port === current) {
      try { current.postMessage({version: 1, sequence: sequence++, timestamp_ms: Date.now(),
        focused: false, window_count: 0, page: null}); } catch { status(false); }
    }
  } finally {
    inFlight = false;
    if (dirty) { dirty = false; schedule(); }
  }
}

function schedule() {
  if (scheduled) return;
  scheduled = true;
  setTimeout(() => { scheduled = false; void publish(); }, 50);
}

function connect() {
  if (port) return;
  try {
    const current = chrome.runtime.connectNative(HOST);
    port = current;
    current.onMessage.addListener(message => {
      if (port === current && message?.type === 'status' && typeof message.desktop_connected === 'boolean') {
        status(message.desktop_connected);
      }
    });
    current.onDisconnect.addListener(() => {
      void chrome.runtime.lastError; // Consume only; never log activity/message payloads.
      if (port === current) { port = null; status(false); setTimeout(connect, 5000); }
    });
    schedule();
  } catch { port = null; status(false); }
}

function clearTab(tabId) {
  for (const [key, frame] of frames) if (frame.tabId === tabId) frames.delete(key);
}

chrome.tabs.onActivated.addListener(schedule);
chrome.tabs.onUpdated.addListener((tabId, changes) => {
  if ('url' in changes || changes.status === 'loading') {
    boundedSet(navigations, tabId, token());
    clearTab(tabId);
  }
  schedule();
});
chrome.tabs.onRemoved.addListener(tabId => { navigations.delete(tabId); clearTab(tabId); schedule(); });
chrome.tabs.onReplaced.addListener((added, removed) => {
  navigations.delete(removed); clearTab(removed); boundedSet(navigations, added, token()); schedule();
});
chrome.windows.onFocusChanged.addListener(schedule);
chrome.windows.onCreated.addListener(schedule);
chrome.windows.onRemoved.addListener(schedule);
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id) return false;
  if (message?.type === 'connection_status' && !sender.tab) {
    sendResponse({desktop_connected: connected, extension_id: chrome.runtime.id});
    return false;
  }
  if (message?.type !== 'media' || typeof message.detected !== 'boolean' || typeof message.playing !== 'boolean'
      || !sender.tab?.active || sender.tab.incognito || !siteOrigin(sender.url)
      || !Number.isInteger(sender.frameId) || sender.frameId < 0
      || typeof sender.documentId !== 'string' || !sender.documentId || sender.documentId.length > 128
      || sender.documentLifecycle !== 'active') return false;
  boundedSet(frames, `${sender.tab.id}:${sender.frameId}`, {tabId: sender.tab.id,
    frameId: sender.frameId, documentId: sender.documentId,
    detected: message.detected, playing: message.detected && message.playing,
    observedAt: Date.now()});
  schedule();
  return false;
});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'tocktail-reconnect') connect(); });
chrome.alarms.create('tocktail-reconnect', {periodInMinutes: 0.5});
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
setInterval(() => { if (port) schedule(); }, 1000);
status(false);
connect();
