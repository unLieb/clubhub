"""WebAuthn/Passkey-Hilfsfunktionen (Registrierung im Profil + Login auf
/login). RP-ID und erwarteter Origin werden pro Request aus dem Host/Schema
abgeleitet statt fest konfiguriert - funktioniert dadurch unveraendert sowohl
im lokalen Dev (http://localhost:8055, WebAuthn erlaubt "localhost" explizit
auch ohne HTTPS) als auch hinter dem Reverse-Proxy in Produktion
(https://clubhub.nifflheim.de, dank --proxy-headers in uvicorn liefert
request.url bereits das per X-Forwarded-Proto/-Host weitergereichte
Origin), ohne dass eine zusaetzliche Umgebungsvariable gepflegt werden
muesste."""
from fastapi import Request
from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
    base64url_to_bytes,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
    PublicKeyCredentialDescriptor,
)

RP_NAME = "ClubHUB"


def rp_id_for(request: Request) -> str:
    return request.url.hostname


def expected_origin_for(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def build_registration_options(request: Request, user, existing_credential_ids: list[str]):
    """Optionen fuer navigator.credentials.create() im Profil. Fordert einen
    "resident key" (= das, was Betriebssysteme/Browser heute als "Passkey"
    bezeichnen), da der Login auf /login absichtlich ohne vorherige
    Identifier-Eingabe funktionieren soll (siehe build_authentication_options
    mit leerer allow_credentials-Liste) - das setzt discoverable Credentials
    voraus. exclude_credentials verhindert, dass derselbe Authenticator
    versehentlich doppelt fuer denselben Nutzer registriert wird. Gibt das
    Options-Objekt zurueck (nicht schon JSON), damit der Aufrufer die
    enthaltene Challenge (Bytes) fuer die Session abgreifen kann, bevor er
    selbst options_to_json() fuers Frontend aufruft."""
    return generate_registration_options(
        rp_id=rp_id_for(request),
        rp_name=RP_NAME,
        user_id=str(user.id).encode("utf-8"),
        user_name=user.name,
        user_display_name=user.name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(cid)) for cid in existing_credential_ids
        ],
    )


def verify_registration(request: Request, credential: dict, expected_challenge: bytes):
    return verify_registration_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=rp_id_for(request),
        expected_origin=expected_origin_for(request),
    )


def build_authentication_options(request: Request):
    """Optionen fuer navigator.credentials.get() auf /login. Bewusst leere
    allow_credentials-Liste (= "usernameless"/discoverable Login) - der
    Browser/das Betriebssystem zeigt dem Nutzer direkt seine passenden
    Passkeys fuer diese Seite zur Auswahl an, ganz ohne vorherige
    Identifier-Eingabe, wie es "Mit Passkey anmelden" als einzelner Button
    verspricht. Gibt wie build_registration_options das rohe Options-Objekt
    zurueck, nicht schon JSON."""
    return generate_authentication_options(
        rp_id=rp_id_for(request),
        user_verification=UserVerificationRequirement.PREFERRED,
    )


def options_json(options) -> str:
    return options_to_json(options)


def verify_authentication(request: Request, credential: dict, expected_challenge: bytes, public_key: bytes, sign_count: int):
    return verify_authentication_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=rp_id_for(request),
        expected_origin=expected_origin_for(request),
        credential_public_key=public_key,
        credential_current_sign_count=sign_count,
    )


def encode_bytes(raw: bytes) -> str:
    return bytes_to_base64url(raw)


def decode_bytes(text: str) -> bytes:
    return base64url_to_bytes(text)
