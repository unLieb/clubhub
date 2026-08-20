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

// Gemeinsamer Kern fuer beide Login-Wege (siehe unten): den expliziten "Mit
// Passkey anmelden"-Button (mediation weggelassen = normaler Modal-Dialog)
// und die Conditional-UI-Autofill-Vorschlaege im Benutzername-Feld
// (mediation: 'conditional' = kein Dialog, der Browser zeigt die
// passenden Passkeys stattdessen direkt in der Autofill-Liste des Feldes
// an, das dafuer autocomplete="webauthn" braucht, siehe login.html). Beide
// nutzen denselben /login/passkey/options-Endpunkt, da sich die
// Challenge/Optionen fachlich nicht unterscheiden - nur mediation ist
// clientseitig anders.
function webauthnRequestLogin(mediation, nextUrl, onDone) {
  fetch('/login/passkey/options', { headers: { 'X-Requested-With': 'fetch' } })
    .then(function (r) { return r.json(); })
    .then(function (opts) {
      var getOptions = { publicKey: webauthnDecodeRequestOptions(opts) };
      if (mediation) getOptions.mediation = mediation;
      return navigator.credentials.get(getOptions);
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
      // AbortError passiert routinemaessig, wenn ein zweiter get()-Aufruf
      // (Button-Klick waehrend die Conditional-UI-Anfrage noch laeuft, oder
      // ein Seitenwechsel) den vorherigen automatisch abbricht - kein
      // echter Fehler, dafuer keine Fehlermeldung anzeigen.
      if (err && err.name === 'AbortError') return;
      var msg = (err && err.name === 'NotAllowedError')
        ? 'Abgebrochen.'
        : 'Passkey-Anmeldung fehlgeschlagen.';
      onDone({ ok: false, error: msg });
    });
}

// Meldet den Nutzer per Passkey an (expliziter Button, Login-Seite), ganz
// ohne vorherige Identifier-Eingabe (discoverable credential / "usernameless").
function webauthnLoginWithPasskey(nextUrl, onDone) {
  webauthnRequestLogin(undefined, nextUrl, onDone);
}

// Startet im Hintergrund die Conditional-UI-Anfrage (Passkey-Autofill): kein
// Dialog, der Browser bietet passende Passkeys stattdessen direkt in der
// nativen Autofill-Liste des Benutzername-Felds an, sobald es fokussiert
// wird. Loest onDone erst aus, wenn der Nutzer dort tatsaechlich einen
// Passkey auswaehlt (oder es fehlschlaegt) - laeuft sonst einfach im
// Hintergrund weiter, bis die Seite verlassen wird.
function webauthnStartConditionalLogin(nextUrl, onDone) {
  if (!webauthnSupported() || typeof PublicKeyCredential.isConditionalMediationAvailable !== 'function') return;
  PublicKeyCredential.isConditionalMediationAvailable().then(function (available) {
    if (available) webauthnRequestLogin('conditional', nextUrl, onDone);
  });
}
