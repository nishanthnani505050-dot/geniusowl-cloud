"""
auth.py — simple Bearer token check for /talk endpoint.
"""

import os


def check_bearer_token(request) -> bool:
    expected = os.environ.get("DEVICE_TOKEN", "test123")
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    return header[len("Bearer "):].strip() == expected
