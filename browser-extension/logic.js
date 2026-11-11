// Pure helpers shared by the service worker and dependency-free Node tests.
export function siteOrigin(raw) {
  try {
    const url = new URL(raw);
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.hostname.includes(':')) return null;
    // Credentials, paths, queries and fragments never cross the extension boundary.
    return url.origin;
  } catch { return null; }
}

export function mediaEvidence(frames, now) {
  const fresh = frames.filter(frame => now >= frame.observedAt && now - frame.observedAt <= 2500 && frame.detected);
  const playing = fresh.filter(frame => frame.playing);
  const evidence = playing.length ? playing : fresh;
  return {detected: fresh.length > 0, playing: playing.length > 0,
    age_ms: evidence.length ? Math.min(...evidence.map(frame => now - frame.observedAt)) : 0};
}

export function buildSnapshot(windows, {sequence, now, navigation, frames = []}) {
  const normal = windows.filter(window => window.type === 'normal');
  const focused = normal.filter(window => window.focused);
  const result = {version: 1, sequence, timestamp_ms: now, focused: focused.length === 1,
    window_count: Math.min(normal.length, 128), page: null};
  if (focused.length !== 1 || normal.length > 128) return result;
  const window = focused[0];
  const active = (window.tabs || []).filter(tab => tab.active);
  if (window.incognito || active.length !== 1 || active[0].incognito) return result;
  const tab = active[0];
  const origin = siteOrigin(tab.url);
  if (!origin || !Number.isSafeInteger(tab.id) || !Number.isSafeInteger(window.id)) return result;
  result.page = {window_id: window.id, tab_id: tab.id, navigation: navigation(tab.id), origin,
    title: String(tab.title || '').replaceAll('\0', '').slice(0, 2048),
    media: mediaEvidence(frames.filter(frame => frame.tabId === tab.id), now)};
  return result;
}
