// Confirm Pickup page: stop a double tap on Mark as Picked Up from sending the form twice
// (the server only checks a package out once anyway; this avoids a second request),
// and show the storage photo full screen when it's tapped.

const pickupForm = document.getElementById("pickup-form");
const pickupButton = document.getElementById("pickup-button");
const pickupLabel = pickupButton.textContent;

pickupForm.addEventListener("submit", () => {
  pickupButton.disabled = true;
  pickupButton.setAttribute("aria-busy", "true"); // components.css shows a spinner
  pickupButton.textContent = "Saving…";
});

// Coming back with the Back button can restore the page with the button still disabled
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    pickupButton.disabled = false;
    pickupButton.removeAttribute("aria-busy");
    pickupButton.textContent = pickupLabel;
  }
});

// Storage photo viewer: only on the page when the package has a photo
const photoViewer = document.getElementById("photo-viewer");
if (photoViewer) {
  document.getElementById("storage-photo-open").addEventListener("click", () => photoViewer.showModal());
  document.getElementById("photo-viewer-close").addEventListener("click", () => photoViewer.close());
  // A tap on the black area around the photo closes it too; <dialog> handles Escape
  photoViewer.addEventListener("click", (event) => {
    if (event.target === photoViewer) photoViewer.close();
  });
}
