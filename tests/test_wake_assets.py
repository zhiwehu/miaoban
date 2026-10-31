import unittest
from configure_wake_assets import pack,unpack

class AssetsTests(unittest.TestCase):
    def test_preserves_binary_data_and_dimensions(self):
        assets={'test.eaf':(bytes(range(256)),360,360),'index.json':(b'{}',0,0)}
        self.assertEqual(unpack(pack(assets)),assets)
    def test_rejects_corrupt_partition(self):
        blob=bytearray(pack({'test.eaf':(b'cat',1,2)}));blob[-1]^=1
        with self.assertRaises(ValueError):unpack(blob)
