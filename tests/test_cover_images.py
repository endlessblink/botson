import unittest

from fastapi import HTTPException

import dashboard.app as dashboard_app


class CoverImageValidationTests(unittest.TestCase):
    def test_rejects_truncated_jpeg(self):
        with self.assertRaises(HTTPException) as ctx:
            dashboard_app._validated_cover_ext(b"\xff\xd8partial jpeg", "image/jpeg")

        self.assertEqual(ctx.exception.status_code, 422)

    def test_accepts_complete_jpeg(self):
        ext = dashboard_app._validated_cover_ext(b"\xff\xd8jpeg data\xff\xd9", "image/jpeg")

        self.assertEqual(ext, "jpg")

    def test_rejects_unsupported_image_content_type(self):
        with self.assertRaises(HTTPException) as ctx:
            dashboard_app._validated_cover_ext(b"<svg></svg>", "image/svg+xml")

        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
