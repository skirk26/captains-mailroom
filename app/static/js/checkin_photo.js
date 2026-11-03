// Storage-photo screen: opens the phone camera, shrinks the photo in the
// browser, and previews it. Phone photos are several MB and the mailroom Wi-Fi
// may be slow, so only the shrunken JPEG (photoBlob) is ever kept; the upload
// step will send that.

const MAX_SIDE = 1600;     // longest side of the saved photo, in pixels
const JPEG_QUALITY = 0.82;

const capture = document.getElementById("photo-capture");
const input = document.getElementById("photo-input");
const preview = document.getElementById("photo-preview");
const errorBox = document.getElementById("photo-error");
const errorText = document.getElementById("photo-error-text");

let photoBlob = null;  // the shrunken JPEG, held in memory until the upload step
let previewUrl = null; // object URL for photoBlob; revoked when the photo is replaced

function setState(state) {
  capture.dataset.state = state; // style.css shows/hides parts of the screen per state
}

function showError(message) {
  errorText.textContent = message;
  errorBox.hidden = false;
}

// ---- Rotation ----
// Phones save photos sideways plus an EXIF "orientation" tag saying how to turn
// them. Current browsers apply it when drawing an image; older ones don't, so
// for those the tag is read here and the canvas is turned by hand.
const BROWSER_APPLIES_ORIENTATION =
  typeof CSS !== "undefined" && CSS.supports && CSS.supports("image-orientation", "from-image");

async function readExifOrientation(file) {
  try {
    const view = new DataView(await file.slice(0, 128 * 1024).arrayBuffer());
    if (view.byteLength < 4 || view.getUint16(0) !== 0xffd8) return 1; // not a JPEG
    let offset = 2;
    while (offset + 4 <= view.byteLength) {
      const marker = view.getUint16(offset);
      if ((marker & 0xff00) !== 0xff00) break;
      // APP1 segment starting "Exif"
      if (marker === 0xffe1 && view.getUint32(offset + 4) === 0x45786966) {
        const tiff = offset + 10;
        const little = view.getUint16(tiff) === 0x4949; // "II" = little-endian
        const ifd = tiff + view.getUint32(tiff + 4, little);
        const entries = view.getUint16(ifd, little);
        for (let i = 0; i < entries; i++) {
          const entry = ifd + 2 + i * 12;
          if (view.getUint16(entry, little) === 0x0112) return view.getUint16(entry + 8, little);
        }
        return 1;
      }
      offset += 2 + view.getUint16(offset + 2);
    }
  } catch (err) {
    // A truncated or unusual header: treat it as upright
  }
  return 1;
}

// Canvas transform for each EXIF orientation (w, h = the canvas size)
function orientationTransform(orientation, w, h) {
  switch (orientation) {
    case 2: return [-1, 0, 0, 1, w, 0];
    case 3: return [-1, 0, 0, -1, w, h];
    case 4: return [1, 0, 0, -1, 0, h];
    case 5: return [0, 1, 1, 0, 0, 0];
    case 6: return [0, 1, -1, 0, w, 0];
    case 7: return [0, -1, -1, 0, w, h];
    case 8: return [0, -1, 1, 0, 0, h];
    default: return [1, 0, 0, 1, 0, 0];
  }
}

// ---- Shrinking ----
function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("The browser couldn't decode this image"));
    };
    img.src = url;
  });
}

function canvasToJpeg(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("JPEG export failed"))),
      "image/jpeg",
      JPEG_QUALITY,
    );
  });
}

async function shrinkPhoto(file) {
  const orientation = BROWSER_APPLIES_ORIENTATION ? 1 : await readExifOrientation(file);
  const img = await loadImage(file);

  const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
  const drawWidth = Math.round(img.naturalWidth * scale);
  const drawHeight = Math.round(img.naturalHeight * scale);
  const turned = orientation >= 5; // 5-8 swap width and height

  const canvas = document.createElement("canvas");
  canvas.width = turned ? drawHeight : drawWidth;
  canvas.height = turned ? drawWidth : drawHeight;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(...orientationTransform(orientation, canvas.width, canvas.height));
  ctx.drawImage(img, 0, 0, drawWidth, drawHeight);

  const blob = await canvasToJpeg(canvas);
  canvas.width = canvas.height = 0; // release the canvas memory now, not at GC
  return blob;
}

// ---- Screen ----
function showPreview(blob) {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  photoBlob = blob;
  previewUrl = URL.createObjectURL(blob);
  preview.src = previewUrl;
  setState("preview");
}

// "Take Photo" and "Retake" both open the camera
document.querySelectorAll(".photo-take").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = ""; // so choosing the same file again still fires "change"
    input.click();
  });
});

input.addEventListener("change", async () => {
  const file = input.files && input.files[0];
  if (!file) return; // camera cancelled: keep whatever was showing

  const previousState = capture.dataset.state;
  errorBox.hidden = true;
  setState("processing");
  try {
    showPreview(await shrinkPhoto(file));
  } catch (err) {
    console.error("Couldn't prepare photo:", err);
    setState(previousState);
    showError("We couldn't read that photo. Try taking it again, or skip for now.");
  }
});

// Free the photo when leaving for good (not when the page is kept for Back)
window.addEventListener("pagehide", (event) => {
  if (previewUrl && !event.persisted) URL.revokeObjectURL(previewUrl);
});
