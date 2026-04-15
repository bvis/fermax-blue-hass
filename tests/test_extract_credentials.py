"""Tests for the credential extraction script."""

import json
import sys
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def script_module():
    """Import the extraction script as a module."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import extract_credentials

    yield extract_credentials
    sys.path.pop(0)


@pytest.fixture
def fake_apk(tmp_path: Path) -> Path:
    """Create a minimal fake APK with embedded credential strings."""
    apk_path = tmp_path / "test.apk"

    # Build a fake resources.arsc with recognizable strings
    strings = [
        b"AIza" + b"X" * 35,  # 39 chars (AIza + 35)
        b"1:123456789012:android:abcdef1234567890",
        b"123456789012",
        b"test-fermax-project",
        b"com.fermax.blue.app",
        b"https://oauth-pro-duoxme.fermax.io",
        b"https://pro-duoxme.fermax.io",
        b"Basic " + b"A" * 60,
    ]
    arsc_content = b"\x00" * 50
    for s in strings:
        arsc_content += b"\x00\x00" + s + b"\x00\x00"
    arsc_content += b"\x00" * 50

    with zipfile.ZipFile(apk_path, "w") as zf:
        zf.writestr("resources.arsc", arsc_content)
        zf.writestr("classes.dex", b"")

    return apk_path


@pytest.fixture
def fake_decompiled(tmp_path: Path) -> Path:
    """Create a minimal decompiled directory with Java source."""
    src = tmp_path / "sources" / "com" / "fermax" / "blue"
    src.mkdir(parents=True)

    (src / "Urls.java").write_text(
        'String url = "https://oauth-pro-duoxme.fermax.io";\n'
        'String base = "https://pro-duoxme.fermax.io";\n'
    )
    (src / "Auth.java").write_text(
        'String auth = "Basic ' + "B" * 60 + '";\nString pkg = "com.fermax.blue.app";\n'
    )

    return tmp_path


class TestExtractFromApk:
    """Test credential extraction from APK files."""

    def test_extracts_firebase_api_key(self, script_module, fake_apk):
        strings = script_module._extract_strings_from_arsc(str(fake_apk))
        creds = script_module._find_credentials(strings)
        assert creds["firebase_api_key"] == "AIza" + "X" * 35

    def test_extracts_firebase_app_id(self, script_module, fake_apk):
        strings = script_module._extract_strings_from_arsc(str(fake_apk))
        creds = script_module._find_credentials(strings)
        assert creds["firebase_app_id"] == "1:123456789012:android:abcdef1234567890"

    def test_extracts_sender_id_from_app_id(self, script_module, fake_apk):
        strings = script_module._extract_strings_from_arsc(str(fake_apk))
        creds = script_module._find_credentials(strings)
        assert creds["firebase_sender_id"] == "123456789012"

    def test_extracts_firebase_project_id(self, script_module, fake_apk):
        strings = script_module._extract_strings_from_arsc(str(fake_apk))
        creds = script_module._find_credentials(strings)
        assert "fermax" in creds["firebase_project_id"]

    def test_extracts_package_name(self, script_module, fake_apk):
        strings = script_module._extract_strings_from_arsc(str(fake_apk))
        creds = script_module._find_credentials(strings)
        assert creds["firebase_package_name"] == "com.fermax.blue.app"

    def test_extracts_fermax_auth_url(self, script_module, fake_apk):
        strings = script_module._extract_strings_from_arsc(str(fake_apk))
        creds = script_module._find_credentials(strings)
        assert "oauth" in creds["fermax_auth_url"]
        assert "fermax.io" in creds["fermax_auth_url"]

    def test_extracts_fermax_base_url(self, script_module, fake_apk):
        strings = script_module._extract_strings_from_arsc(str(fake_apk))
        creds = script_module._find_credentials(strings)
        assert creds["fermax_base_url"] == "https://pro-duoxme.fermax.io"

    def test_extracts_auth_basic(self, script_module, fake_apk):
        strings = script_module._extract_strings_from_arsc(str(fake_apk))
        creds = script_module._find_credentials(strings)
        assert creds["fermax_auth_basic"].startswith("Basic ")


class TestExtractFromDecompiled:
    """Test credential extraction from decompiled directories."""

    def test_extracts_urls(self, script_module, fake_decompiled):
        strings = script_module._search_decompiled_dir(str(fake_decompiled))
        creds = script_module._find_credentials(strings)
        assert "oauth" in creds["fermax_auth_url"]
        assert creds["fermax_base_url"] == "https://pro-duoxme.fermax.io"

    def test_extracts_auth_basic(self, script_module, fake_decompiled):
        strings = script_module._search_decompiled_dir(str(fake_decompiled))
        creds = script_module._find_credentials(strings)
        assert creds["fermax_auth_basic"].startswith("Basic ")

    def test_extracts_package_name(self, script_module, fake_decompiled):
        strings = script_module._search_decompiled_dir(str(fake_decompiled))
        creds = script_module._find_credentials(strings)
        assert creds["firebase_package_name"] == "com.fermax.blue.app"


class TestGoogleServicesJson:
    """Test google-services.json parsing."""

    def test_parses_google_services(self, script_module, tmp_path):
        gs = {
            "project_info": {
                "project_number": "123456789",
                "project_id": "my-project",
            },
            "client": [
                {
                    "client_info": {
                        "mobilesdk_app_id": "1:123:android:abc",
                        "android_client_info": {"package_name": "com.test.app"},
                    },
                    "api_key": [{"current_key": "AIzaTestKey"}],
                }
            ],
        }
        result = script_module._parse_google_services(gs)
        assert result["firebase_api_key"] == "AIzaTestKey"
        assert result["firebase_sender_id"] == "123456789"
        assert result["firebase_app_id"] == "1:123:android:abc"
        assert result["firebase_project_id"] == "my-project"
        assert result["firebase_package_name"] == "com.test.app"

    def test_google_services_in_apk(self, script_module, tmp_path):
        gs = {
            "project_info": {"project_number": "999", "project_id": "proj"},
            "client": [
                {
                    "client_info": {
                        "mobilesdk_app_id": "1:999:android:fff",
                        "android_client_info": {"package_name": "com.x.y"},
                    },
                    "api_key": [{"current_key": "AIzaFromJson"}],
                }
            ],
        }
        apk_path = tmp_path / "gs.apk"
        with zipfile.ZipFile(apk_path, "w") as zf:
            zf.writestr("res/google-services.json", json.dumps(gs))
            zf.writestr("resources.arsc", b"\x00" * 100)

        result = script_module._search_google_services_json(str(apk_path))
        assert result["firebase_api_key"] == "AIzaFromJson"


class TestPatternMatching:
    """Test credential pattern matching edge cases."""

    def test_derives_auth_url_from_base(self, script_module):
        strings = ["https://pro-duoxme.fermax.io"]
        creds = script_module._find_credentials(strings)
        assert creds["fermax_auth_url"] == ("https://oauth-pro-duoxme.fermax.io/oauth/token")

    def test_derives_sender_id_from_app_id(self, script_module):
        strings = ["1:999888777666:android:aabbccddee"]
        creds = script_module._find_credentials(strings)
        assert creds["firebase_sender_id"] == "999888777666"

    def test_ignores_signaling_url_as_base(self, script_module):
        strings = [
            "http://signaling-pro-duoxme.fermax.io",
            "https://pro-duoxme.fermax.io",
        ]
        creds = script_module._find_credentials(strings)
        assert creds["fermax_base_url"] == "https://pro-duoxme.fermax.io"

    def test_empty_input(self, script_module):
        creds = script_module._find_credentials([])
        assert all(v == "" for v in creds.values())
