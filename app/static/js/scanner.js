// Barcode scanning for the check-in page. Relies on html5-qrcode, which
// checkin.html loads from the CDN before this file.

// ---- Supported barcode formats ----
// CODE_128 is what UPS, FedEx, and USPS tracking barcodes use. The others are
// only here for testing with whatever barcode is handy; narrow this list later.
// Fewer formats = faster, more reliable decoding.
const SUPPORTED_FORMATS = [
  Html5QrcodeSupportedFormats.CODE_128,
  Html5QrcodeSupportedFormats.QR_CODE,
  Html5QrcodeSupportedFormats.CODE_39,
  Html5QrcodeSupportedFormats.EAN_13,
  Html5QrcodeSupportedFormats.UPC_A,
];

const SCAN_FPS = 10;

// The library decodes from a canvas sized in layout pixels, not camera pixels.
// Laying the video out this many times larger (then scaling it down to fit)
// gives the decoder more pixels per bar, which long tracking barcodes need.
const RENDER_SCALE = 2;

// Browsers default to 640x480; ask for more so thin bars stay sharp
const VIDEO_CONSTRAINTS = {
  facingMode: "environment",
  width: { ideal: 1920 },
  height: { ideal: 1080 },
};

const page = document.getElementById("checkin");
const viewfinder = document.getElementById("viewfinder");
const reader = document.getElementById("reader");
const scanFrame = document.getElementById("scan-frame");
const messageTitle = document.getElementById("scan-message-title");
const messageBody = document.getElementById("scan-message-body");
const resultText = document.getElementById("scan-result-text");
const scanAgainButton = document.getElementById("scan-again");

function createScanner() {
  return new Html5Qrcode("reader", {
    formatsToSupport: SUPPORTED_FORMATS,
    // Use the browser's built-in BarcodeDetector where it exists (e.g. Chrome on
    // Android); it reads 1D barcodes much better than the JS fallback
    experimentalFeatures: { useBarCodeDetectorIfSupported: true },
    verbose: false,
  });
}

let scanner = createScanner();

let videoSize = null; // unscaled size of the library's <video>, known once it plays
let handlingResult = false;

function setState(state) {
  page.dataset.state = state; // style.css shows/hides parts of the page per state
}

function showMessage(title, body) {
  messageTitle.textContent = title;
  messageBody.textContent = body;
  setState("error");
}

// The library sizes its <video> to #reader's width, then maps the scan box onto
// the camera image using the video's unscaled clientWidth/clientHeight. A CSS
// transform doesn't change those, so scaling #reader lets the video cover the
// viewfinder while the scan box (shrunk by the same factor) still lines up
// exactly with the gold brackets.
function coverScale() {
  return Math.max(
    viewfinder.clientWidth / videoSize.width,
    viewfinder.clientHeight / videoSize.height,
  );
}

function applyCoverScale() {
  if (videoSize) {
    reader.style.transform = `translate(-50%, -50%) scale(${coverScale()})`;
  }
}

// Passed as the library's qrbox; it calls this once the video starts playing
function scanBoxFor(videoWidth, videoHeight) {
  videoSize = { width: videoWidth, height: videoHeight };
  applyCoverScale();
  const scale = coverScale();
  const frame = scanFrame.getBoundingClientRect();
  return {
    width: Math.max(50, Math.floor(frame.width / scale)), // 50px is the library's minimum
    height: Math.max(50, Math.floor(frame.height / scale)),
  };
}

function stopScanning() {
  // stop() throws if the camera isn't running (e.g. permission was never granted)
  if (scanner.getState() === Html5QrcodeScannerState.NOT_STARTED) {
    return Promise.resolve();
  }
  return scanner.stop().catch((err) => {
    // stop() fails if called before the camera's first frame (e.g. leaving the
    // page while it warms up). The camera is released anyway, but the instance
    // is stuck in its scanning state for good, so replace it.
    console.error("Failed to stop camera cleanly:", err);
    scanner = createScanner();
  });
}

function startScanning() {
  handlingResult = false;
  videoSize = null;
  setState("starting");

  // Wide enough that a 4:3 camera image is at least as tall as the viewfinder,
  // times RENDER_SCALE; coverScale() then shrinks it back down to fit
  const coverWidth = Math.max(viewfinder.clientWidth, (viewfinder.clientHeight * 4) / 3);
  reader.style.transform = "";
  reader.style.width = `${Math.ceil(coverWidth * RENDER_SCALE)}px`;

  // start() can't run while a previous session is still shutting down
  stopScanning()
    .then(() =>
      scanner.start(
        { facingMode: "environment" }, // required, but videoConstraints takes precedence
        { fps: SCAN_FPS, qrbox: scanBoxFor, videoConstraints: VIDEO_CONSTRAINTS },
        onScanSuccess,
      ),
    )
    .then(() => setState("scanning"))
    .catch(showCameraError);
}

function onScanSuccess(decodedText) {
  // The library can report the same code again before stop() finishes
  if (handlingResult) return;
  handlingResult = true;

  console.log("Scanned:", decodedText);
  resultText.textContent = decodedText;
  stopScanning().then(() => setState("scanned"));
}

function showCameraError(err) {
  console.error("Camera failed to start:", err);

  // The library rejects with a string that includes the browser's error name
  const message = String(err);
  if (/NotAllowedError|PermissionDenied|SecurityError/.test(message)) {
    showMessage(
      "Camera access is blocked",
      "Allow camera access for this site in your browser settings, then reload the page. " +
        "Or enter the tracking number manually below.",
    );
  } else if (/NotFoundError|DevicesNotFound|OverconstrainedError/.test(message)) {
    showMessage(
      "No camera found",
      "This device doesn't have a camera the browser can use. " +
        "Enter the tracking number manually below.",
    );
  } else if (/NotReadableError|TrackStartError/.test(message)) {
    showMessage(
      "Camera is in use",
      "Another app or tab may be using the camera. Close it, then reload this page. " +
        "Or enter the tracking number manually below.",
    );
  } else {
    showMessage(
      "Couldn't start the camera",
      "Reload the page to try again, or enter the tracking number manually below.",
    );
  }
}

scanAgainButton.addEventListener("click", startScanning);
window.addEventListener("resize", applyCoverScale);

// Release the camera when leaving the page (including into the back/forward cache)
window.addEventListener("pagehide", stopScanning);

// Coming back with the Back button restores the page from cache with the camera off
window.addEventListener("pageshow", (event) => {
  if (event.persisted && page.dataset.state !== "scanned" && page.dataset.state !== "error") {
    startScanning();
  }
});

// Browsers only expose the camera on secure origins (HTTPS or localhost)
if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
  startScanning();
} else {
  showMessage(
    "Camera needs a secure connection",
    "Browsers only allow camera access over HTTPS or on localhost. " +
      "Enter the tracking number manually below.",
  );
}
