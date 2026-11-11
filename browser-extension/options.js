async function refresh() {
  document.querySelector('#extension-id').textContent = chrome.runtime.id;
  try {
    const result = await chrome.runtime.sendMessage({type: 'connection_status'});
    document.querySelector('#status').textContent = result?.desktop_connected
      ? 'Connected to your desktop app' : 'Waiting for the desktop connection';
  } catch { document.querySelector('#status').textContent = 'Waiting for the browser companion'; }
}
void refresh();
setInterval(refresh, 2000);
