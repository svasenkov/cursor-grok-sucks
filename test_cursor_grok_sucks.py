"""Unit tests for cursor_grok_sucks (no Cursor install required)."""

from __future__ import annotations

import unittest

import cursor_grok_sucks as cgs


class GrokMatchTests(unittest.TestCase):
    def test_is_grok(self) -> None:
        self.assertTrue(cgs.is_grok("grok-4.5"))
        self.assertTrue(cgs.is_grok("GROK-code-fast-1"))
        self.assertFalse(cgs.is_grok("default"))
        self.assertFalse(cgs.is_grok(""))


class ScrubAiSettingsTests(unittest.TestCase):
    def test_moves_grok_from_enabled_to_disabled(self) -> None:
        ai = {
            "modelOverrideEnabled": ["default", "grok-4.5"],
            "modelOverrideDisabled": [],
            "modelConfig": {"composer": {"modelName": "default", "selectedModels": []}},
        }
        actions = cgs.scrub_ai_settings(ai, "default", {"grok-4.5"})
        self.assertIn("grok-4.5", ai["modelOverrideDisabled"])
        self.assertNotIn("grok-4.5", ai["modelOverrideEnabled"])
        self.assertTrue(any("override disable" in a for a in actions))

    def test_replaces_active_grok_surface(self) -> None:
        ai = {
            "modelOverrideEnabled": [],
            "modelOverrideDisabled": ["grok-4.5"],
            "modelConfig": {
                # "composer" = Cursor's agent-chat surface key, not the Composer model
                "composer": {
                    "modelName": "grok-4.5",
                    "selectedModels": [{"modelId": "grok-4.5", "parameters": []}],
                }
            },
        }
        actions = cgs.scrub_ai_settings(ai, "default", {"grok-4.5"})
        chat = ai["modelConfig"]["composer"]
        self.assertEqual(chat["modelName"], "default")
        self.assertEqual(chat["selectedModels"][0]["modelId"], "default")
        self.assertTrue(any(a.startswith("composer:") for a in actions))


if __name__ == "__main__":
    unittest.main()
