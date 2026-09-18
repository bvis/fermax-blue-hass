#!/usr/bin/env python3
"""Extract API and Firebase credentials from the Fermax Blue Android APK.

Usage:
    python scripts/extract_credentials.py /path/to/fermax-blue.apk
    python scripts/extract_credentials.py /path/to/app-sources-dir/
    python scripts/extract_credentials.py --client-id <ID> --client-secret <SECRET>

Extracts:
  - Firebase credentials from resources.arsc / google-services.json
  - Fermax API URLs and OAuth client credentials from Java source

Output: a credentials.json file ready for use with the integration.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

BASIC_AUTH_RE = re.compile(r"(Basic\s+[A-Za-z0-9+/=]{50,})")
PRODUCTION_AUTH_HOST = "oauth-pro-duoxme.fermax.io"
PRODUCTION_BASE_HOST = "pro-duoxme.fermax.io"

# Constants whose name marks them as telemetry credentials. The app ships a
# TRACING_BASIC_AUTH header right next to the OAuth ones; picking it up as
# fermax_auth_basic yields a Basic header the OAuth endpoint rejects.
TRACING_CONSTANT_RE = re.compile(
    r'\b[A-Z0-9_]*(?:TRACING|TRACE|MONITORING|OTEL|TELEMETRY)[A-Z0-9_]*\s*=\s*"([^"]{10,300})"'
)

# APK 4.3.4+ dropped the AES-encrypted arrays and stores the OAuth credentials
# as plain string constants in BuildConfig instead.
BUILD_CONFIG_CLIENT_ID_RE = re.compile(r'\bOAUTH_CLIENT_ID\s*=\s*"([^"]+)"')
BUILD_CONFIG_CLIENT_SECRET_RE = re.compile(r'\bOAUTH_CLIENT_SECRET\s*=\s*"([^"]+)"')

# Up to APK 4.3.0 the credentials come from these two Urls.java accessors.
CLIENT_ID_METHOD_RE = re.compile(r"\bclientId\s*\(")
CLIENT_SECRET_METHOD_RE = re.compile(r"\bclientSecret\s*\(")


@dataclass(frozen=True)
class OAuthCredentialCandidate:
    """OAuth Basic candidate generated from APK OAuth credentials."""

    label: str
    auth_basic: str
    auth_url: str = ""
    base_url: str = ""
    source: str = "OAuthUtils.java + Urls.java"


def _has_monitoring_or_tracing_context(text: str) -> bool:
    """Return True if text appears to belong to telemetry/tracing code."""
    lower = text.lower()
    return any(
        hint in lower
        for hint in (
            "tracemanagerotel",
            "opentelemetry",
            "/monitoring/",
            "/traces",
            "monitoring/v1/traces",
            "traceparent",
        )
    )


def _extract_strings_from_arsc(apk_path: str) -> list[str]:
    """Extract printable strings from resources.arsc inside the APK."""
    try:
        with zipfile.ZipFile(apk_path) as zf:
            data = zf.read("resources.arsc")
            return [
                s.decode("ascii", errors="ignore")
                for s in re.findall(rb"[A-Za-z0-9_./:@+ -]{8,300}", data)
            ]
    except (KeyError, zipfile.BadZipFile):
        return []


def _extract_strings_from_dex(apk_path: str) -> list[str]:
    """Extract printable strings from DEX files inside the APK."""
    strings: list[str] = []
    try:
        with zipfile.ZipFile(apk_path) as zf:
            for name in zf.namelist():
                if name.endswith(".dex"):
                    data = zf.read(name)
                    strings.extend(
                        s.decode("ascii", errors="ignore")
                        for s in re.findall(rb"[A-Za-z0-9_./:@+ -]{15,300}", data)
                    )
    except zipfile.BadZipFile:
        pass
    return strings


def _search_sources_dir(dir_path: str) -> list[str]:
    """Collect relevant strings from an app sources directory."""
    strings: list[str] = []
    root = Path(dir_path)

    for java_file in root.rglob("*.java"):
        try:
            content = java_file.read_text(errors="ignore")
        except OSError:
            continue
        monitoring_context = _has_monitoring_or_tracing_context(
            f"{java_file.as_posix()}\n{content}"
        )
        # Values assigned to a telemetry-named constant are never OAuth
        # credentials, whatever file they live in (BuildConfig carries both).
        tracing_values = set(TRACING_CONSTANT_RE.findall(content))
        for literal in re.findall(r'"([^"]{10,300})"', content):
            if BASIC_AUTH_RE.search(literal) and (monitoring_context or literal in tracing_values):
                continue
            strings.append(literal)

    for json_file in root.rglob("*.json"):
        try:
            content = json_file.read_text(errors="ignore")
            strings.append(content)
        except OSError:
            continue

    return strings


_ASCII_MAP = {
    "NAK": 21,
    "DC4": 20,
    "ESC": 27,
    "US": 31,
    "GS": 29,
    "FS": 28,
    "RS": 30,
    "ETB": 23,
    "CAN": 24,
    "DC1": 17,
    "DC2": 18,
    "DC3": 19,
    "SOH": 1,
    "STX": 2,
    "ETX": 3,
}


def _parse_java_byte_token(token: str) -> int:
    """Parse a single Java byte value token to an unsigned byte."""
    token = token.strip()
    # (byte) N
    m = re.search(r"\(byte\)\s*(-?\d+)", token)
    if m:
        return int(m.group(1)) & 0xFF
    # Byte.valueOf(N)
    m = re.search(r"Byte\.valueOf\(\s*(-?\d+)\s*\)", token)
    if m:
        return int(m.group(1)) & 0xFF
    # Byte.valueOf(Ascii.XX) or just Ascii.XX
    if "Ascii" in token:
        for name, val in _ASCII_MAP.items():
            if name in token:
                return val
        return 0
    # bValueOf / bValueOf2 (local variable references)
    if "bValueOf" in token or "bvalue" in token.lower():
        return 20 if "2" in token else 21
    # Raw int
    try:
        return int(token) & 0xFF
    except ValueError:
        return 0


def _parse_java_byte_list(byte_list_str: str) -> bytes:
    """Parse a comma-separated Java byte list into bytes."""
    return bytes(_parse_java_byte_token(t) for t in byte_list_str.split(","))


def _read_java_source(java_file: Path) -> str:
    """Read a Java source file, treating an unreadable one as empty."""
    try:
        return java_file.read_text(errors="ignore")
    except OSError:
        return ""


def _declares_oauth_methods(content: str) -> bool:
    """Whether a Urls.java source carries the OAuth accessors we decrypt."""
    return bool(CLIENT_ID_METHOD_RE.search(content) and CLIENT_SECRET_METHOD_RE.search(content))


def _find_oauth_aes_key(root: Path) -> bytes | None:
    """Find the AES key used by OAuthUtils.decrypt()."""
    for java_file in root.rglob("OAuthUtils.java"):
        m = re.search(r"SecretKeySpec\(new byte\[]\s*\{([^}]+)\}", _read_java_source(java_file))
        if m:
            return _parse_java_byte_list(m.group(1))
    return None


def _read_urls_source(root: Path) -> str:
    """Read the Urls.java source if present.

    Library modules ship their own Urls.java, and the directory walk order is
    filesystem-dependent, so the file carrying the OAuth accessors wins over
    whatever happens to be found first. Failing that, a file that at least
    holds URLs is preferred, so the environment endpoints are still picked up.
    """
    with_urls = ""
    first = ""
    for java_file in root.rglob("Urls.java"):
        content = _read_java_source(java_file)
        if not content:
            continue
        if _declares_oauth_methods(content):
            return content
        if not with_urls and "http" in content:
            with_urls = content
        if not first:
            first = content
    return with_urls or first


def _parse_byte_arrays_from_method(content: str, method_name: str) -> list[bytes]:
    """Extract all Java byte array literals from a method body."""
    m = re.search(
        rf"\b{re.escape(method_name)}\s*\(\)\s*\{{(.*?)(?=\n\s+(?:public|private|protected)\s|\Z)",
        content,
        re.DOTALL,
    )
    if not m:
        return []

    method_body = m.group(1)
    local_vars: dict[str, int] = {}
    for lv in re.finditer(r"Byte\s+(\w+)\s*=\s*Byte\.valueOf\(([^;]+)\);", method_body):
        local_vars[lv.group(1)] = _parse_java_byte_token(lv.group(2))

    arrays: list[bytes] = []
    for arr_match in re.finditer(r"new (?:Byte|byte)\[]\s*\{([^}]+)\}", method_body):
        byte_values: list[int] = []
        for token in arr_match.group(1).split(","):
            token = token.strip()
            byte_values.append(
                local_vars[token] if token in local_vars else _parse_java_byte_token(token)
            )
        arrays.append(bytes(byte_values))
    return arrays


def _strip_pkcs_padding(data: bytes) -> bytes:
    """Remove PKCS5/PKCS7 padding."""
    if not data:
        raise ValueError("empty decrypted value")
    padding_length = data[-1]
    if padding_length < 1 or padding_length > 16:
        raise ValueError("invalid PKCS padding length")
    if data[-padding_length:] != bytes([padding_length]) * padding_length:
        raise ValueError("invalid PKCS padding")
    return data[:-padding_length]


def _decrypt_aes_ecb_pkcs5(encrypted: bytes, aes_key: bytes) -> str:
    """Decrypt AES/ECB/PKCS5Padding data from the Android app."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        decryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor()
        decrypted = decryptor.update(encrypted) + decryptor.finalize()
    except ImportError:
        try:
            from Crypto.Cipher import AES as _AES  # type: ignore[import-untyped]

            decrypted = _AES.new(aes_key, _AES.MODE_ECB).decrypt(encrypted)
        except ImportError as exc:
            raise RuntimeError("AES dependency not available") from exc

    return _strip_pkcs_padding(decrypted).decode("utf-8")


def _build_basic_header(client_id: str, client_secret: str) -> str:
    """Build the OAuth Basic header the same way as the Android app."""
    credential = f"{quote_plus(client_id)}:{quote_plus(client_secret)}"
    return f"Basic {base64.b64encode(credential.encode()).decode()}"


def _flag_value(args: list[str], name: str) -> str | None:
    """Return the value of --name VALUE or --name=VALUE, or None if absent."""
    prefix = f"{name}="
    for index, arg in enumerate(args):
        if arg == name:
            return args[index + 1] if index + 1 < len(args) else ""
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    return None


def _auth_basic_from_args(args: list[str]) -> str | None:
    """Build fermax_auth_basic from --client-id/--client-secret if both are given.

    Returns None when neither flag is present so the caller falls back to APK
    analysis. Raises ValueError when the flags are used incorrectly.
    """
    client_id = _flag_value(args, "--client-id")
    client_secret = _flag_value(args, "--client-secret")
    if client_id is None and client_secret is None:
        return None
    if client_id is None or client_secret is None:
        raise ValueError("both --client-id and --client-secret are required together")
    if not client_id or not client_secret:
        raise ValueError("--client-id and --client-secret must not be empty")
    return _build_basic_header(client_id, client_secret)


def _extract_preferred_urls_from_source(content: str) -> tuple[str, str]:
    """Find preferred production auth/base URLs from Urls.java."""
    auth_url = ""
    base_url = ""

    m = re.search(rf"(https://{re.escape(PRODUCTION_AUTH_HOST)}[^\s\"']*)", content)
    if m:
        auth_url = m.group(1).rstrip("/")
    else:
        m = re.search(r"(https://oauth-[^\s\"']*fermax\.io[^\s\"']*)", content)
        if m:
            auth_url = m.group(1).rstrip("/")

    if auth_url and not auth_url.endswith("/oauth/token"):
        auth_url = f"{auth_url}/oauth/token"

    m = re.search(rf"(https://{re.escape(PRODUCTION_BASE_HOST)})(?:[/:\"'\s)]|$)", content)
    if m:
        base_url = m.group(1)
    else:
        for url in re.findall(r"https://[A-Za-z0-9.-]*fermax\.io", content):
            if not any(skip in url for skip in ("oauth", "signaling", "monitor")):
                base_url = url
                break

    return auth_url, base_url


def _production_candidate_index(
    content: str,
    auth_url: str,
    base_url: str,
    candidate_count: int,
) -> int | None:
    """Infer which OAuth credential array belongs to production."""
    if candidate_count == 0:
        return None
    if candidate_count == 1:
        return 0
    if (
        PRODUCTION_AUTH_HOST in content
        or PRODUCTION_BASE_HOST in content
        or PRODUCTION_AUTH_HOST in auth_url
        or PRODUCTION_BASE_HOST in base_url
    ):
        return candidate_count - 1
    return None


def _select_oauth_candidate(
    candidates: list[OAuthCredentialCandidate],
) -> OAuthCredentialCandidate | None:
    """Select the best OAuth candidate, preserving previous last-candidate fallback."""
    if not candidates:
        return None
    for candidate in candidates:
        if candidate.label == "production":
            return candidate
    return candidates[0] if len(candidates) == 1 else candidates[-1]


def _find_build_config_oauth(root: Path) -> tuple[str, str] | None:
    """Find plaintext OAuth credentials in BuildConfig.java (APK 4.3.4+).

    Library modules ship their own BuildConfig.java, so every match is checked
    and only the one carrying both OAuth constants is used. The neighbouring
    TRACING_BASIC_AUTH constant is deliberately never read.
    """
    for java_file in root.rglob("BuildConfig.java"):
        content = _read_java_source(java_file)
        client_id = BUILD_CONFIG_CLIENT_ID_RE.search(content)
        client_secret = BUILD_CONFIG_CLIENT_SECRET_RE.search(content)
        if client_id and client_secret:
            return client_id.group(1), client_secret.group(1)
    return None


def _encrypted_oauth_candidates(
    root: Path, content: str, auth_url: str, base_url: str
) -> list[OAuthCredentialCandidate]:
    """Decrypt the AES-encrypted OAuth arrays used up to APK 4.3.0."""
    aes_key = _find_oauth_aes_key(root)
    if not aes_key or not content:
        return []

    client_id_arrays = _parse_byte_arrays_from_method(content, "clientId")
    client_secret_arrays = _parse_byte_arrays_from_method(content, "clientSecret")
    candidate_count = min(len(client_id_arrays), len(client_secret_arrays))
    if not candidate_count:
        return []

    production_index = _production_candidate_index(content, auth_url, base_url, candidate_count)

    candidates: list[OAuthCredentialCandidate] = []
    for index in range(candidate_count):
        try:
            client_id = _decrypt_aes_ecb_pkcs5(client_id_arrays[index], aes_key)
            client_secret = _decrypt_aes_ecb_pkcs5(client_secret_arrays[index], aes_key)
        except (RuntimeError, UnicodeDecodeError, ValueError):
            continue

        label = "production" if index == production_index else f"environment {index + 1}"
        candidates.append(
            OAuthCredentialCandidate(
                label=label,
                auth_basic=_build_basic_header(client_id, client_secret),
                auth_url=auth_url,
                base_url=base_url,
            )
        )

    return candidates


def _extract_oauth_candidates_from_source(dir_path: str) -> list[OAuthCredentialCandidate]:
    """Build OAuth Basic candidates from the app sources.

    Prefers the encrypted OAuthUtils + Urls.java layout (APK <= 4.3.0) and
    falls back to the plaintext BuildConfig constants introduced in 4.3.4.
    """
    root = Path(dir_path)
    content = _read_urls_source(root)
    auth_url, base_url = _extract_preferred_urls_from_source(content) if content else ("", "")

    candidates = _encrypted_oauth_candidates(root, content, auth_url, base_url)
    if candidates:
        return candidates

    plaintext = _find_build_config_oauth(root)
    if not plaintext:
        return []

    client_id, client_secret = plaintext
    return [
        OAuthCredentialCandidate(
            label="production",
            auth_basic=_build_basic_header(client_id, client_secret),
            auth_url=auth_url,
            base_url=base_url,
            source="BuildConfig.java",
        )
    ]


def _oauth_source_diagnostics(dir_path: str) -> list[str]:
    """Describe what the OAuth scan saw, so a failed extraction is actionable."""
    root = Path(dir_path)

    urls_files = list(root.rglob("Urls.java"))
    with_methods = sum(
        1 for java_file in urls_files if _declares_oauth_methods(_read_java_source(java_file))
    )
    oauth_utils = list(root.rglob("OAuthUtils.java"))
    build_configs = list(root.rglob("BuildConfig.java"))
    with_constants = 0
    for java_file in build_configs:
        content = _read_java_source(java_file)
        if BUILD_CONFIG_CLIENT_ID_RE.search(content) and BUILD_CONFIG_CLIENT_SECRET_RE.search(
            content
        ):
            with_constants += 1

    lines = []
    if urls_files:
        lines.append(
            f"Urls.java: {len(urls_files)} found, {with_methods} with clientId()/clientSecret()"
        )
    else:
        lines.append("Urls.java: none found")

    if oauth_utils:
        lines.append(f"OAuthUtils.java: {len(oauth_utils)} found")
    else:
        lines.append("OAuthUtils.java: none found")

    lines.append(f"AES key: {'found' if _find_oauth_aes_key(root) else 'not found'}")

    if build_configs:
        lines.append(
            f"BuildConfig.java: {len(build_configs)} found, "
            f"{with_constants} with OAUTH_CLIENT_ID/OAUTH_CLIENT_SECRET"
        )
    else:
        lines.append("BuildConfig.java: none found")

    return lines


def _extract_oauth_from_source(dir_path: str) -> str:
    """Return the best generated OAuth Basic header from the app sources."""
    candidate = _select_oauth_candidate(_extract_oauth_candidates_from_source(dir_path))
    return candidate.auth_basic if candidate else ""


def _find_credentials(strings: list[str]) -> dict[str, str]:
    """Search a list of strings for credential patterns."""
    creds: dict[str, str] = {
        "fermax_auth_url": "",
        "fermax_base_url": "",
        "fermax_auth_basic": "",
        "firebase_api_key": "",
        "firebase_sender_id": "",
        "firebase_app_id": "",
        "firebase_project_id": "",
        "firebase_package_name": "",
    }

    for s in strings:
        s = s.strip()

        # Firebase API key (AIza...)
        if not creds["firebase_api_key"]:
            m = re.search(r"(AIza[A-Za-z0-9_-]{35})", s)
            if m:
                creds["firebase_api_key"] = m.group(1)

        # Firebase App ID (1:NUMBER:android:HEX)
        if not creds["firebase_app_id"]:
            m = re.search(r"(1:\d{10,15}:android:[a-f0-9]{10,})", s)
            if m:
                creds["firebase_app_id"] = m.group(1)

        # Firebase Sender ID (large number, typically 12 digits)
        if not creds["firebase_sender_id"]:
            m = re.match(r"^(\d{12,15})$", s)
            if m:
                creds["firebase_sender_id"] = m.group(1)

        # Firebase Project ID (short slug like "fermax-blue")
        if (
            not creds["firebase_project_id"]
            and re.match(r"^[a-z][a-z0-9-]{3,30}$", s)
            and "fermax" in s
        ):
            creds["firebase_project_id"] = s

        # Firebase Package Name
        if not creds["firebase_package_name"] and re.match(r"^com\.fermax\.\w+\.\w+$", s):
            creds["firebase_package_name"] = s

        # Fermax OAuth URL
        if not creds["fermax_auth_url"]:
            m = re.search(r"(https://oauth-pro[^\s\"']*fermax\.io)", s)
            if m:
                creds["fermax_auth_url"] = m.group(1) + "/oauth/token"

        # Fermax Base URL
        if not creds["fermax_base_url"]:
            m = re.search(r"(https://pro-duoxme\.fermax\.io)", s)
            if m:
                creds["fermax_base_url"] = m.group(1)
            else:
                m = re.search(r"(https://pro[^\s\"']*\.fermax\.io)(?!/)", s)
                if m and "oauth" not in m.group(1) and "signaling" not in m.group(1):
                    creds["fermax_base_url"] = m.group(1)

        # Basic auth header
        if not creds["fermax_auth_basic"]:
            m = BASIC_AUTH_RE.search(s)
            if m:
                if _has_monitoring_or_tracing_context(s):
                    continue
                creds["fermax_auth_basic"] = m.group(1)

    # Try to derive OAuth URL from base URL if only base found
    if creds["fermax_base_url"] and not creds["fermax_auth_url"]:
        base = creds["fermax_base_url"]
        # pro-duoxme.fermax.io -> oauth-pro-duoxme.fermax.io
        oauth_host = base.replace("https://", "https://oauth-")
        creds["fermax_auth_url"] = oauth_host + "/oauth/token"

    # Derive base URL from auth URL if still missing
    # https://oauth-pro-duoxme.fermax.io/oauth/token -> https://pro-duoxme.fermax.io
    if creds["fermax_auth_url"] and not creds["fermax_base_url"]:
        m = re.match(r"(https://)oauth-(.+)/oauth/token", creds["fermax_auth_url"])
        if m:
            creds["fermax_base_url"] = m.group(1) + m.group(2)

    # Try to extract sender_id from app_id if missing
    if creds["firebase_app_id"] and not creds["firebase_sender_id"]:
        m = re.match(r"1:(\d+):android:", creds["firebase_app_id"])
        if m:
            creds["firebase_sender_id"] = m.group(1)

    return creds


def _search_android_strings_xml(path: str) -> dict[str, str]:
    """Extract Firebase credentials from Android res/values/strings.xml.

    Extracted Android resources live under resources/res/values/.
    The Firebase SDK embeds its config there (google_app_id, project_id,
    gcm_defaultSenderId, google_api_key) as named string resources.
    """
    root = Path(path)
    result: dict[str, str] = {}

    resource_map = {
        "google_app_id": "firebase_app_id",
        "project_id": "firebase_project_id",
        "gcm_defaultSenderId": "firebase_sender_id",
        "google_api_key": "firebase_api_key",
    }

    for xml_file in root.rglob("strings.xml"):
        try:
            content = xml_file.read_text(errors="ignore")
        except OSError:
            continue
        for res_name, cred_key in resource_map.items():
            if result.get(cred_key):
                continue
            m = re.search(rf'<string name="{re.escape(res_name)}">([^<]+)</string>', content)
            if m:
                result[cred_key] = m.group(1)

    return result


def _search_google_services_json(path: str) -> dict[str, str]:
    """Try to parse google-services.json if found."""
    root = Path(path)
    targets = list(root.rglob("google-services.json")) if root.is_dir() else []

    if not targets and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if "google-services" in name and name.endswith(".json"):
                    try:
                        gs = json.loads(zf.read(name))
                        return _parse_google_services(gs)
                    except (json.JSONDecodeError, KeyError):
                        continue

    for target in targets:
        try:
            gs = json.loads(target.read_text())
            return _parse_google_services(gs)
        except (json.JSONDecodeError, KeyError):
            continue

    return {}


def _parse_google_services(gs: dict) -> dict[str, str]:
    """Parse a google-services.json dict into our credential format."""
    project_info = gs.get("project_info", {})
    client = gs.get("client", [{}])[0]
    client_info = client.get("client_info", {})

    return {
        "firebase_api_key": client.get("api_key", [{}])[0].get("current_key", ""),
        "firebase_sender_id": str(project_info.get("project_number", "")),
        "firebase_app_id": client_info.get("mobilesdk_app_id", ""),
        "firebase_project_id": project_info.get("project_id", ""),
        "firebase_package_name": client_info.get("android_client_info", {}).get("package_name", ""),
    }


def _display_credential_value(key: str, value: str) -> str:
    """Return a console-safe representation of an extracted credential."""
    if not value:
        return value
    if key == "fermax_auth_basic":
        return "Basic [redacted]"
    if key == "firebase_api_key":
        return "[redacted]"
    return str(value)[:50] + "..." if len(str(value)) > 50 else value


def main() -> None:
    args = sys.argv[1:]

    # Manual mode: build the Basic header from OAuth client id/secret the user
    # already has, without needing the APK at all.
    try:
        manual_header = _auth_basic_from_args(args)
    except ValueError as error:
        print(f"Error: {error}")
        sys.exit(1)
    if manual_header is not None:
        print("fermax_auth_basic (paste this into the integration):")
        print()
        print(f"  {manual_header}")
        print()
        print("This is the Authorization header the login endpoint expects. Keep")
        print("it private and never share it. If login still fails, double-check")
        print("that the client id and secret came from the Fermax Blue app and")
        print("not from a tracing/monitoring value.")
        sys.exit(0)

    if not args:
        print(f"Usage: {sys.argv[0]} <path-to-fermax-blue.apk-or-sources-dir>")
        print(f"   or: {sys.argv[0]} --client-id <ID> --client-secret <SECRET>")
        print()
        print("Extracts API and Firebase credentials from the Fermax Blue APK.")
        print("Accepts either an .apk file or an app sources directory.")
        print("With --client-id/--client-secret it only builds the Basic auth header")
        print("from OAuth values you already have.")
        print("Output: credentials.json")
        sys.exit(1)

    target = args[0]
    target_path = Path(target)
    if not target_path.exists():
        print(f"Error: not found: {target}")
        sys.exit(1)

    print(f"Analyzing {target}...")
    all_strings: list[str] = []

    if target_path.is_file() and zipfile.is_zipfile(target):
        # APK file
        print("  Extracting strings from resources.arsc...")
        all_strings.extend(_extract_strings_from_arsc(target))
        print(f"    Found {len(all_strings)} strings")

        print("  Extracting strings from DEX files...")
        dex_strings = _extract_strings_from_dex(target)
        all_strings.extend(dex_strings)
        print(f"    Found {len(dex_strings)} strings")

    elif target_path.is_dir():
        # App sources directory
        print("  Scanning app source files...")
        all_strings.extend(_search_sources_dir(target))
        print(f"    Found {len(all_strings)} string literals")

    else:
        print(f"Error: {target} is not an APK file or directory")
        sys.exit(1)

    # Try google-services.json first
    print("  Looking for google-services.json...")
    gs_creds = _search_google_services_json(target)
    if gs_creds:
        print(f"    Found {sum(1 for v in gs_creds.values() if v)} Firebase values")

    # Try Android strings.xml (present in app sources directories)
    print("  Looking for Android strings.xml resources...")
    xml_creds = _search_android_strings_xml(target)
    if xml_creds:
        print(f"    Found {sum(1 for v in xml_creds.values() if v)} Firebase values")

    # Pattern-match all collected strings
    print("  Pattern matching credentials...")
    creds = _find_credentials(all_strings)

    # A Basic literal found by the generic scan is not a credential: recent APKs
    # carry the telemetry header, which the login endpoint rejects with
    # invalid_client. Set it aside and only publish a source-backed header.
    unverified_auth_basic = creds["fermax_auth_basic"]
    creds["fermax_auth_basic"] = ""
    auth_basic_source = ""

    # Merge google-services results (higher priority)
    for k, v in gs_creds.items():
        if v:
            creds[k] = v

    # Merge Android resource values (higher priority than pattern matching)
    for k, v in xml_creds.items():
        if v:
            creds[k] = v

    # Try to decrypt OAuth credentials from OAuthUtils.java (AES-encrypted)
    sources_dir = target if target_path.is_dir() else None
    if not sources_dir and target_path.is_file():
        # Check for a pre-existing sources dir next to the app package
        possible = Path(target).with_suffix("")
        if possible.is_dir() and (possible / "sources").is_dir():
            sources_dir = str(possible)

    if sources_dir:
        print("  Reading OAuth credentials from source...")
        oauth_candidates = _extract_oauth_candidates_from_source(sources_dir)
        oauth_candidate = _select_oauth_candidate(oauth_candidates)
        if oauth_candidate:
            creds["fermax_auth_basic"] = oauth_candidate.auth_basic
            auth_basic_source = f"{oauth_candidate.source} ({oauth_candidate.label})"
            if oauth_candidate.auth_url:
                creds["fermax_auth_url"] = oauth_candidate.auth_url
            if oauth_candidate.base_url:
                creds["fermax_base_url"] = oauth_candidate.base_url
            print(f"    OK ({auth_basic_source})")
            if len(oauth_candidates) > 1:
                labels = ", ".join(candidate.label for candidate in oauth_candidates)
                print(f"    Found {len(oauth_candidates)} OAuth environments: {labels}")
        else:
            print("    Not found or decryption failed")
            for line in _oauth_source_diagnostics(sources_dir):
                print(f"      {line}")

    # Report
    print()
    found = sum(1 for v in creds.values() if v)
    total = len(creds)
    print(f"Results: {found}/{total} credentials found")
    print()

    for key, value in creds.items():
        status = "OK" if value else "MISSING"
        display = _display_credential_value(key, value)
        print(f"  {status:7s} {key}: {display}")

    # Report where the header came from — or why a literal was not trusted.
    if creds["fermax_auth_basic"]:
        print()
        print(f"  NOTE: fermax_auth_basic was generated from {auth_basic_source}.")
        print("  Keep credentials.json private and never publish the generated")
        print("  Basic header or Firebase values.")
    elif unverified_auth_basic:
        print()
        print("  IGNORED: a Basic header was found by the generic string scan, but")
        print("  it was not read from the OAuth sources, so it is not reported as a")
        print("  credential and was not saved. APKs carry unrelated Basic headers for")
        print("  tracing/monitoring (TraceManagerOtelImpl, /monitoring/v1/traces, the")
        print("  TRACING_BASIC_AUTH constant), and the login endpoint answers")
        print("  'invalid_client' for those. Extract the app sources and run this script")
        print("  against the output directory: it reads OAuthUtils.java +")
        print("  Urls.clientId()/clientSecret() (APK <= 4.3.0) or the")
        print("  OAUTH_CLIENT_ID/OAUTH_CLIENT_SECRET constants in BuildConfig.java")
        print("  (APK 4.3.4+).")

    if found < total:
        missing = [k for k, v in creds.items() if not v]
        print()
        print(f"Missing: {', '.join(missing)}")
        print("Try running the script against the app sources directory instead.")
        print("See README.md for manual extraction instructions.")

    # Write output
    output_path = Path("credentials.json")
    output_path.write_text(json.dumps(creds, indent=2) + "\n")
    print(f"\nSaved to {output_path}")

    if found == total:
        print("\nAll credentials found. Verify fermax_auth_basic works before")
        print("using it in integration setup.")


if __name__ == "__main__":
    main()
