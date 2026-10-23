"""Einstieg für die Android-App (Chaquopy): startet den lokalen Server und gibt Port und Token zurück."""
import os


def start(home: str, out_dir: str) -> int:
    os.environ["NRMM_HOME"] = home
    os.environ["NRMM_OUT"] = out_dir
    os.makedirs(out_dir, exist_ok=True)
    from .server import App, serve
    app, _srv = serve(App())
    return app.port
