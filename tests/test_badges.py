import io
import os
import sys
import tempfile
import unittest
from base64 import b64encode
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
	sys.path.insert(0, str(SCRIPTS))

import build_logos  # noqa: E402
from download_logos import LogoEntry, is_valid_svg, load_config  # noqa: E402

try:
	import cairosvg
	from PIL import Image
except ImportError:  # pragma: no cover - handled by skip decorator
	cairosvg = None
	Image = None

CONFIG = ROOT / 'config' / 'logos.yaml'
LOGO_DIR = ROOT / 'assets' / 'logos'
VISUAL_SCALE = 4
MIN_LOGO_AXIS_FILL = 0.30

VALID_SVG = (
	b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
	b'<path d="M0 0h1v1H0z"/></svg>'
)


def _svg_tag(name: str) -> str:
	return f'{{http://www.w3.org/2000/svg}}{name}'


def _build_svg(entry: LogoEntry, logo: bytes = VALID_SVG) -> str:
	encoded = b64encode(logo).decode('ascii')
	return build_logos.build_badge_svg(
		entry.name,
		encoded,
		entry.badge_logo_mode,
		entry.badge_tone,
	)


def _entry(
	*,
	logo_mode: str = 'icon',
	tone: str = 'original',
	source_type: str = 'official',
) -> LogoEntry:
	return LogoEntry(
		key='demo',
		name='Demo',
		file='demo.svg',
		source=None if source_type == 'manual' else 'https://example.invalid/demo.svg',
		source_type=source_type,
		badge_enabled=True,
		badge_logo_mode=logo_mode,
		badge_tone=tone,
	)


class BadgeStructureTests(unittest.TestCase):
	def test_icon_badge_is_transparent_and_contains_only_logo(self) -> None:
		root = ET.fromstring(_build_svg(_entry()))
		width = int(root.attrib['width'])
		height = int(root.attrib['height'])
		image = root.find(_svg_tag('image'))

		self.assertEqual(width, build_logos.badge_width('icon'))
		self.assertEqual(height, build_logos.HEIGHT)
		self.assertEqual(root.attrib['viewBox'], f'0 0 {width} {height}')
		self.assertIsNone(root.find(_svg_tag('rect')))
		self.assertIsNone(root.find(_svg_tag('text')))
		self.assertIsNotNone(image)

	def test_logo_is_centered_with_symmetric_padding(self) -> None:
		for logo_mode in ('icon', 'wordmark'):
			with self.subTest(logo_mode=logo_mode):
				root = ET.fromstring(_build_svg(_entry(logo_mode=logo_mode)))
				image = root.find(_svg_tag('image'))
				assert image is not None

				width = float(root.attrib['width'])
				height = float(root.attrib['height'])
				x = float(image.attrib['x'])
				y = float(image.attrib['y'])
				image_width = float(image.attrib['width'])
				image_height = float(image.attrib['height'])

				self.assertAlmostEqual(x, width - x - image_width)
				self.assertAlmostEqual(y, height - y - image_height)
				self.assertEqual(
					image.attrib.get('preserveAspectRatio'),
					'xMidYMid meet',
				)

	def test_wordmark_badge_keeps_a_wide_logo_slot(self) -> None:
		root = ET.fromstring(_build_svg(_entry(logo_mode='wordmark')))
		image = root.find(_svg_tag('image'))
		assert image is not None

		self.assertEqual(float(image.attrib['width']), build_logos.WORDMARK_WIDTH)
		self.assertEqual(int(root.attrib['width']), build_logos.badge_width('wordmark'))

	def test_neutral_tone_is_opt_in(self) -> None:
		original = _build_svg(_entry())
		neutral = _build_svg(_entry(tone='neutral'))

		self.assertNotIn('filter:', original)
		self.assertIn('grayscale(1)', neutral)
		self.assertIn('invert(.55)', neutral)

	def test_config_has_no_legacy_badge_background_or_text_fields(self) -> None:
		data = yaml.safe_load(CONFIG.read_text(encoding='utf-8'))
		failures: list[str] = []

		for key, raw in data['logos'].items():
			badge = raw.get('badge') or {}
			legacy = {'label', 'background', 'text_color'} & badge.keys()
			if legacy:
				failures.append(f'{key}: {sorted(legacy)}')

		self.assertFalse(failures, '\n'.join(failures))

	def test_source_logos_are_scalable_svg_documents(self) -> None:
		failures: list[str] = []

		for entry in load_config(CONFIG):
			logo = LOGO_DIR / entry.file
			if not is_valid_svg(logo):
				continue

			try:
				root = ET.parse(logo).getroot()
			except ET.ParseError as exc:
				failures.append(f'{entry.key}: invalid XML ({exc})')
				continue

			if not root.tag.endswith('svg'):
				failures.append(f'{entry.key}: root element is not <svg>')
				continue

			has_viewbox = bool(root.attrib.get('viewBox'))
			has_dimensions = bool(
				root.attrib.get('width') and root.attrib.get('height')
			)
			if not (has_viewbox or has_dimensions):
				failures.append(
					f'{entry.key}: SVG has neither viewBox nor width/height'
				)

		self.assertFalse(failures, '\n'.join(failures))


class ManualLogoTests(unittest.TestCase):
	def test_missing_manual_logo_does_not_fail_build(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			logo_dir = root / 'logos'
			badge_dir = root / 'badges'
			logo_dir.mkdir()
			badge_dir.mkdir()

			outcome = build_logos.process(
				[_entry(source_type='manual')],
				logo_dir,
				badge_dir,
				config_mtime=0.0,
				force=False,
			)

			self.assertEqual(outcome.manual, 1)
			self.assertEqual(outcome.failed, 0)
			self.assertEqual(outcome.built, 0)


class IncrementalBuildTests(unittest.TestCase):
	def test_config_change_forces_rebuild(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			logo = root / 'logo.svg'
			badge = root / 'badge.svg'
			logo.write_text('<svg/>', encoding='utf-8')
			badge.write_text('<svg/>', encoding='utf-8')

			os.utime(logo, (100, 100))
			os.utime(badge, (200, 200))

			self.assertTrue(build_logos.needs_rebuild(badge, logo, 300))

	def test_unchanged_inputs_do_not_force_rebuild(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			logo = root / 'logo.svg'
			badge = root / 'badge.svg'
			logo.write_text('<svg/>', encoding='utf-8')
			badge.write_text('<svg/>', encoding='utf-8')

			os.utime(logo, (100, 100))
			os.utime(badge, (300, 300))

			self.assertFalse(build_logos.needs_rebuild(badge, logo, 200))


@unittest.skipUnless(
	cairosvg is not None and Image is not None,
	'visual tests require CairoSVG and Pillow',
)
class BadgeVisualSafetyTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls) -> None:
		cls.entries = [
			entry
			for entry in load_config(CONFIG)
			if entry.badge_enabled and is_valid_svg(LOGO_DIR / entry.file)
		]

	def test_icon_sources_fill_square_slot(self) -> None:
		"""Reject icon-mode sources that become unreadably thin in a square slot."""
		failures: list[str] = []

		for entry in self.entries:
			if entry.badge_logo_mode != 'icon':
				continue

			logo = LOGO_DIR / entry.file
			png = cairosvg.svg2png(
				url=str(logo),
				output_width=256,
				output_height=256,
			)
			image = Image.open(io.BytesIO(png)).convert('RGBA')
			bbox = image.getchannel('A').getbbox()
			if bbox is None:
				failures.append(f'{entry.key}: logo renders fully transparent')
				continue

			width_fill = (bbox[2] - bbox[0]) / image.width
			height_fill = (bbox[3] - bbox[1]) / image.height
			axis_fill = min(width_fill, height_fill)

			if axis_fill < MIN_LOGO_AXIS_FILL:
				failures.append(
					f'{entry.key}: logo fills only {axis_fill:.1%} of its '
					'smallest axis; use an icon/symbol SVG instead of a wide wordmark'
				)

		self.assertFalse(failures, '\n'.join(failures))

	def test_wordmarks_are_legible_in_wide_slot(self) -> None:
		failures: list[str] = []

		for entry in self.entries:
			if entry.badge_logo_mode != 'wordmark':
				continue

			logo = LOGO_DIR / entry.file
			png = cairosvg.svg2png(
				url=str(logo),
				output_width=build_logos.WORDMARK_WIDTH * VISUAL_SCALE,
				output_height=build_logos.ICON_SIZE * VISUAL_SCALE,
			)
			image = Image.open(io.BytesIO(png)).convert('RGBA')
			bbox = image.getchannel('A').getbbox()
			if bbox is None:
				failures.append(f'{entry.key}: logo renders fully transparent')
				continue

			height_fill = (bbox[3] - bbox[1]) / image.height
			if height_fill < 0.45:
				failures.append(
					f'{entry.key}: wordmark fills only {height_fill:.1%} of slot height'
				)

		self.assertFalse(failures, '\n'.join(failures))


if __name__ == '__main__':
	unittest.main(verbosity=2)
