import os

import httpx

from . import config

CHANNEL_HEADER = "X-Channel"
CHANNEL_NAME = "payproof"


class CoreClientError(Exception):
    pass


class FinAegisClient:
    """Cliente HTTP del core bancario FinAegis.

    Toda llamada lleva el header X-Channel: payproof para que el core
    atribuya el tráfico a este canal en su observabilidad (Grafana).
    """

    def __init__(self, base_url=None, email=None, password=None, channel=CHANNEL_NAME):
        self.base_url = (base_url or config.FINAEGIS_BASE_URL).rstrip("/")
        self.email = email or config.FINAEGIS_EMAIL
        self.password = password or config.FINAEGIS_PASSWORD
        self.channel = channel
        self._token = None

    def _headers(self, json_body=False):
        headers = {CHANNEL_HEADER: self.channel}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, method, path, json_body=None, timeout=20.0):
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.request(
                method,
                url,
                json=json_body,
                headers=self._headers(json_body=json_body is not None),
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise CoreClientError(f"No se pudo conectar con el core FinAegis ({url}): {exc}") from exc
        if resp.status_code == 401 and self._token:
            self._token = None
            self.login()
            try:
                resp = httpx.request(
                    method,
                    url,
                    json=json_body,
                    headers=self._headers(json_body=json_body is not None),
                    timeout=timeout,
                )
            except httpx.HTTPError as exc:
                raise CoreClientError(f"No se pudo conectar con el core FinAegis ({url}): {exc}") from exc
        return resp

    def login(self):
        resp = self._request(
            "POST",
            "/api/auth/login",
            json_body={
                "email": self.email,
                "password": self.password,
                "device_name": "payproof",
            },
        )
        if resp.status_code != 200:
            raise CoreClientError(f"Login en el core falló (HTTP {resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        token = (data.get("data") or {}).get("access_token") or data.get("access_token")
        if not token:
            raise CoreClientError("La respuesta de login no incluye access_token.")
        self._token = token
        return token

    def _ensure_token(self):
        if self._token:
            return
        self.login()

    def get_accounts(self):
        self._ensure_token()
        resp = self._request("GET", "/api/v2/accounts")
        if resp.status_code != 200:
            raise CoreClientError(f"Listar cuentas falló (HTTP {resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        accounts = data.get("data") or data.get("accounts") or []
        if isinstance(accounts, dict):
            accounts = accounts.get("accounts") or accounts.get("data") or []
        return accounts

    def get_source_account_uuid(self):
        configured = (config.FINAEGIS_FROM_ACCOUNT or "").strip()
        if configured:
            return configured
        accounts = self.get_accounts()
        if not accounts:
            raise CoreClientError("El usuario del core no tiene cuentas para originar el pago.")
        return accounts[0].get("uuid") or accounts[0].get("id")

    def get_vendor_account_uuid(self):
        accounts = self.get_accounts()
        if len(accounts) >= 2:
            return accounts[1].get("uuid") or accounts[1].get("id")
        return self.get_source_account_uuid()

    def create_transfer(self, from_account_uuid, to_account_uuid, amount, asset_code="EUR", description="Pago PayProof"):
        self._ensure_token()
        payload = {
            "from_account_uuid": from_account_uuid,
            "to_account_uuid": to_account_uuid,
            "amount": amount,
            "asset_code": asset_code,
            "description": description[:255],
        }
        resp = self._request("POST", "/api/v2/transfers", json_body=payload, timeout=45.0)
        if resp.status_code not in (200, 201):
            raise CoreClientError(
                f"El core rechazó la transferencia (HTTP {resp.status_code}): {resp.text[:400]}"
            )
        data = resp.json()
        inner = data.get("data") or {}
        return {
            "transfer_uuid": inner.get("uuid") or inner.get("transfer_uuid"),
            "status": inner.get("status", "pending"),
            "amount": amount,
            "asset_code": asset_code,
        }
