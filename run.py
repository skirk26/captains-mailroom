from app import create_app, debug_enabled

app = create_app()


if __name__ == "__main__":
    # host="0.0.0.0" lets phones on the same network reach the dev server
    app.run(host="0.0.0.0", port=5000, debug=debug_enabled())
