"""Two-factor routes are switched off until they are rebuilt.

The TOTP endpoints call operator-manager methods that do not exist, so none of them has ever worked, and
/api/operator/totp/verify was written to mint a session from a callsign and a six-digit code alone. Answering
501 for the whole prefix keeps anyone from wiring it to a password-less login by accident.
"""
from flask import jsonify, request

TOTP_PREFIX = "/api/operator/totp/"


def install_totp_gate(app):
    @app.before_request
    def _totp_not_available():
        if request.path.startswith(TOTP_PREFIX):
            return jsonify({"status": "error", "message": "Two-factor authentication is not available"}), 501
