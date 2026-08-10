
window.onerror = function(msg, url, line, col, error) {
  alert("JS Error:\n" + msg + "\nLine: " + line + "\nCol: " + col);
  return false;
};
window.addEventListener('unhandledrejection', function(e) {
  alert("Promise Error:\n" + (e.reason && e.reason.message ? e.reason.message : e.reason));
});
