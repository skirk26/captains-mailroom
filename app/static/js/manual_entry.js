// "Can't scan?" fallback on the scan screens: a bottom sheet with a tracking
// number field that goes to the same confirm page as a successful scan.
// While it's open the camera pauses (scanner.js listens for scanner:pause and
// scanner:resume), so nothing is decoded behind the sheet.

const sheet = document.getElementById("manual-entry-sheet");
const manualToggle = document.getElementById("manual-entry-toggle");
const manualForm = document.getElementById("manual-entry");
const manualInput = document.getElementById("manual-tracking");

manualToggle.addEventListener("click", () => {
  sheet.showModal();
  manualToggle.setAttribute("aria-expanded", "true");
  document.dispatchEvent(new Event("scanner:pause"));
  manualInput.focus();
});

document.getElementById("manual-entry-close").addEventListener("click", () => sheet.close());

// Tapping the dimmed area outside the panel closes it; Escape is handled by <dialog>
sheet.addEventListener("click", (event) => {
  if (event.target === sheet) sheet.close();
});

sheet.addEventListener("close", () => {
  manualToggle.setAttribute("aria-expanded", "false");
  document.dispatchEvent(new Event("scanner:resume"));
});

manualForm.addEventListener("submit", (event) => {
  manualInput.value = manualInput.value.trim();
  if (!manualInput.value) {
    // `required` alone lets whitespace-only input through
    event.preventDefault();
    manualInput.setCustomValidity("Enter a tracking number.");
    manualInput.reportValidity();
  }
});

manualInput.addEventListener("input", () => manualInput.setCustomValidity(""));
