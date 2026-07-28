import unittest
from mopeka_parsers import parse_advertisement, compute_fill_level

class TestMopekaParsers(unittest.TestCase):
    
    def test_parse_advertisement_top_down(self):
        # Mopeka Pro 200 payload
        # hw_id=0x04, batt=0x50, temp=0x3C, level=0x4321, accel=0x00,0x00, mac=0xAA,0xBB,0xCC
        hw_byte = 0x04 # ID: 0x04, extended: 0
        batt_byte = int(3.0 * 32.0) & 0x7F # 96 = 0x60 -> 3.0V = 100%
        temp_byte = 20 + 40 # 60 = 0x3C -> 20C
        # ToF = 1000us (0x03E8), Quality = 2 (High)
        # Word = (2 << 14) | 1000 = 0x8000 | 0x03E8 = 0x83E8
        level_bytes = b'\xE8\x83' # Little Endian
        
        payload = bytes([hw_byte, batt_byte, temp_byte]) + level_bytes + b'\x00\x00\xAA\xBB\xCC'
        
        res = parse_advertisement(payload)
        
        self.assertEqual(res["hardware_id"], 0x04)
        self.assertEqual(res["hardware_name"], "Pro 200")
        self.assertFalse(res["extended_range"])
        self.assertEqual(res["voltage_v"], 3.0)
        self.assertEqual(res["battery_pct"], 100.0)
        self.assertEqual(res["temp_c"], 20)
        self.assertFalse(res["sync_pressed"])
        self.assertEqual(res["tof_us"], 1000)
        self.assertEqual(res["quality_stars"], 2)
        self.assertEqual(res["quality_label"], "Medium")
        
        # Distance: v_air = 331.3 + 0.606*20 = 343.42 m/s
        # dist = 1000 * 343.42 / 2000.0 = 171.71 mm
        self.assertAlmostEqual(res["distance_mm"], 171.7, places=1)
        
    def test_parse_advertisement_bottom_up(self):
        # Mopeka Pro Check payload
        hw_byte = 0x03 # ID: 0x03, extended: 0
        batt_byte = int(2.6 * 32.0) & 0x7F # 83 = 0x53 -> 2.59375V = 49.2%
        temp_byte = 10 + 40 # 50 = 0x32 -> 10C
        # ToF = 500us (0x01F4), Quality = 3 (High)
        # Word = (3 << 14) | 500 = 0xC000 | 0x01F4 = 0xC1F4
        level_bytes = b'\xF4\xC1' 
        
        payload = bytes([hw_byte, batt_byte, temp_byte]) + level_bytes + b'\x00\x00\xAA\xBB\xCC'
        
        res = parse_advertisement(payload)
        
        self.assertEqual(res["hardware_id"], 0x03)
        self.assertEqual(res["hardware_name"], "Pro Check")
        self.assertEqual(res["temp_c"], 10)
        self.assertEqual(res["quality_stars"], 3)
        self.assertEqual(res["quality_label"], "High")
        
        # Distance: 500 * (0.575 - 0.0017*10) = 500 * 0.558 = 279.0 mm
        self.assertAlmostEqual(res["distance_mm"], 279.0, places=1)

    def test_extended_range(self):
        hw_byte = 0x84 # ID: 0x04, extended: 1
        batt_byte = 0x60
        temp_byte = 0x3C
        # ToF = 100us, Quality = 1 -> Word = 0x4064 -> \x64\x40
        level_bytes = b'\x64\x40' 
        payload = bytes([hw_byte, batt_byte, temp_byte]) + level_bytes + b'\x00\x00\xAA\xBB\xCC'
        
        res = parse_advertisement(payload)
        self.assertTrue(res["extended_range"])
        self.assertEqual(res["tof_us"], 400) # 100 * 4

    def test_sync_pressed(self):
        hw_byte = 0x04
        batt_byte = 0x60
        temp_byte = 0x3C | 0x80 # Set sync bit
        payload = bytes([hw_byte, batt_byte, temp_byte, 0x00, 0x00, 0x00, 0x00, 0xAA, 0xBB, 0xCC])
        res = parse_advertisement(payload)
        self.assertTrue(res["sync_pressed"])

    def test_short_payload(self):
        res = parse_advertisement(b'\x01\x02')
        self.assertIn("error", res)

    def test_compute_fill_level(self):
        self.assertEqual(compute_fill_level(100.0, 20.0), 80.0)
        self.assertEqual(compute_fill_level(100.0, 0.0), 100.0)
        self.assertEqual(compute_fill_level(100.0, 150.0), 0.0) # clamped
        self.assertEqual(compute_fill_level(0.0, 50.0), 0.0) # zero depth

if __name__ == "__main__":
    unittest.main()
