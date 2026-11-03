# Friendly error pages. They only ever show the fixed text below, so no
# exception details, stack traces or file paths reach the browser.
from flask import render_template

from app import db

ERROR_PAGES = {
    400: ("Something went wrong with that request",
          "The page couldn't process what was sent. Go back, reload the page, and try again."),
    403: ("You don't have access to this",
          "Your account isn't allowed to open this page."),
    404: ("Page not found",
          "That page doesn't exist. Check the address, or start again from the dashboard."),
    405: ("That action isn't available here",
          "The page can't be used this way. Start again from the dashboard."),
    429: ("Too many attempts",
          "Please wait a minute, then try again."),
    500: ("Something went wrong on our end",
          "The problem has been noted. Try again in a moment; if it keeps happening, tell a supervisor."),
}


def render_error(code):
    title, message = ERROR_PAGES[code]
    return render_template("error.html", code=code, title=title, message=message), code


def register_error_pages(app):
    for code in (400, 403, 404, 405, 429):
        app.register_error_handler(code, lambda error, code=code: render_error(code))

    @app.errorhandler(500)
    def server_error(error):
        # Flask has already written the details to the server log. A failed
        # database write would otherwise leave the session unusable.
        db.session.rollback()
        return render_error(500)
