#!/usr/bin/env python3
"""Extract API and Firebase credentials from the Fermax Blue Android APK.

Usage:
    python scripts/extract_credentials.py /path/to/fermax-blue.apk
    python scripts/extract_credentials.py /path/to/decompiled-dir/

Extracts:
  - Firebase credentials from resources.arsc / google-services.json
  - Fermax API URLs and OAuth client credentials from Java source

Output: a credentials.json file ready for use with the integration.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path


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


def _search_decompiled_dir(dir_path: str) -> list[str]:
    """Collect relevant strings from a decompiled source directory."""
    strings: list[str] = []
    root = Path(dir_path)

    for java_file in root.rglob("*.java"):
        try:
            content = java_file.read_text(errors="ignore")
            strings.extend(re.findall(r'"([^"]{10,300})"', content))
        except OSError:
            continue

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


def _extract_oauth_from_source(dir_path: str) -> str:
    """Try to decrypt OAuth Basic auth from OAuthUtils + Urls.java source.

    The Fermax Blue APK encrypts the OAuth client_id and client_secret
    with AES using a hardcoded key in OAuthUtils.decrypt(). This function
    finds the AES key, the encrypted byte arrays for the production
    environment, decrypts them, and constructs the Basic auth header.
    """
    import base64

    root = Path(dir_path)

    # Find OAuthUtils.java to get the AES key
    aes_key: bytes | None = None
    for f in root.rglob("OAuthUtils.java"):
        content = f.read_text(errors="ignore")
        # Match: new byte[]{98, 52, -29, ...}  (the SecretKeySpec bytes)
        m = re.search(
            r"SecretKeySpec\(new byte\[]\{([^}]+)\}",
            content,
        )
        if m:
            aes_key = _parse_java_byte_list(m.group(1))
            break

    if not aes_key:
        return ""

    # Find Urls.java to get the encrypted clientId/clientSecret for production
    for f in root.rglob("Urls.java"):
        content = f.read_text(errors="ignore")
        break
    else:
        return ""

    # Parse the production clientId and clientSecret byte arrays
    # Production is typically the last case (i == 4 || i == 5)
    def _parse_byte_arrays(method_name: str) -> list[bytes]:
        """Extract all byte array literals from a method.

        Previously used 'throw new NoWhenBranch' as the end anchor, which
        caused the production (PRO) arrays to be silently dropped: in JADX
        output the PRO case appears *after* the throw statement, not before.

        Fix: use the next method declaration (or end-of-file) as the boundary
        so all environments — including PRO — are captured.

        Also resolves local Byte variables (e.g. ``Byte bValueOf = Byte.valueOf(Ascii.NAK)``)
        by scanning the method body instead of relying on hardcoded heuristics.
        """
        # Match from method signature to the next method declaration or EOF.
        # The PRO environment's return statement follows the NoWhenBranch throw,
        # so the old anchor missed it entirely.
        m = re.search(
            rf"public final Byte\[] {method_name}\(\)(.*?)(?=\n    public |\Z)",
            content,
            re.DOTALL,
        )
        if not m:
            return []
        method_body = m.group(1)

        # Build a local-variable lookup from the method body.
        # Handles: Byte bValueOf = Byte.valueOf(Ascii.NAK);
        local_vars: dict[str, int] = {}
        for lv in re.finditer(
            r"Byte\s+(\w+)\s*=\s*Byte\.valueOf\((?:Ascii\.(\w+)|(-?\d+))\)",
            method_body,
        ):
            var_name, ascii_name, raw_int = lv.group(1), lv.group(2), lv.group(3)
            if ascii_name and ascii_name in _ASCII_MAP:
                local_vars[var_name] = _ASCII_MAP[ascii_name]
            elif raw_int:
                local_vars[var_name] = int(raw_int) & 0xFF

        arrays: list[bytes] = []
        for arr_match in re.finditer(r"new Byte\[]\{([^}]+)\}", method_body):
            resolved: list[int] = []
            for token in arr_match.group(1).split(","):
                token = token.strip()
                if token in local_vars:
                    resolved.append(local_vars[token])
                else:
                    resolved.append(_parse_java_byte_token(token))
            arrays.append(bytes(resolved))
        return arrays

    client_id_arrays = _parse_byte_arrays("clientId")
    client_secret_arrays = _parse_byte_arrays("clientSecret")

    if not client_id_arrays or not client_secret_arrays:
        return ""

    # Production is the last array (i == 4 || i == 5)
    prod_client_id_enc = client_id_arrays[-1]
    prod_client_secret_enc = client_secret_arrays[-1]

    # Decrypt with AES ECB
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        cipher = Cipher(algorithms.AES(aes_key), modes.ECB())
        decryptor = cipher.decryptor()
        client_id = decryptor.update(prod_client_id_enc) + decryptor.finalize()
        decryptor2 = cipher.decryptor()
        client_secret = (
            decryptor2.update(prod_client_secret_enc) + decryptor2.finalize()
        )
        # Remove PKCS padding
        client_id = client_id[: -client_id[-1]].decode("utf-8")
        client_secret = client_secret[: -client_secret[-1]].decode("utf-8")
    except ImportError:
        # Fallback: use PyCryptodome or built-in
        try:
            from Crypto.Cipher import AES as _AES  # type: ignore[import-untyped]

            cipher = _AES.new(aes_key, _AES.MODE_ECB)  # type: ignore[assignment]
            client_id = cipher.decrypt(prod_client_id_enc)  # type: ignore[assignment]
            client_id = client_id[: -client_id[-1]].decode("utf-8")  # type: ignore[index,arg-type]
            cipher2 = _AES.new(aes_key, _AES.MODE_ECB)  # type: ignore[assignment]
            client_secret = cipher2.decrypt(prod_client_secret_enc)  # type: ignore[assignment]
            client_secret = client_secret[: -client_secret[-1]].decode("utf-8")  # type: ignore[index,arg-type]
        except ImportError:
            return ""

    # Build Basic auth header
    cred_str = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(cred_str.encode()).decode()
    return f"Basic {encoded}"


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
        if not creds["firebase_package_name"] and re.match(
            r"^com\.fermax\.\w+\.\w+$", s
        ):
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
            m = re.search(r"(Basic\s+[A-Za-z0-9+/=]{50,})", s)
            if m:
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

    JADX places compiled Android resources under resources/res/values/.
    The Firebase SDK embeds its config there (google_app_id, project_id,
    gcm_defaultSenderId, google_api_key) as named string resources, which
    are not present in .java or google-services.json in a decompiled dir.
    """
    root = Path(path)
    result: dict[str, str] = {}

    # Mapping from Android resource name to our credential key
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
            m = re.search(
                rf'<string name="{re.escape(res_name)}">([^<]+)</string>', content
            )
            if m:
                result[cred_key] = m.group(1).strip()

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
        "firebase_package_name": client_info.get("android_client_info", {}).get(
            "package_name", ""
        ),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <path-to-fermax-blue.apk-or-decompiled-dir>")
        print()
        print("Extracts API and Firebase credentials from the Fermax Blue APK.")
        print("Accepts either an .apk file or a decompiled directory (JADX/apktool).")
        print("Output: credentials.json")
        sys.exit(1)

    target = sys.argv[1]
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
        # Decompiled directory
        print("  Scanning decompiled source files...")
        all_strings.extend(_search_decompiled_dir(target))
        print(f"    Found {len(all_strings)} string literals")

    else:
        print(f"Error: {target} is not an APK file or directory")
        sys.exit(1)

    # Try google-services.json first
    print("  Looking for google-services.json...")
    gs_creds = _search_google_services_json(target)
    if gs_creds:
        print(f"    Found {sum(1 for v in gs_creds.values() if v)} Firebase values")

    # Try Android strings.xml (present in JADX-decompiled directories)
    print("  Looking for Android strings.xml resources...")
    xml_creds = _search_android_strings_xml(target)
    if xml_creds:
        print(f"    Found {sum(1 for v in xml_creds.values() if v)} Firebase values")

    # Pattern-match all collected strings
    print("  Pattern matching credentials...")
    creds = _find_credentials(all_strings)

    # Merge google-services results (higher priority)
    for k, v in gs_creds.items():
        if v:
            creds[k] = v

    # Merge Android resource values (higher priority than pattern matching)
    for k, v in xml_creds.items():
        if v:
            creds[k] = v

    # Try to decrypt OAuth credentials from OAuthUtils.java (AES-encrypted)
    decompiled_dir = target if target_path.is_dir() else None
    if not decompiled_dir and target_path.is_file():
        # Check for a pre-existing decompiled dir next to the APK
        possible = Path(target).with_suffix("")
        if possible.is_dir() and (possible / "sources").is_dir():
            decompiled_dir = str(possible)

    if decompiled_dir:
        print("  Decrypting OAuth credentials from source...")
        oauth_basic = _extract_oauth_from_source(decompiled_dir)
        if oauth_basic:
            creds["fermax_auth_basic"] = oauth_basic
            print("    OK (decrypted from OAuthUtils.java)")
        else:
            print("    Not found or decryption failed")

    # Report
    print()
    found = sum(1 for v in creds.values() if v)
    total = len(creds)
    print(f"Results: {found}/{total} credentials found")
    print()

    for key, value in creds.items():
        status = "OK" if value else "MISSING"
        display = str(value)[:50] + "..." if len(str(value)) > 50 else value
        print(f"  {status:7s} {key}: {display}")

    # Warn about auth_basic — the APK may contain multiple OAuth clients
    # and the one found by pattern matching may not be the correct one
    if creds["fermax_auth_basic"]:
        print()
        print("  WARNING: fermax_auth_basic was found automatically, but the APK may")
        print("  contain multiple OAuth clients. If authentication fails with")
        print("  'invalid_client', you need to extract the correct one manually.")
        print("  Use JADX to decompile and search for the OAuth client_id:secret")
        print("  used in the login/authentication flow. See README.md for details.")

    if found < total:
        missing = [k for k, v in creds.items() if not v]
        print()
        print(f"Missing: {', '.join(missing)}")
        print("Try decompiling with JADX and running against the output directory.")
        print("See README.md for manual extraction instructions.")

    # Write output
    output_path = Path("credentials.json")
    output_path.write_text(json.dumps(creds, indent=2) + "\n")
    print(f"\nSaved to {output_path}")

    if found == total:
        print("\nAll credentials found. Verify fermax_auth_basic works before")
        print("using in integration setup (see WARNING above).")


if __name__ == "__main__":
    main()
