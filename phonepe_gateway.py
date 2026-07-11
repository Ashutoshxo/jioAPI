import time

import requests


class PhonePeError(Exception):
    pass


class PhonePeGateway:
    def __init__(self, config):
        self.enabled = bool(config.get("enabled"))
        self.environment = (config.get("environment") or "sandbox").lower()
        self.client_id = config.get("client_id") or ""
        self.client_secret = config.get("client_secret") or ""
        self.client_version = str(config.get("client_version") or "1")
        self.redirect_url = config.get("redirect_url") or ""
        self.expire_after = int(config.get("expire_after_seconds") or 900)
        self.timeout = int(config.get("timeout_seconds") or 20)
        self._token = None
        self._token_expires_at = 0

    def is_configured(self):
        return bool(
            self.enabled
            and self.client_id
            and self.client_secret
            and self.client_version
            and self.redirect_url
        )

    @property
    def base_url(self):
        if self.environment == "production":
            return "https://api.phonepe.com/apis/pg"
        return "https://api-preprod.phonepe.com/apis/pg-sandbox"

    @property
    def auth_url(self):
        if self.environment == "production":
            return "https://api.phonepe.com/apis/identity-manager/v1/oauth/token"
        return "https://api-preprod.phonepe.com/apis/pg-sandbox/v1/oauth/token"

    def auth_token(self):
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        response = requests.post(
            self.auth_url,
            data={
                "client_id": self.client_id,
                "client_version": self.client_version,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        data = self._json_response(response)
        token = data.get("access_token")
        if not token:
            raise PhonePeError(f"PhonePe auth token missing: {data}")
        self._token = token
        self._token_expires_at = int(data.get("expires_at") or (time.time() + 300))
        return token

    def create_payment(self, merchant_order_id, amount_paise, message="JioMart Bot Deposit"):
        if not self.is_configured():
            raise PhonePeError("PhonePe config missing")
        payload = {
            "merchantOrderId": merchant_order_id,
            "amount": int(amount_paise),
            "expireAfter": self.expire_after,
            "paymentFlow": {
                "type": "PG_CHECKOUT",
                "message": message,
                "merchantUrls": {"redirectUrl": self.redirect_url},
                "paymentModeConfig": {
                    "enabledPaymentModes": [
                        {"type": "UPI_QR"},
                        {"type": "UPI_INTENT"},
                    ]
                },
            },
            "metaInfo": {
                "udf1": "telegram_deposit",
                "udf2": merchant_order_id,
            },
        }
        response = requests.post(
            f"{self.base_url}/checkout/v2/pay",
            json=payload,
            headers=self._json_headers(),
            timeout=self.timeout,
        )
        return self._json_response(response)

    def order_status(self, merchant_order_id, details=False):
        if not self.is_configured():
            raise PhonePeError("PhonePe config missing")
        response = requests.get(
            f"{self.base_url}/checkout/v2/order/{merchant_order_id}/status",
            params={"details": str(bool(details)).lower()},
            headers=self._json_headers(),
            timeout=self.timeout,
        )
        return self._json_response(response)

    def _json_headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": f"O-Bearer {self.auth_token()}",
        }

    @staticmethod
    def _json_response(response):
        try:
            data = response.json()
        except ValueError as exc:
            raise PhonePeError(f"PhonePe non-JSON response: {response.text[:500]}") from exc
        if response.status_code >= 400:
            raise PhonePeError(f"PhonePe HTTP {response.status_code}: {data}")
        return data
