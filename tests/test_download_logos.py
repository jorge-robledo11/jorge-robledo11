from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
	sys.path.insert(0, str(SCRIPTS))

import download_logos  # noqa: E402

VALID_SVG = (
	b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
	b'<path d="M0 0h1v1H0z"/></svg>'
)


def entry(*, source_type: str = 'official') -> download_logos.LogoEntry:
	return download_logos.LogoEntry(
		key='demo',
		name='Demo',
		file='demo.svg',
		source=None if source_type == 'manual' else 'https://example.invalid/demo.svg',
		source_type=source_type,
		badge_label='Demo',
		badge_background='#100000',
		badge_text_color='#FFFFFF',
		badge_enabled=True,
	)


class DownloadShortCircuitTests(unittest.TestCase):
	def test_valid_local_svg_skips_network_before_fetch(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			logo_dir = Path(tmp)
			(logo_dir / 'demo.svg').write_bytes(VALID_SVG)

			with patch.object(download_logos, 'fetch_bytes') as fetch:
				counts = download_logos.process([entry()], logo_dir, force=False)

			fetch.assert_not_called()
			self.assertEqual(counts['skipped'], 1)
			self.assertEqual(counts['downloaded'], 0)
			self.assertEqual(counts['failed'], 0)

	def test_manual_asset_never_touches_network_even_with_force(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			logo_dir = Path(tmp)
			(logo_dir / 'demo.svg').write_bytes(VALID_SVG)

			with patch.object(download_logos, 'fetch_bytes') as fetch:
				counts = download_logos.process(
					[entry(source_type='manual')],
					logo_dir,
					force=True,
				)

			fetch.assert_not_called()
			self.assertEqual(counts['skipped'], 1)

	def test_missing_manual_asset_is_reported_without_network(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			logo_dir = Path(tmp)

			with patch.object(download_logos, 'fetch_bytes') as fetch:
				counts = download_logos.process(
					[entry(source_type='manual')],
					logo_dir,
					force=False,
				)

			fetch.assert_not_called()
			self.assertEqual(counts['manual'], 1)

	def test_missing_remote_asset_downloads_and_validates_svg(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			logo_dir = Path(tmp)

			with patch.object(
				download_logos, 'fetch_bytes', return_value=VALID_SVG
			) as fetch:
				counts = download_logos.process([entry()], logo_dir, force=False)

			fetch.assert_called_once()
			self.assertEqual(counts['downloaded'], 1)
			self.assertTrue(download_logos.is_valid_svg(logo_dir / 'demo.svg'))

	def test_failed_force_refresh_does_not_destroy_valid_local_svg(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			logo_dir = Path(tmp)
			target = logo_dir / 'demo.svg'
			target.write_bytes(VALID_SVG)

			with patch.object(
				download_logos,
				'fetch_bytes',
				return_value=b'<html>not an svg</html>',
			):
				counts = download_logos.process([entry()], logo_dir, force=True)

			self.assertEqual(counts['failed'], 1)
			self.assertEqual(target.read_bytes(), VALID_SVG)
			self.assertTrue(download_logos.is_valid_svg(target))


if __name__ == '__main__':
	unittest.main(verbosity=2)
