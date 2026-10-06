from flask import redirect, url_for

from app import create_app

app = create_app()


# Login redirects to "/", so send signed-in staff on to the dashboard
@app.route("/")
def index():
    return redirect(url_for("dashboard.dashboard"))


if __name__ == "__main__":
    # host="0.0.0.0" lets phones on the same network reach the dev server
    app.run(host="0.0.0.0", port=5000, debug=True)
