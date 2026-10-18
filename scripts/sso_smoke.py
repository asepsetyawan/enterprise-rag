"""Exercise the real Keycloak authorization-code + PKCE callback in development."""
from html.parser import HTMLParser
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


class LoginForm(HTMLParser):
    action = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "kc-form-login":
            self.action = attrs["action"]


def main():
    with httpx.Client(timeout=30, follow_redirects=True, trust_env=False) as client:
        for attempt in range(60):
            try:
                response = client.get("http://localhost:8089/realms/enterprise-rag/.well-known/openid-configuration")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)
        else:
            raise TimeoutError("Keycloak is not ready")
        response = client.get("http://localhost:8088/auth/login")
        response.raise_for_status()
        form = LoginForm()
        form.feed(response.text)
        if not form.action.startswith("http://localhost:8089/"):
            raise ValueError("Unexpected identity-provider form action")
        # Browsers treat loopback as a secure context; HTTPX does not send Secure cookies over HTTP.
        # Mimic the localhost browser exception ONLY in this development-only test harness.
        for cookie in client.cookies.jar:
            if cookie.domain == "localhost.local":
                cookie.secure = False
        response = client.post(form.action, data={"username": "alice", "password": (ROOT / ".secrets/dev_password").read_text().strip(), "credentialId": ""})
        response.raise_for_status()
        response = client.get("http://localhost:8088/api/me")
        response.raise_for_status()
        me = response.json()
        assert me["user_id"] == "11111111-1111-4111-8111-111111111111"
        assert len(me["tenants"]) == 1
        print("Real OIDC authorization code + PKCE, nonce verification, BFF cookie and tenant membership: passed")


if __name__ == "__main__":
    main()
