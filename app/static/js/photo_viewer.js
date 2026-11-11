// Storage photo viewer (the photo_viewer() macro in _package.html): tap the photo
// to see it full screen. Used by the check-out and package detail pages.

const photoViewer = document.getElementById("photo-viewer");
if (photoViewer) {
  document.getElementById("storage-photo-open").addEventListener("click", () => photoViewer.showModal());
  document.getElementById("photo-viewer-close").addEventListener("click", () => photoViewer.close());
  // A tap on the black area around the photo closes it too; <dialog> handles Escape
  photoViewer.addEventListener("click", (event) => {
    if (event.target === photoViewer) photoViewer.close();
  });
}
