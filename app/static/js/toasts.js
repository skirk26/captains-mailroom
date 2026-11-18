// Toasts (the toasts() macro in _macros.html): brief confirmations at the top of
// the screen, such as "Picked up: …23456784." Each hides itself after 3 seconds,
// or straight away when its close button is tapped. Nothing waits on them, so
// the scanner underneath stays ready for the next package.

const TOAST_MS = 3000;

document.querySelectorAll(".app-toast").forEach((toast) => {
  const dismiss = () => toast.remove();
  toast.querySelector(".app-toast-close").addEventListener("click", dismiss);
  setTimeout(dismiss, TOAST_MS);
});
