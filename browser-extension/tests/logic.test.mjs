import assert from 'node:assert/strict';
import {test} from 'node:test';
import {buildSnapshot, mediaEvidence, siteOrigin} from '../logic.js';

const now = 1800000000000;
const window = {type: 'normal', id: 77, focused: true, incognito: false,
  tabs: [{id: 111, active: true, incognito: false, url: 'https://user:secret@example.com/private?q=password#token', title: 'Example'}]};
const options = {sequence: 1, now, navigation: () => 'c'.repeat(32)};

test('origins discard credentials, page paths, queries and fragments', () => {
  assert.equal(siteOrigin(window.tabs[0].url), 'https://example.com');
  for (const url of ['chrome://history', 'edge://settings', 'file:///secret', 'about:blank', 'not a URL']) {
    assert.equal(siteOrigin(url), null);
  }
});

test('active snapshot transmits origin-only metadata and browser IDs', () => {
  const result = buildSnapshot([window], options);
  assert.equal(result.page.window_id, 77);
  assert.equal(result.page.tab_id, 111);
  assert.equal(result.page.origin, 'https://example.com');
  assert.equal(result.page.title, 'Example');
  assert.equal(JSON.stringify(result).includes('password'), false);
  assert.equal(JSON.stringify(result).includes('secret'), false);
  assert.deepEqual(result.page.media, {detected: false, playing: false, age_ms: 0});
});

test('unfocused and private windows never transmit page information', () => {
  assert.equal(buildSnapshot([{...window, focused: false}], options).page, null);
  assert.equal(buildSnapshot([{...window, incognito: true}], options).page, null);
  const privateTab = {...window, tabs: [{...window.tabs[0], incognito: true}]};
  assert.equal(buildSnapshot([privateTab], options).page, null);
});

test('internal pages never become content or media evidence', () => {
  const internal = {...window, tabs: [{...window.tabs[0], url: 'chrome://settings'}]};
  assert.equal(buildSnapshot([internal], options).page, null);
});

test('all normal windows are counted even if only one is focused', () => {
  const background = {...window, id: 88, focused: false,
    tabs: [{...window.tabs[0], id: 222, title: 'Background', url: 'https://background.example'}]};
  const result = buildSnapshot([window, background, {type: 'popup', focused: false}], options);
  assert.equal(result.window_count, 2);
  assert.equal(result.page.window_id, 77);
  assert.equal(result.page.tab_id, 111);
  assert.equal(result.page.origin, 'https://example.com');
});

test('audio permission/audible flags cannot imply playback', () => {
  const audible = {...window, tabs: [{...window.tabs[0], audible: true}]};
  assert.equal(buildSnapshot([audible], options).page.media.playing, false);
});

test('media evidence expires and requires observed HTML media', () => {
  assert.deepEqual(mediaEvidence([{detected: true, playing: true, observedAt: now - 100}], now),
    {detected: true, playing: true, age_ms: 100});
  assert.equal(mediaEvidence([{detected: true, playing: true, observedAt: now - 2501}], now).playing, false);
  assert.equal(mediaEvidence([{detected: false, playing: true, observedAt: now}], now).playing, false);
  assert.equal(mediaEvidence([{detected: true, playing: true, observedAt: now + 1}], now).playing, false);
});

test('only the selected tab supplies media evidence', () => {
  const result = buildSnapshot([window], {...options, frames: [{tabId: 999, detected: true, playing: true, observedAt: now}]});
  assert.equal(result.page.media.playing, false);
});
