# Security headers added to every response.

# Everything is self-hosted (app/static/vendor/), so only this site is allowed.
# Exceptions, each needed for something the app does:
#   style-src 'unsafe-inline'  inline style="" attributes, and the styles
#                              html5-qrcode sets on its video element
#   img-src data:              Bootstrap's built-in icons (select arrows,
#                              validation marks) are data: SVGs in its CSS
# The camera stream is attached with video.srcObject, which CSP doesn't cover,
# so it needs no media-src exception.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "connect-src 'self'",  # recipient_search.js calls /api/recipients/search
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    # The scanner needs the camera on this site; nothing else gets it
    "Permissions-Policy": "camera=(self)",
}


def register_security_headers(app):
    @app.after_request
    def add_security_headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
