(() => {
  // Read only HTMLMediaElement playback state. Never inspect forms, text inputs,
  // keystrokes, mouse events, media URLs or arbitrary page text.
  function report() {
    if (!chrome.runtime?.id) return;
    const media = [...document.querySelectorAll('audio, video')];
    const detected = media.length > 0;
    const playing = !document.hidden && media.some(item => !item.paused && !item.ended && !item.seeking && item.readyState >= 3);
    try {
      const result = chrome.runtime.sendMessage({type: 'media', detected, playing});
      result?.catch(() => {});
    } catch { /* Extension was reloaded or this frame is unavailable. */ }
  }
  for (const event of ['playing', 'pause', 'ended', 'emptied', 'waiting', 'loadeddata']) {
    document.addEventListener(event, report, true);
  }
  document.addEventListener('visibilitychange', report);
  setInterval(() => { if (!document.hidden) report(); }, 1000);
  report();
})();
