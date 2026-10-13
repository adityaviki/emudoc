// Apply a saved dark choice before first paint; light is the default. This is a file rather
// than a script inline in index.html so that the page's Content-Security-Policy, which the
// backend sends, can allow no inline script at all.
try {
  if (localStorage.getItem('theme') === 'dark') document.documentElement.dataset.theme = 'dark'
} catch {}
