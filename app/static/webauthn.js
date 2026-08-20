// Passkey (WebAuthn/FIDO2) - Login (login.html) und Verwaltung (profile.html).
// Serverseitige Optionen/Verifikation siehe webauthn_auth.py + main.py
// (/login/passkey/options+verify, /profile/passkeys/options+register).
// Die vom Server per options_to_json() gelieferten Felder (challenge,
// user.id, excludeCredentials[].id, allowCredentials[].id) sind
// base64url-Strings, navigator.credentials.create()/get() erwarten dafuer
// aber ArrayBuffer - und umgekehrt muss die Antwort des Authenticators vor
// dem Zurueckschicken wieder in base64url-Strings umgewandelt werden, da
// ArrayBuffer nicht direkt JSON-serialisierbar ist. Diese Hin-und-Her-
// Konvertierung ist der Kern dieser Datei.

function webauthnBase64urlToBuffer(base64url) {
  var padded = base64url.replace(/-/g, '+').replace(/_/g, '/');
  var padding = padded.length % 4 === 0 ? '' : '='.repeat(4 - (padded.length % 4));
  var raw = atob(padded + padding);
  var buffer = new Uint8Array(raw.length);
  for (var i = 0; i < raw.length; i++) buffer[i] = raw.charCodeAt(i);
  return buffer.buffer;
}

function webauthnBufferToBase64url(buffer) {
  var bytes = new Uint8Array(buffer);
  var str = '';
  for (var i = 0; i < bytes.byteLength; i++) str += String.fromCharCode(bytes[i]);
  return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function webauthnDecodeCreationOptions(opts) {
  opts.challenge = webauthnBase64urlToBuffer(opts.challenge);
  opts.user.id = webauthnBase64urlToBuffer(opts.user.id);
  if (opts.excludeCredentials) {
    opts.excludeCredentials.forEach(function (c) { c.id = webauthnBase64urlToBuffer(c.id); });
  }
  return opts;
}

function webauthnDecodeRequestOptions(opts) {
  opts.challenge = webauthnBase64urlToBuffer(opts.challenge);
  if (opts.allowCredentials) {
    opts.allowCredentials.forEach(function (c) { c.id = webauthnBase64urlToBuffer(c.id); });
  }
  return opts;
}

function webauthnEncodeRegistrationCredential(cred) {
  var out = {
    id: cred.id,
    rawId: webauthnBufferToBase64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: webauthnBufferToBase64url(cred.response.clientDataJSON),
      attestationObject: webauthnBufferToBase64url(cred.response.attestationObject),
    },
  };
  if (cred.authenticatorAttachment) out.authenticatorAttachment = cred.authenticatorAttachment;
  if (cred.response.getTransports) out.response.transports = cred.response.getTransports();
  return out;
}

function webauthnEncodeAuthenticationCredential(cred) {
  var out = {
    id: cred.id,
    rawId: webauthnBufferToBase64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: webauthnBufferToBase64url(cred.response.clientDataJSON),
      authenticatorData: webauthnBufferToBase64url(cred.response.authenticatorData),
      signature: webauthnBufferToBase64url(cred.response.signature),
      userHandle: cred.response.userHandle ? webauthnBufferToBase64url(cred.response.userHandle) : null,
    },
  };
  if (cred.authenticatorAttachment) out.authenticatorAttachment = cred.authenticatorAttachment;
  return out;
}

function webauthnSupported() {
  return !!(window.PublicKeyCredential && navigator.credentials);
}

// Registriert einen neuen Passkey fuer den eingeloggten Nutzer (Profil).
// onDone(result) wird mit {ok: true} oder {ok: false, error: "..."} aufgerufen.
function webauthnRegisterPasskey(name, onDone) {
  fetch('/profile/passkeys/options', { headers: { 'X-Requested-With': 'fetch' } })
    .then(function (r) { return r.json(); })
    .then(function (opts) {
      return navigator.credentials.create({ publicKey: webauthnDecodeCreationOptions(opts) });
    })
    .then(function (cred) {
      return fetch('/profile/passkeys/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, credential: webauthnEncodeRegistrationCredential(cred) }),
      });
    })
    .then(function (r) { return r.json(); })
    .then(onDone)
    .catch(function (err) {
      var msg = (err && err.name === 'NotAllowedError')
        ? 'Abgebrochen.'
        : 'Passkey konnte nicht erstellt werden.';
      onDone({ ok: false, error: msg });
    });
}

// Meldet den Nutzer per Passkey an (Login-Seite), ganz ohne vorherige
// Identifier-Eingabe (discoverable credential / "usernameless").
function webauthnLoginWithPasskey(nextUrl, onDone) {
  fetch('/login/passkey/options', { headers: { 'X-Requested-With': 'fetch' } })
    .then(function (r) { return r.json(); })
    .then(function (opts) {
      return navigator.credentials.get({ publicKey: webauthnDecodeRequestOptions(opts) });
    })
    .then(function (cred) {
      return fetch('/login/passkey/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credential: webauthnEncodeAuthenticationCredential(cred), next: nextUrl || '/' }),
      });
    })
    .then(function (r) { return r.json(); })
    .then(onDone)
    .catch(function (err) {
      var msg = (err && err.name === 'NotAllowedError')
        ? 'Abgebrochen.'
        : 'Passkey-Anmeldung fehlgeschlagen.';
      onDone({ ok: false, error: msg });
    });
}
