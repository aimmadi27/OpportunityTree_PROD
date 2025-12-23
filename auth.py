import os, base64, hashlib, secrets, time
from typing import Optional
from dataclasses import dataclass

import streamlit as st
from authlib.integrations.requests_client import OAuth2Session
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
OAUTH_SCOPE = "openid email profile"

REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "https://handwritingextraction.streamlit.app/")

@dataclass
class CurrentUser:
    email: str
    name: str
    picture: Optional[str] = None
    sub: Optional[str] = None


def _get_client():
    cid = os.getenv("GOOGLE_CLIENT_ID")
    cs = os.getenv("GOOGLE_CLIENT_SECRET")
    if not cid or not cs:
        raise RuntimeError("Missing GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET")
    return cid, cs


def _new_pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _new_state():
    return base64.urlsafe_b64encode(secrets.token_bytes(24)).rstrip(b"=").decode()


def _oauth_session(client_id: str) -> OAuth2Session:
    return OAuth2Session(
        client_id=client_id,
        scope=OAUTH_SCOPE,
        redirect_uri=REDIRECT_URI,
    )
    
@st.cache_resource
def _pkce_store():
    # state -> {"verifier": str, "ts": float}
    return {}


def start_google_login() -> str:
    client_id, _ = _get_client()
    verifier, challenge = _new_pkce_pair()
    state = _new_state()

    # Save verifier keyed by state (NOT session_state)
    store = _pkce_store()
    store[state] = {"verifier": verifier, "ts": time.time()}

    sess = _oauth_session(client_id)
    uri, _ = sess.create_authorization_url(
        GOOGLE_AUTHORIZATION_ENDPOINT,
        state=state,
        code_challenge=challenge,
        code_challenge_method="S256",
        prompt="consent",
    )
    return uri


def handle_oauth_callback() -> Optional[CurrentUser]:
    q = st.query_params
    if "code" not in q or "state" not in q:
        return None

    returned_state = q.get("state")
    code = q.get("code")

    store = _pkce_store()

    # Optional: prune old entries (10 min)
    now = time.time()
    for s in list(store.keys()):
        if now - store[s]["ts"] > 600:
            store.pop(s, None)

    entry = store.get(returned_state)
    if not entry:
        st.error("Invalid login state.")
        return None

    verifier = entry["verifier"]

    client_id, client_secret = _get_client()
    sess = _oauth_session(client_id)

    token = sess.fetch_token(
        GOOGLE_TOKEN_ENDPOINT,
        code=code,
        code_verifier=verifier,
        client_secret=client_secret,
    )

    idinfo = id_token.verify_oauth2_token(
        token["id_token"],
        google_requests.Request(),
        client_id,
    )

    user = CurrentUser(
        email=idinfo["email"],
        name=idinfo.get("name", idinfo["email"]),
        picture=idinfo.get("picture"),
        sub=idinfo.get("sub"),
    )

    # Persist logged-in user
    st.session_state["current_user"] = user

    # One-time use: remove verifier + clear query params
    store.pop(returned_state, None)
    st.query_params.clear()

    return user


def get_current_user() -> Optional[CurrentUser]:
    return st.session_state.get("current_user")


def logout():
    for k in list(st.session_state.keys()):
        st.session_state.pop(k)
