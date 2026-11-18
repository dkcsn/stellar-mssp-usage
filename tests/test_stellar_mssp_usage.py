import datetime as dt
import unittest

import stellar_mssp_usage as usage


class LicenseExpiryTests(unittest.TestCase):
    def test_date_accepts_seconds_milliseconds_and_iso(self):
        self.assertEqual(usage._date(1798761600), "2027-01-01")
        self.assertEqual(usage._date(1798761600000), "2027-01-01")
        self.assertEqual(usage._date("2027-01-01T12:00:00Z"), "2027-01-01")

    def test_prefers_common_active_full_protection_expiry(self):
        agents = [
            self.agent("2027-01-01T00:00:00Z"),
            self.agent("2027-01-01T00:00:00Z"),
            self.agent("2026-12-01T00:00:00Z", licenseType="LICENSE_TYPE_TRIAL"),
            self.agent("2028-01-01T00:00:00Z", agentEdition="AGENT_EDITION_SENSOR"),
        ]
        self.assertEqual(usage.console_license_expiry(agents), dt.date(2027, 1, 1))

    def test_warns_from_sixty_days_before_expiry(self):
        agents = [self.agent("2027-01-01T00:00:00Z")]
        line, alert = usage.license_expiry_summary(agents, dt.date(2026, 11, 2))
        self.assertEqual(line, "License expires 2027-01-01 (60 days)")
        self.assertIn("expires in 60 days", alert)

    def test_does_not_warn_at_sixty_one_days(self):
        agents = [self.agent("2027-01-01T00:00:00Z")]
        _, alert = usage.license_expiry_summary(agents, dt.date(2026, 11, 1))
        self.assertIsNone(alert)

    def test_reports_an_expired_license(self):
        agents = [self.agent("2027-01-01T00:00:00Z")]
        line, alert = usage.license_expiry_summary(agents, dt.date(2027, 1, 3))
        self.assertEqual(line, "License expired 2027-01-01 (2 days ago)")
        self.assertIn("renew immediately", alert)

    def test_returns_nothing_without_an_expiry(self):
        self.assertEqual(usage.license_expiry_summary([]), (None, None))

    @staticmethod
    def agent(expiry, **overrides):
        agent = {
            "agentEdition": "AGENT_EDITION_PROTECTION",
            "activated": True,
            "licenseStatus": "LICENSE_STATUS_VALID",
            "licenseType": "LICENSE_TYPE_FULL",
            "licenseExpiredAt": expiry,
        }
        agent.update(overrides)
        return agent


if __name__ == "__main__":
    unittest.main()
