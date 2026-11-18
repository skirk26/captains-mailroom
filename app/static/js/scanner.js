// Barcode scanning for the check-in and check-out pages. Relies on
// html5-qrcode, which the page loads (from static/vendor/) before this file.
//
// Each page sets these on its #reader element:
//   data-confirm-url        where to go with ?tracking=<decoded value>
//   data-tracking-patterns  JSON list of tracking-number regexes (CARRIER_PATTERNS)
//   data-expected-tracking  optional (check-out from a package's page): the
//                           tracking number staff mean to scan, normalized. A
//                           different label shows a warning instead of navigating.
//
// The page layout is _scan_page.html. Around the decoding (which this file's
// settings below control) it adds: a green frame and a short vibration on a
// good read, a flashlight button where the camera supports one, camera
// problems as an inline alert with Try again, and pausing while the manual
// entry sheet is open (manual_entry.js sends scanner:pause / scanner:resume).

// ---- Supported barcode formats ----
// CODE_128 is what UPS, FedEx, and USPS tracking barcodes use. DATA_MATRIX is
// for Amazon labels: the TBA number is also in the top-left square code, which
// survives print damage that often leaves Amazon's long barcode unreadable.
// The others are only here for testing with whatever barcode is handy; narrow
// this list later. Fewer formats = faster, more reliable decoding.
const SUPPORTED_FORMATS = [
  Html5QrcodeSupportedFormats.CODE_128,
  Html5QrcodeSupportedFormats.DATA_MATRIX,
  Html5QrcodeSupportedFormats.QR_CODE,
  Html5QrcodeSupportedFormats.CODE_39,
  Html5QrcodeSupportedFormats.EAN_13,
  Html5QrcodeSupportedFormats.UPC_A,
];

const SCAN_FPS = 10;

// A good read shows the green frame this long before moving on
const SUCCESS_MS = 300;

// Shipping labels carry DataMatrix codes holding internal IDs as well as the
// tracking number (Amazon prints three), so a DataMatrix only counts if it
// looks like a tracking number. Patterns come from CARRIER_PATTERNS in checkin.py.
const IGNORE_UNLESS_TRACKING = [Html5QrcodeSupportedFormats.DATA_MATRIX];

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

const reader = document.getElementById("reader");
const page = reader.closest("[data-state]"); // the element style.css reads the scan state from
const trackingPatterns = JSON.parse(reader.dataset.trackingPatterns || "[]").map(
  (pattern) => new RegExp(`^(?:${pattern})$`),
);
const viewfinder = document.getElementById("viewfinder");
const scanFrame = document.getElementById("scan-frame");
const messageTitle = document.getElementById("scan-message-title");
const messageBody = document.getElementById("scan-message-body");
const cameraError = document.getElementById("camera-error");
const torchButton = document.getElementById("torch-toggle");
const expectedTracking = reader.dataset.expectedTracking || "";
const mismatch = document.getElementById("scan-mismatch");

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

// Camera problems: an inline alert with a plain-language fix and Try again
function showMessage(title, body) {
  messageTitle.textContent = title;
  messageBody.textContent = body;
  cameraError.hidden = false;
  torchButton.hidden = true;
  setState("error");
}

const secureCamera = Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

document.getElementById("camera-retry").addEventListener("click", () => {
  if (!secureCamera) {
    window.location.reload(); // nothing to retry on this page until it's opened over HTTPS
    return;
  }
  startScanning();
});

// ---- Flashlight ----
// Only some phones (mostly Android Chrome) let a web page switch the light on.
// The button stays hidden everywhere else.
function torchFeature() {
  try {
    const torch = scanner.getRunningTrackCameraCapabilities().torchFeature();
    return torch.isSupported() ? torch : null;
  } catch (err) {
    return null;
  }
}

function setUpTorch() {
  const torch = torchFeature();
  torchButton.hidden = !torch;
  torchButton.setAttribute("aria-pressed", String(Boolean(torch && torch.value())));
}

torchButton.addEventListener("click", () => {
  const torch = torchFeature();
  if (!torch) {
    torchButton.hidden = true;
    return;
  }
  const on = !torch.value();
  torch
    .apply(on)
    .then(() => torchButton.setAttribute("aria-pressed", String(on)))
    .catch((err) => {
      console.error("Couldn't switch the flashlight:", err);
      torchButton.hidden = true;
    });
});

// ---- Manual entry sheet ----
// Pausing stops decoding (and the video) behind the sheet; it resumes on close
document.addEventListener("scanner:pause", () => {
  if (scanner.getState() === Html5QrcodeScannerState.SCANNING) scanner.pause(true);
});
document.addEventListener("scanner:resume", () => {
  if (scanner.getState() === Html5QrcodeScannerState.PAUSED) scanner.resume();
});

// The library sizes its <video> to #reader's width, then maps the scan box onto
// the camera image using the video's unscaled clientWidth/clientHeight. A CSS
// transform doesn't change those, so scaling #reader lets the video cover the
// viewfinder while the scan box (shrunk by the same factor) still lines up
// exactly with the corner brackets.
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
  cameraError.hidden = true;
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
        {
          fps: SCAN_FPS,
          qrbox: scanBoxFor,
          videoConstraints: VIDEO_CONSTRAINTS,
          // Camera frames arrive unmirrored, so skip the library's second,
          // mirrored decode of every frame; that roughly doubles decode speed
          disableFlip: true,
        },
        onScanSuccess,
      ),
    )
    .then(() => {
      setState("scanning");
      setUpTorch();
    })
    .catch(showCameraError);
}

function normalizeTracking(text) {
  return text.replace(/\s+/g, "").toUpperCase(); // same as normalize_tracking()
}

function looksLikeTrackingNumber(text) {
  const compact = normalizeTracking(text);
  return trackingPatterns.some((pattern) => pattern.test(compact));
}

function confirmUrlFor(tracking) {
  const confirmUrl = new URL(reader.dataset.confirmUrl, window.location.href);
  confirmUrl.searchParams.set("tracking", tracking); // URL-encodes the value
  return confirmUrl;
}

// Scanned a label other than the expected one: stop and ask, rather than
// opening the wrong package
function showMismatch(scannedText) {
  const scanned = normalizeTracking(scannedText);
  document.getElementById("scan-mismatch-text").textContent =
    `This isn't the package you selected (it ends ${scanned.slice(-8)}).`;
  document.getElementById("scan-mismatch-continue").href = confirmUrlFor(scannedText);
  setState("paused");
  mismatch.hidden = false;
}

if (mismatch) {
  document.getElementById("scan-again").addEventListener("click", () => {
    mismatch.hidden = true;
    startScanning();
  });
}

function onScanSuccess(decodedText, decodedResult) {
  // The library can report the same code again before stop() finishes
  if (handlingResult) return;

  const format = decodedResult?.result?.format?.format;
  if (IGNORE_UNLESS_TRACKING.includes(format) && !looksLikeTrackingNumber(decodedText)) {
    return; // e.g. Amazon's sorting codes; keep scanning for the tracking number
  }
  handlingResult = true;

  console.log("Scanned:", decodedText);
  if (expectedTracking && normalizeTracking(decodedText) !== expectedTracking) {
    stopScanning().then(() => showMismatch(decodedText));
    return;
  }
  showSuccess().then(stopScanning).then(() => window.location.assign(confirmUrlFor(decodedText)));
}

// A good read: green frame with a check, and a short buzz where phones allow it.
// With reduced motion the frame still turns green; it just doesn't animate.
function showSuccess() {
  setState("success");
  if (navigator.vibrate) navigator.vibrate(30);
  return new Promise((resolve) => setTimeout(resolve, SUCCESS_MS));
}

function showCameraError(err) {
  console.error("Camera failed to start:", err);

  // The library rejects with a string that includes the browser's error name
  const message = String(err);
  if (/NotAllowedError|PermissionDenied|SecurityError/.test(message)) {
    showMessage(
      "Camera access is blocked",
      "Allow the camera for this site in your browser's settings, then tap Try again. " +
        "Or tap “Can't scan? Enter manually” below.",
    );
  } else if (/NotFoundError|DevicesNotFound|OverconstrainedError/.test(message)) {
    showMessage(
      "No camera found",
      "This device has no camera the browser can use. " +
        "Tap “Can't scan? Enter manually” below to type the number.",
    );
  } else if (/NotReadableError|TrackStartError/.test(message)) {
    showMessage(
      "Camera is in use",
      "Close any other app or tab that's using the camera, then tap Try again.",
    );
  } else {
    showMessage(
      "Couldn't start the camera",
      "Tap Try again. If it keeps failing, tap “Can't scan? Enter manually” below.",
    );
  }
}

window.addEventListener("resize", applyCoverScale);

// Release the camera when leaving the page (including into the back/forward cache)
window.addEventListener("pagehide", stopScanning);

// Coming back with the Back button restores the page from cache with the camera off
window.addEventListener("pageshow", (event) => {
  if (event.persisted && page.dataset.state !== "error" && page.dataset.state !== "paused") {
    startScanning();
  }
});

// Browsers only expose the camera on secure origins (HTTPS or localhost)
if (secureCamera) {
  startScanning();
} else {
  showMessage(
    "Camera needs a secure connection",
    "Open this site with an https:// address to use the camera. " +
      "Until then, tap “Can't scan? Enter manually” below.",
  );
}
