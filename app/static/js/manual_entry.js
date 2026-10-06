// "Can't scan?" fallback on the check-in page: reveals a tracking number form
// that goes to the same confirm page as a successful scan.

const manualToggle = document.getElementById("manual-entry-toggle");
const manualForm = document.getElementById("manual-entry");
const manualInput = document.getElementById("manual-tracking");

manualToggle.addEventListener("click", (event) => {
  event.preventDefault();
  manualForm.hidden = !manualForm.hidden;
  manualToggle.setAttribute("aria-expanded", String(!manualForm.hidden));
  if (!manualForm.hidden) {
    manualInput.focus();
    manualForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
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
