// Confirm Pickup page: stop a double tap on Mark as Picked Up from sending the form twice.
// (The server only checks a package out once anyway; this avoids a second request.)

const pickupForm = document.getElementById("pickup-form");
const pickupButton = document.getElementById("pickup-button");
const pickupLabel = pickupButton.textContent;

pickupForm.addEventListener("submit", () => {
  pickupButton.disabled = true;
  pickupButton.textContent = "Saving…";
});

// Coming back with the Back button can restore the page with the button still disabled
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    pickupButton.disabled = false;
    pickupButton.textContent = pickupLabel;
  }
});
