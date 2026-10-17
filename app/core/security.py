"""Sikkerhedsheadere og indeksering.

Alt er som udgangspunkt noindex. Kun views markeret med @public_page må indekseres
af søgemaskiner (specifikation 12.5).
"""
from flask import request

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "frame-ancestors 'none'; form-action 'self'; base-uri 'self'; object-src 'none'"
)


def public_page(view):
    view.public_page = True
    return view


def init_app(app):
    @app.after_request
    def set_security_headers(response):
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if app.config.get("SESSION_COOKIE_SECURE"):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        if app.config.get("NOINDEX_ALL") or not is_public_endpoint(app, request.endpoint):
            headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response


def is_public_endpoint(app, endpoint):
    view = app.view_functions.get(endpoint) if endpoint else None
    return bool(getattr(view, "public_page", False))
