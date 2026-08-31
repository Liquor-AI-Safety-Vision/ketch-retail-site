// Keep the pre-paint theme without allowing executable inline scripts in CSP.
try {
  document.documentElement.dataset.colorMode = localStorage.getItem('ketch.color-mode') === 'dark' ? 'dark' : 'light';
} catch {
  // The default light theme also works when browser storage is unavailable.
}
