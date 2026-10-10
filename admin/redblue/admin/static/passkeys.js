// Passkey sign-in and registration for the admin. The server sends WebAuthn options as JSON
// (binary fields base64url-encoded); this turns them into what navigator.credentials wants,
// runs the ceremony, and posts the browser's answer back. Same-origin file, so it fits the CSP.
(function () {
  "use strict";

  const supported = !!(window.PublicKeyCredential && navigator.credentials);

  function toBytes(b64url) {
    const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(b64 + "===".slice((b64.length + 3) % 4));
    return Uint8Array.from(bin, (c) => c.charCodeAt(0)).buffer;
  }

  function toB64url(buffer) {
    let bin = "";
    new Uint8Array(buffer).forEach((b) => { bin += String.fromCharCode(b); });
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function csrfToken() {
    try {
      return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRF-Token"] || "";
    } catch (e) {
      return "";
    }
  }

  async function post(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try { data = await resp.json(); } catch (e) { /* fall through */ }
    if (!resp.ok) {
      const detail = typeof data.detail === "string" ? data.detail : "";
      throw new Error(data.error || detail || "Something went wrong (" + resp.status + ").");
    }
    return data;
  }

  function credentialJSON(cred) {
    const r = cred.response;
    const out = {
      id: cred.id,
      rawId: toB64url(cred.rawId),
      type: cred.type,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: { clientDataJSON: toB64url(r.clientDataJSON) },
    };
    if (r.attestationObject) {
      out.response.attestationObject = toB64url(r.attestationObject);
      out.response.transports = r.getTransports ? r.getTransports() : [];
    } else {
      out.response.authenticatorData = toB64url(r.authenticatorData);
      out.response.signature = toB64url(r.signature);
      out.response.userHandle = r.userHandle ? toB64url(r.userHandle) : null;
    }
    return out;
  }

  function showError(box, err) {
    const el = box.querySelector(".rb-error");
    // A cancelled or timed-out browser prompt is not worth an error message.
    if (err && err.name === "NotAllowedError") { el.hidden = true; return; }
    el.textContent = (err && err.message) || "The passkey didn't work. Please try again.";
    el.hidden = false;
  }

  async function signIn(box) {
    const options = await post("/admin/passkeys/login/options");
    options.challenge = toBytes(options.challenge);
    (options.allowCredentials || []).forEach((c) => { c.id = toBytes(c.id); });
    const cred = await navigator.credentials.get({ publicKey: options });
    const result = await post("/admin/passkeys/login", {
      credential: credentialJSON(cred),
      next: box.dataset.next || "/admin",
    });
    window.location.assign(result.redirect || "/admin");
  }

  async function register(box) {
    const options = await post("/admin/passkeys/register/options");
    options.challenge = toBytes(options.challenge);
    options.user.id = toBytes(options.user.id);
    (options.excludeCredentials || []).forEach((c) => { c.id = toBytes(c.id); });
    const cred = await navigator.credentials.create({ publicKey: options });
    const name = box.querySelector("input[name=name]").value;
    await post("/admin/passkeys/register", { credential: credentialJSON(cred), name: name });
    window.location.assign("/admin/account?msg=" + encodeURIComponent("Passkey added."));
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-passkey]").forEach(function (box) {
      const kind = box.dataset.passkey;
      if (kind === "unsupported") { box.hidden = supported; return; }
      if (!supported) return;
      box.hidden = false;
      if (kind === "login") {
        box.querySelector("button").addEventListener("click", function () {
          signIn(box).catch(function (err) { showError(box, err); });
        });
      } else if (kind === "register") {
        box.querySelector("form").addEventListener("submit", function (ev) {
          ev.preventDefault();
          register(box).catch(function (err) { showError(box, err); });
        });
      }
    });
  });
})();
