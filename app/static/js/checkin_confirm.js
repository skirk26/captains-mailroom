// Confirm Check-In page: stop a double tap on Save from sending the form twice.
// (The server rejects a duplicate tracking number anyway; this avoids the error.)

const checkinForm = document.getElementById("checkin-form");
const saveButton = document.getElementById("save-button");
const saveLabel = saveButton.textContent;

// Only fires once the browser's own checks (required, box pattern) have passed
checkinForm.addEventListener("submit", () => {
  saveButton.disabled = true;
  saveButton.textContent = "Saving…";
});

// Coming back with the Back button can restore the page with the button still disabled
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    saveButton.disabled = false;
    saveButton.textContent = saveLabel;
  }
});
