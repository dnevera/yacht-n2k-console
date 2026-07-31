"""test_device_contract.py — Unit tests for N2KDevice Registration Contract."""

import unittest
from ydnu02_tcp_gateway.device_contract import (
    N2KDeviceInfo,
    N2KDeviceEncoder,
    N2KDeviceRegistry,
)


class TestDeviceContract(unittest.TestCase):
    """Test suite for N2KDeviceInfo, N2KDeviceEncoder, and N2KDeviceRegistry."""

    def test_device_info_is_complete(self):
        info_incomplete = N2KDeviceInfo(sa=64, model_id="YDNU-02", model_serial="")
        self.assertFalse(info_incomplete.is_complete)

        info_complete = N2KDeviceInfo(sa=64, model_id="YDNU-02", model_serial="00402047")
        self.assertTrue(info_complete.is_complete)

    def test_encoder_generates_frames_with_correct_source(self):
        info = N2KDeviceInfo(
            sa=64,
            unique_id=402047,
            mfg_code=717,
            model_id="YDNU-02",
            software_version="1.75 07/08/2025",
            model_serial="00402047",
        )
        lines = N2KDeviceEncoder.encode_announcement(info)
        self.assertGreater(len(lines), 1)

        # Check ISO Claim line starts with 18EEFF40 (PGN 60928, Broadcast 0xFF, SA=64 0x40)
        claim_line = lines[0].decode("ascii")
        self.assertTrue(claim_line.startswith("18EEFF40"))

        # Check Product Info line starts with 19F01440 (PGN 126996, SA=64 0x40)
        prod_line = lines[1].decode("ascii")
        self.assertTrue(prod_line.startswith("19F01440"))

    def test_registry_updates_from_encoded_frames(self):
        info = N2KDeviceInfo(
            sa=64,
            unique_id=402047,
            mfg_code=717,
            model_id="YDNU-02",
            software_version="1.75 07/08/2025",
            model_serial="00402047",
        )
        lines = N2KDeviceEncoder.encode_announcement(info)

        registry = N2KDeviceRegistry()
        for line in lines:
            registry.update_from_frame(line)

        dev = registry.get_device(64)
        self.assertIsNotNone(dev)
        self.assertEqual(dev.sa, 64)
        self.assertEqual(dev.unique_id, 402047)
        self.assertEqual(dev.model_id, "YDNU-02")
        self.assertEqual(dev.model_serial, "00402047")


if __name__ == "__main__":
    unittest.main()
