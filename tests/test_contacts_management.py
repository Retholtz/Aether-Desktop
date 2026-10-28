import unittest
from unittest.mock import MagicMock, patch
from core.oauth_manager import (
    GOOGLE_SCOPES,
    MICROSOFT_SCOPES,
    OAuthManager,
    parse_address_components,
)
from tools.payload_sanitizer import EXEMPT_TOOLS
from tools.dispatcher import _CUSTOM_TOOL_DECLARATIONS, _CUSTOM_TOOL_HANDLERS
from tools.email_tools import get_email_tool_declarations


class TestContactsManagement(unittest.TestCase):
    def test_oauth_scopes(self):
        """Verify write scopes are configured for both Google and Microsoft."""
        self.assertIn("https://www.googleapis.com/auth/contacts", GOOGLE_SCOPES)
        self.assertNotIn("https://www.googleapis.com/auth/contacts.readonly", GOOGLE_SCOPES)
        self.assertIn("Contacts.ReadWrite", MICROSOFT_SCOPES)
        self.assertNotIn("Contacts.Read", MICROSOFT_SCOPES)

    def test_parse_address_components(self):
        """Verify natural address parsing into street, city, region, postal_code."""
        addr1 = parse_address_components("112 Long Meadow Hill Road, Brookfield, CT 06804")
        self.assertEqual(addr1.get("street"), "112 Long Meadow Hill Road")
        self.assertEqual(addr1.get("city"), "Brookfield")
        self.assertEqual(addr1.get("region"), "CT")
        self.assertEqual(addr1.get("postal_code"), "06804")

        addr2 = parse_address_components("112 Long Meadow Hill Road, Brookfield, Connecticut")
        self.assertEqual(addr2.get("street"), "112 Long Meadow Hill Road")
        self.assertEqual(addr2.get("city"), "Brookfield")
        self.assertEqual(addr2.get("region"), "Connecticut")

        addr3 = parse_address_components("6828 Hampsford Circle, Toledo, Ohio")
        self.assertEqual(addr3.get("street"), "6828 Hampsford Circle")
        self.assertEqual(addr3.get("city"), "Toledo")
        self.assertEqual(addr3.get("region"), "Ohio")

    def test_tool_registration_and_exemption(self):
        """Verify update_contact and create_contact are registered and exempt from aggressive truncation."""
        self.assertIn("update_contact", _CUSTOM_TOOL_DECLARATIONS)
        self.assertIn("update_contact", _CUSTOM_TOOL_HANDLERS)
        self.assertIn("create_contact", _CUSTOM_TOOL_DECLARATIONS)
        self.assertIn("create_contact", _CUSTOM_TOOL_HANDLERS)
        self.assertIn("update_contact", EXEMPT_TOOLS)
        self.assertIn("create_contact", EXEMPT_TOOLS)

        decls = get_email_tool_declarations()
        names = [d.name for d in decls]
        self.assertIn("update_contact", names)
        self.assertIn("create_contact", names)

    def test_google_update_contact_success(self):
        """Verify successful contact update via Google People API."""
        mgr = OAuthManager()
        with patch.object(mgr, "resolve_provider", return_value="google"), \
             patch.object(mgr, "_google_headers", return_value={"Authorization": "Bearer fake_token"}):
            def fake_get(url, headers=None, params=None, timeout=10):
                resp = MagicMock()
                resp.ok = True
                resp.status_code = 200
                if "searchContacts" in url:
                    resp.json.return_value = {
                        "results": [
                            {
                                "person": {
                                    "resourceName": "people/c12345",
                                    "etag": "%etag123",
                                    "names": [{"displayName": "Michelle Sands"}],
                                    "addresses": [],
                                }
                            }
                        ]
                    }
                elif "people/c12345" in url:
                    resp.json.return_value = {
                        "resourceName": "people/c12345",
                        "etag": "%etag123",
                        "names": [{"displayName": "Michelle Sands"}],
                        "addresses": [],
                    }
                return resp

            patch_calls = []

            def fake_patch(url, headers=None, json=None, timeout=10):
                patch_calls.append((url, json))
                resp = MagicMock()
                resp.ok = True
                resp.status_code = 200
                resp.json.return_value = {"resourceName": "people/c12345"}
                return resp

            with patch("core.oauth_manager.requests.get", side_effect=fake_get), \
                 patch("core.oauth_manager.requests.patch", side_effect=fake_patch):
                res = mgr.update_contact(
                    name_or_query="Michelle Sands",
                    address="112 Long Meadow Hill Road, Brookfield, CT",
                    provider="google",
                )
                self.assertTrue(res["success"])
                self.assertEqual(res["provider"], "google")
                self.assertEqual(res["name"], "Michelle Sands")
                self.assertEqual(len(patch_calls), 1)
                patch_url, patch_body = patch_calls[0]
                self.assertIn("people/c12345:updateContact", patch_url)
                self.assertIn("updatePersonFields=addresses", patch_url)
                self.assertEqual(patch_body["etag"], "%etag123")
                self.assertEqual(len(patch_body["addresses"]), 1)
                self.assertEqual(patch_body["addresses"][0]["formattedValue"], "112 Long Meadow Hill Road, Brookfield, CT")
                self.assertEqual(patch_body["addresses"][0]["city"], "Brookfield")

    def test_google_update_contact_403_insufficient_permissions(self):
        """Verify actionable error message when Google returns 403 on older read-only tokens."""
        mgr = OAuthManager()
        with patch.object(mgr, "resolve_provider", return_value="google"), \
             patch.object(mgr, "_google_headers", return_value={"Authorization": "Bearer fake_token"}):
            def fake_get(url, headers=None, params=None, timeout=10):
                resp = MagicMock()
                resp.ok = True
                resp.status_code = 200
                if "searchContacts" in url:
                    resp.json.return_value = {
                        "results": [
                            {
                                "person": {
                                    "resourceName": "people/c12345",
                                    "etag": "%etag123",
                                    "names": [{"displayName": "Scott Sands"}],
                                }
                            }
                        ]
                    }
                elif "people/c12345" in url:
                    resp.ok = False
                    resp.status_code = 403
                    resp.text = "Insufficient Permission"
                return resp

            with patch("core.oauth_manager.requests.get", side_effect=fake_get):
                res = mgr.update_contact(
                    name_or_query="Scott Sands",
                    address="112 Long Meadow Hill Road, Brookfield, CT",
                    provider="google",
                )
                self.assertFalse(res["success"])
                self.assertIn("403 Forbidden", res["error"])
                self.assertIn("Reconnect", res["error"])

    def test_microsoft_update_contact_success(self):
        """Verify successful contact update via Microsoft Graph API."""
        mgr = OAuthManager()
        with patch.object(mgr, "resolve_provider", return_value="microsoft"), \
             patch.object(mgr, "_microsoft_headers", return_value={"Authorization": "Bearer fake_token"}):
            def fake_get(url, headers=None, params=None, timeout=10):
                resp = MagicMock()
                resp.ok = True
                resp.status_code = 200
                resp.json.return_value = {
                    "value": [
                        {
                            "id": "AAMkAGI2...",
                            "displayName": "Scott Sands",
                            "emailAddresses": [{"address": "scott@example.com"}],
                        }
                    ]
                }
                return resp

            patch_calls = []

            def fake_patch(url, headers=None, json=None, timeout=10):
                patch_calls.append((url, json))
                resp = MagicMock()
                resp.ok = True
                resp.status_code = 200
                return resp

            with patch("core.oauth_manager.requests.get", side_effect=fake_get), \
                 patch("core.oauth_manager.requests.patch", side_effect=fake_patch):
                res = mgr.update_contact(
                    name_or_query="Scott Sands",
                    address="112 Long Meadow Hill Road, Brookfield, CT",
                    provider="microsoft",
                )
                self.assertTrue(res["success"])
                self.assertEqual(res["provider"], "microsoft")
                self.assertEqual(len(patch_calls), 1)
                patch_url, patch_body = patch_calls[0]
                self.assertIn("/me/contacts/AAMkAGI2...", patch_url)
                self.assertIn("homeAddress", patch_body)
                self.assertEqual(patch_body["homeAddress"]["street"], "112 Long Meadow Hill Road")
                self.assertEqual(patch_body["homeAddress"]["city"], "Brookfield")


    def test_google_create_contact_success(self):
        """Verify successful contact creation via Google People API."""
        mgr = OAuthManager()
        with patch.object(mgr, "resolve_provider", return_value="google"), \
             patch.object(mgr, "_google_headers", return_value={"Authorization": "Bearer fake_token"}):
            post_calls = []

            def fake_post(url, headers=None, json=None, timeout=10):
                post_calls.append((url, json))
                resp = MagicMock()
                resp.ok = True
                resp.status_code = 200
                resp.json.return_value = {"resourceName": "people/c98765"}
                return resp

            with patch("core.oauth_manager.requests.post", side_effect=fake_post):
                res = mgr.create_contact(
                    name="John Doe",
                    address="100 Main St, Danbury, CT",
                    phone="203-555-0100",
                    email="john@example.com",
                    provider="google",
                )
                self.assertTrue(res["success"])
                self.assertEqual(res["provider"], "google")
                self.assertEqual(res["resource_name"], "people/c98765")
                self.assertEqual(len(post_calls), 1)
                post_url, post_body = post_calls[0]
                self.assertIn("people:createContact", post_url)
                self.assertEqual(post_body["names"][0]["givenName"], "John")
                self.assertEqual(post_body["names"][0]["familyName"], "Doe")
                self.assertEqual(post_body["addresses"][0]["city"], "Danbury")

    def test_microsoft_create_contact_success(self):
        """Verify successful contact creation via Microsoft Graph API."""
        mgr = OAuthManager()
        with patch.object(mgr, "resolve_provider", return_value="microsoft"), \
             patch.object(mgr, "_microsoft_headers", return_value={"Authorization": "Bearer fake_token"}):
            post_calls = []

            def fake_post(url, headers=None, json=None, timeout=10):
                post_calls.append((url, json))
                resp = MagicMock()
                resp.ok = True
                resp.status_code = 200
                resp.json.return_value = {"id": "AAMk_NEW_123"}
                return resp

            with patch("core.oauth_manager.requests.post", side_effect=fake_post):
                res = mgr.create_contact(
                    name="Jane Smith",
                    address="200 Elm St, Brookfield, CT",
                    phone="203-555-0199",
                    email="jane@example.com",
                    provider="microsoft",
                )
                self.assertTrue(res["success"])
                self.assertEqual(res["provider"], "microsoft")
                self.assertEqual(res["id"], "AAMk_NEW_123")
                self.assertEqual(len(post_calls), 1)
                post_url, post_body = post_calls[0]
                self.assertIn("/me/contacts", post_url)
                self.assertEqual(post_body["givenName"], "Jane")
                self.assertEqual(post_body["surname"], "Smith")
                self.assertEqual(post_body["homeAddress"]["city"], "Brookfield")


if __name__ == "__main__":
    unittest.main()
