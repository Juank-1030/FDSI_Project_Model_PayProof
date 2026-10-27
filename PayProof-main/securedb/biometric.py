import base64
import hashlib
import secrets

from . import config
from .database import get_conn, now_iso


class BiometricError(Exception):
    pass


class MockBiometricProvider:
    """Biometría simulada para la demo.

    El dispositivo "firma" el challenge localmente (clave de demo) y el
    servidor solo valida que el actor indicado exista y tenga rol.
    """

    provider_name = "mock"

    def verify_approval(self, proof, approval_id, payload_hash, challenge_b64=None):
        if not proof or not proof.get("actor"):
            return None, "Falta el actor de la verificación biométrica simulada."
        actor = str(proof["actor"]).strip()
        with get_conn() as conn:
            row = conn.execute("SELECT username FROM users WHERE username = ?", (actor,)).fetchone()
        if row is None:
            return None, "El actor de la verificación biométrica no existe en el sistema."
        return actor, None

    def demo_signature(self, approval_id, payload_hash):
        """Firma simulada del dispositivo de demo (HMAC local)."""
        import hmac

        message = f"{approval_id}:{payload_hash}:{config.APPROVAL_HMAC_SECRET}"
        return hmac.new(config.APPROVAL_HMAC_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


class WebAuthnProvider:
    """Biometría real con passkeys (FIDO2/WebAuthn).

    La huella/cara se valida EN el dispositivo; el servidor solo recibe y
    verifica la firma criptográfica sobre un challenge ligado al payload_hash.
    Requiere HTTPS o localhost (contexto seguro del navegador).
    """

    provider_name = "webauthn"

    def __init__(self):
        from webauthn import (  # noqa: F401
            generate_authentication_options,
            generate_registration_options,
            options_to_json,
            verify_authentication_response,
            verify_registration_response,
        )
        from webauthn.helpers import (  # noqa: F401
            base64url_to_bytes,
            bytes_to_base64url,
            parse_authentication_credential_json,
            parse_registration_credential_json,
        )
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor  # noqa: F401

        self.generate_registration_options = generate_registration_options
        self.verify_registration_response = verify_registration_response
        self.generate_authentication_options = generate_authentication_options
        self.verify_authentication_response = verify_authentication_response
        self.options_to_json = options_to_json
        self.base64url_to_bytes = base64url_to_bytes
        self.bytes_to_base64url = bytes_to_base64url
        self.parse_registration_credential_json = parse_registration_credential_json
        self.parse_authentication_credential_json = parse_authentication_credential_json
        self.PublicKeyCredentialDescriptor = PublicKeyCredentialDescriptor

    # ------------------------------------------------------------ registro

    def register_begin(self, username: str):
        """Genera las opciones de registro de una passkey para el usuario."""
        user_id = hashlib.sha256(username.encode("utf-8")).digest()
        options = self.generate_registration_options(
            rp_id=config.WEBAUTHN_RP_ID,
            rp_name=config.WEBAUTHN_RP_NAME,
            user_id=user_id,
            user_name=username,
        )
        return self.options_to_json(options), self.bytes_to_base64url(options["challenge"])

    def register_finish(self, username: str, credential_json: str, challenge_b64: str) -> bool:
        credential = self.parse_registration_credential_json(credential_json)
        verification = self.verify_registration_response(
            credential=credential,
            expected_challenge=self.base64url_to_bytes(challenge_b64),
            expected_origin=config.WEBAUTHN_ORIGIN,
            expected_rp_id=config.WEBAUTHN_RP_ID,
        )
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO passkeys (username, credential_id, public_key, sign_count, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    username,
                    self.bytes_to_base64url(verification.credential_id),
                    self.bytes_to_base64url(verification.credential_public_key),
                    verification.sign_count,
                    now_iso(),
                ),
            )
        return True

    # ------------------------------------------------------------ aprobación

    def auth_options(self, approval_id: int, payload_hash: str, username: str):
        """Genera el reto WebAuthn ligado al payload que se va a aprobar."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT credential_id FROM passkeys WHERE username = ? ORDER BY id DESC LIMIT 1",
                (username,),
            ).fetchone()
        if row is None:
            raise BiometricError("El usuario no tiene passkey registrada.")
        challenge_bytes = hashlib.sha256(
            f"{approval_id}:{payload_hash}:{secrets.token_hex(8)}".encode("utf-8")
        ).digest()
        allow_credentials = [
            self.PublicKeyCredentialDescriptor(id=self.base64url_to_bytes(row["credential_id"]))
        ]
        options = self.generate_authentication_options(
            rp_id=config.WEBAUTHN_RP_ID,
            challenge=challenge_bytes,
            allow_credentials=allow_credentials,
            timeout=60000,
        )
        return self.options_to_json(options), self.bytes_to_base64url(challenge_bytes)

    def verify_approval(self, proof, approval_id, payload_hash, challenge_b64=None):
        """Verifica la firma biométrica y devuelve el username del aprobador."""
        if not challenge_b64:
            return None, "Falta el challenge de la verificación biométrica."
        try:
            credential = self.parse_authentication_credential_json(proof)
        except Exception as exc:  # pragma: no cover - depende del paquete
            return None, f"Respuesta biométrica inválida: {exc}"
        credential_id_b64 = self.bytes_to_base64url(credential.raw_id)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM passkeys WHERE credential_id = ?", (credential_id_b64,)
            ).fetchone()
            if row is None:
                return None, "La passkey presentada no está registrada en PayProof."
            try:
                verification = self.verify_authentication_response(
                    credential=credential,
                    expected_challenge=self.base64url_to_bytes(challenge_b64),
                    expected_origin=config.WEBAUTHN_ORIGIN,
                    expected_rp_id=config.WEBAUTHN_RP_ID,
                    credential_public_key=self.base64url_to_bytes(row["public_key"]),
                    credential_current_sign_count=row["sign_count"],
                )
            except Exception as exc:
                return None, f"Verificación biométrica fallida: {exc}"
            conn.execute(
                "UPDATE passkeys SET sign_count = ? WHERE id = ?",
                (verification.new_sign_count, row["id"]),
            )
            return row["username"], None


_biometric_instance = None


def get_provider():
    global _biometric_instance
    if _biometric_instance is not None:
        return _biometric_instance
    provider = (config.BIOMETRIC_PROVIDER or "mock").lower()
    if provider == "webauthn":
        _biometric_instance = WebAuthnProvider()
    else:
        _biometric_instance = MockBiometricProvider()
    return _biometric_instance
