import unittest

from shipping import shipping_fee_cents


class ShippingFeeTests(unittest.TestCase):
    def test_vip_threshold(self):
        self.assertEqual(shipping_fee_cents(8_000, vip=True), 0)
        self.assertEqual(shipping_fee_cents(7_999, vip=True), 1_200)

    def test_regular_threshold(self):
        self.assertEqual(shipping_fee_cents(10_000), 0)
        self.assertEqual(shipping_fee_cents(9_999), 1_200)

    def test_negative_subtotal(self):
        with self.assertRaises(ValueError):
            shipping_fee_cents(-1, vip=True)

