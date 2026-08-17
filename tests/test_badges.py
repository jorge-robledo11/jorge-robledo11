import io
import os
import sys
import tempfile
import unittest
from base64 import b64encode
from pathlib import Path
from xml.etree import ElementTree as ET

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
EDGE_GUARD_PX = 4
TEXT_WIDTH_SAFETY_PX = 8.0
MIN_LOGO_AXIS_FILL = 0.30


def _svg_tag(name: str) -> str:
	return f'{{http://www.w3.org/2000/svg}}{name}'


def _hex_rgb(value: str) -> tuple[int, int, int]:
	value = value.removeprefix('#')
	return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _build_svg(entry) -> str:
	logo = LOGO_DIR / entry.file
	encoded = b64encode(logo.read_bytes()).decode('ascii')
	return build_logos.build_badge_svg(
		entry.badge_label,
		entry.badge_background,
		entry.badge_text_color,
		encoded,
		entry.badge_logo_mode,
	)


class BadgeStructureTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls) -> None:
		cls.entries = [
			entry
			for entry in load_config(CONFIG)
			if entry.badge_enabled and is_valid_svg(LOGO_DIR / entry.file)
		]

	def test_generated_badges_have_viewbox_and_fixed_height(self) -> None:
		failures: list[str] = []

		for entry in self.entries:
			root = ET.fromstring(_build_svg(entry))
			width = int(root.attrib['width'])
			height = int(root.attrib['height'])

			if height != build_logos.HEIGHT:
				failures.append(f'{entry.key}: height={height}')
			if root.attrib.get('viewBox') != f'0 0 {width} {height}':
				failures.append(f'{entry.key}: invalid/missing viewBox')

		self.assertFalse(failures, '\n'.join(failures))

	def test_text_slot_has_conservative_width_budget(self) -> None:
		failures: list[str] = []

		for entry in self.entries:
			if entry.badge_logo_mode != 'icon':
				continue

			root = ET.fromstring(_build_svg(entry))
			width = float(root.attrib['width'])
			text = root.find(_svg_tag('text'))
			if text is None:
				failures.append(f'{entry.key}: missing <text>')
				continue

			text_x = float(text.attrib['x'])
			available = width - text_x - build_logos.RIGHT_PAD
			required = len(entry.badge_label.upper()) * TEXT_WIDTH_SAFETY_PX
			if available < required:
				failures.append(
					f'{entry.key}: text slot {available:.1f}px < safe budget {required:.1f}px'
				)

		self.assertFalse(failures, '\n'.join(failures))

	def test_logo_image_box_is_inside_badge_and_preserves_aspect_ratio(self) -> None:
		failures: list[str] = []

		for entry in self.entries:
			root = ET.fromstring(_build_svg(entry))
			width = float(root.attrib['width'])
			height = float(root.attrib['height'])
			image = root.find(_svg_tag('image'))

			if image is None:
				failures.append(f'{entry.key}: missing <image>')
				continue

			x = float(image.attrib['x'])
			y = float(image.attrib['y'])
			image_width = float(image.attrib['width'])
			image_height = float(image.attrib['height'])

			if x < 0 or y < 0 or x + image_width > width or y + image_height > height:
				failures.append(f'{entry.key}: logo image box escapes badge viewport')
			if image.attrib.get('preserveAspectRatio') != 'xMidYMid meet':
				failures.append(
					f'{entry.key}: preserveAspectRatio must be xMidYMid meet'
				)

		self.assertFalse(failures, '\n'.join(failures))

	def test_wordmark_badges_use_wide_logo_slot_without_duplicate_text(self) -> None:
		failures: list[str] = []

		for entry in self.entries:
			if entry.badge_logo_mode != 'wordmark':
				continue

			root = ET.fromstring(_build_svg(entry))
			image = root.find(_svg_tag('image'))
			text = root.find(_svg_tag('text'))

			if image is None:
				failures.append(f'{entry.key}: missing <image>')
				continue
			if float(image.attrib['width']) != build_logos.WORDMARK_WIDTH:
				failures.append(f'{entry.key}: wordmark slot width is incorrect')
			if text is not None:
				failures.append(
					f'{entry.key}: wordmark badge must not duplicate label text'
				)

		self.assertFalse(failures, '\n'.join(failures))

	def test_source_logos_are_scalable_svg_documents(self) -> None:
		failures: list[str] = []

		for entry in self.entries:
			logo = LOGO_DIR / entry.file
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

			entry = LogoEntry(
				key='manual-demo',
				name='Manual Demo',
				file='manual-demo.svg',
				source=None,
				source_type='manual',
				badge_label='Manual Demo',
				badge_background='#100000',
				badge_text_color='#FFFFFF',
				badge_enabled=True,
			)

			outcome = build_logos.process(
				[entry],
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
					f'{entry.key}: logo fills only {axis_fill:.1%} of its smallest axis; '
					'use an icon/symbol SVG instead of a wide wordmark'
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
				output_height=build_logos.LOGO_SIZE * VISUAL_SCALE,
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

	def test_content_does_not_touch_badge_edges(self) -> None:
		"""Catch clipped text/logos by requiring a clean background guard band."""
		failures: list[str] = []

		for entry in self.entries:
			svg = _build_svg(entry)
			png = cairosvg.svg2png(
				bytestring=svg.encode('utf-8'),
				scale=VISUAL_SCALE,
			)

			image = Image.open(io.BytesIO(png)).convert('RGBA')
			pixels = image.load()
			width, height = image.size
			background = _hex_rgb(entry.badge_background)
			guard = EDGE_GUARD_PX * VISUAL_SCALE

			def is_background(x: int, y: int) -> bool:
				r, g, b, a = pixels[x, y]
				return a == 255 and (r, g, b) == background

			touched_edges: list[str] = []
			if any(
				not is_background(x, y) for x in range(guard) for y in range(height)
			):
				touched_edges.append('left')
			if any(
				not is_background(x, y)
				for x in range(width - guard, width)
				for y in range(height)
			):
				touched_edges.append('right')
			if any(not is_background(x, y) for y in range(guard) for x in range(width)):
				touched_edges.append('top')
			if any(
				not is_background(x, y)
				for y in range(height - guard, height)
				for x in range(width)
			):
				touched_edges.append('bottom')

			if touched_edges:
				failures.append(
					f'{entry.key}: rendered content reaches {", ".join(touched_edges)} edge(s)'
				)

		self.assertFalse(failures, '\n'.join(failures))


if __name__ == '__main__':
	unittest.main(verbosity=2)
