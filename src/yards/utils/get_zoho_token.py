import requests
import time
import json
import os
import sys

class ZohoInventoryClient:
    def __init__(
        self,
        client_id,
        client_secret,
        redirect_uri,
        organization_id,
        token_file="zoho_tokens.json",
        base_url="https://www.zohoapis.in/inventory/v1"
    ):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.redirect_uri = redirect_uri
        self.organization_id = organization_id

        # ✅ FIX: Make path work for both .py and .exe
        if getattr(sys, 'frozen', False):
            base_path = os.path.dirname(sys.executable)
        else:
            base_path = os.getcwd()

        self.token_file = os.path.join(base_path, token_file)

        self.base_url = base_url
        self.accounts_url = "https://accounts.zoho.in"

        self.tokens = self._load_tokens()

    # ---------------------------
    # TOKEN STORAGE
    # ---------------------------
    def _load_tokens(self):
        try:
            if os.path.exists(self.token_file):
                with open(self.token_file, "r") as f:
                    return json.load(f)
            else:
                # ✅ Auto-create empty file to avoid crash
                with open(self.token_file, "w") as f:
                    json.dump({}, f)
        except Exception as e:
            print(f"⚠️ Token file read error: {e}")

        return {}

    def _save_tokens(self, tokens):
        try:
            tokens["created_at"] = int(time.time())

            with open(self.token_file, "w") as f:
                json.dump(tokens, f, indent=2)

            self.tokens = tokens

        except Exception as e:
            raise Exception(f"Failed to save tokens: {e}")

    # ---------------------------
    # TOKEN GENERATION
    # ---------------------------
    def generate_tokens(self, authorization_code):
        url = f"{self.accounts_url}/oauth/v2/token"

        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": authorization_code
        }

        response = requests.post(url, data=data)
        response.raise_for_status()

        tokens = response.json()

        if "error" in tokens:
            raise Exception(f"Token generation failed: {tokens}")

        self._save_tokens(tokens)
        return tokens

    # ---------------------------
    # REFRESH TOKEN
    # ---------------------------
    def refresh_access_token(self):
        if not self.tokens.get("refresh_token"):
            raise Exception("Refresh token missing. Re-authentication required.")

        url = f"{self.accounts_url}/oauth/v2/token"

        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.tokens.get("refresh_token")
        }

        response = requests.post(url, data=data)
        response.raise_for_status()

        new_tokens = response.json()

        # ✅ Proper validation
        if "error" in new_tokens or "access_token" not in new_tokens:
            raise Exception(
                f"Token refresh failed: {new_tokens}\n"
                "➡️ Fix: Re-generate auth code with access_type=offline & prompt=consent"
            )

        new_tokens["refresh_token"] = self.tokens.get("refresh_token")

        self._save_tokens(new_tokens)
        return new_tokens

    # ---------------------------
    # GET VALID ACCESS TOKEN
    # ---------------------------
    def get_access_token(self):
        if not self.tokens:
            raise Exception("Tokens not found. Run generate_tokens() first.")

        expires_in = self.tokens.get("expires_in", 3600)
        created_at = self.tokens.get("created_at", 0)

        # ✅ Auto refresh
        if int(time.time()) > created_at + expires_in - 60:
            print("🔄 Refreshing access token...")
            self.refresh_access_token()

        return self.tokens.get("access_token")

    # ---------------------------
    # API REQUEST WRAPPER
    # ---------------------------
    def make_request(self, method, endpoint, params=None, data=None):
        url = f"{self.base_url}{endpoint}"

        headers = {
            "Authorization": f"Zoho-oauthtoken {self.get_access_token()}"
        }

        if not params:
            params = {}

        params["organization_id"] = self.organization_id

        response = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=data
        )

        try:
            response_data = response.json()
        except:
            response_data = {}

        # ✅ Handle token expiry properly
        if response.status_code == 401 or response_data.get("code") == 57:
            print("⚠️ Token expired. Refreshing...")

            self.refresh_access_token()

            headers["Authorization"] = f"Zoho-oauthtoken {self.get_access_token()}"

            response = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=data
            )

            response_data = response.json()

            if response.status_code == 401 or response_data.get("code") == 57:
                raise Exception(f"Unauthorized even after refresh: {response_data}")

        response.raise_for_status()
        return response_data

    # ---------------------------
    # GET ALL ITEMS
    # ---------------------------
    def get_all_items(self, per_page=200):
        page = 1
        all_items = []

        while True:
            params = {
                "page": page,
                "per_page": per_page
            }

            response = self.make_request("GET", "/items", params=params)

            items = response.get("items", [])
            all_items.extend(items)

            page_context = response.get("page_context", {})
            if not page_context.get("has_more_page"):
                break

            page += 1

        return all_items