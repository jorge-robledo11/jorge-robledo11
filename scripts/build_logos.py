"""
Build for-the-badge style SVGs locally from existing logo files.

Reads config/logos.yaml + assets/logos/*.svg and emits assets/badges/*.svg.
No network access. Multicolor logos are preserved via base64 embed.
"""

import argparse
import os
import sys
from base64 import b64encode
from dataclasses import dataclass
from html import escape
from pathlib import Path

from download_logos import LogoEntry, is_valid_svg, load_config

HEIGHT = 28
LOGO_SIZE = 16
WORDMARK_WIDTH = 96
LEFT_PAD = 8
LOGO_TEXT_GAP = 6
RIGHT_PAD = 12
CHAR_W = 8.5
FONT_FAMILY = 'Verdana, Geneva, DejaVu Sans, sans-serif'
FONT_SIZE = 11


def badge_width(label: str, logo_mode: str = 'icon') -> int:
	if logo_mode == 'wordmark':
		return LEFT_PAD + WORDMARK_WIDTH + RIGHT_PAD
	return round(LEFT_PAD + LOGO_SIZE + LOGO_TEXT_GAP + len(label) * CHAR_W + RIGHT_PAD)


def status_line(verb: str, name: str, note: str) -> str:
	return f'{verb:<9}{name:<24}{note}'


@dataclass
class BuildOutcome:
	built: int = 0
	skipped: int = 0
	manual: int = 0
	failed: int = 0


def needs_rebuild(badge: Path, logo: Path, config_mtime: float) -> bool:
	if not badge.is_file():
		return True
	try:
		badge_mtime = badge.stat().st_mtime
	except OSError:
		return True
	if logo.stat().st_mtime > badge_mtime:
		return True
	return config_mtime > badge_mtime


def build_badge_svg(
	label: str,
	background: str,
	text_color: str,
	logo_b64: str,
	logo_mode: str = 'icon',
) -> str:
	width = max(60, badge_width(label, logo_mode))
	safe_label = escape(label, quote=True)
	image_width = WORDMARK_WIDTH if logo_mode == 'wordmark' else LOGO_SIZE
	image = (
		f'<image href="data:image/svg+xml;base64,{logo_b64}" '
		f'x="{LEFT_PAD}" y="{(HEIGHT - LOGO_SIZE) // 2}" '
		f'width="{image_width}" height="{LOGO_SIZE}" '
		f'preserveAspectRatio="xMidYMid meet"/>'
	)

	text = ''
	if logo_mode == 'icon':
		safe_label_upper = escape(label.upper())
		text = (
			f'<text x="{LEFT_PAD + LOGO_SIZE + LOGO_TEXT_GAP}" '
			f'y="{int(HEIGHT / 2 + FONT_SIZE / 2 - 2)}" '
			f'fill="{text_color}" font-family="{FONT_FAMILY}" '
			f'font-size="{FONT_SIZE}" font-weight="700">{safe_label_upper}</text>'
		)

	return (
		f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{HEIGHT}" '
		f'viewBox="0 0 {width} {HEIGHT}" role="img" aria-label="{safe_label}">'
		f'<rect width="{width}" height="{HEIGHT}" fill="{background}"/>'
		f'{image}{text}</svg>\n'
	)


def atomic_write(path: Path, content: str) -> None:
	tmp = path.with_suffix(path.suffix + '.tmp')
	tmp.write_text(content, encoding='utf-8')
	os.replace(tmp, path)


def process(
	entries: list[LogoEntry],
	logo_dir: Path,
	badge_dir: Path,
	config_mtime: float,
	force: bool,
) -> BuildOutcome:
	outcome = BuildOutcome()

	for entry in entries:
		if not entry.badge_enabled:
			outcome.skipped += 1
			continue

		logo_path = logo_dir / entry.file
		if not is_valid_svg(logo_path):
			if entry.source_type == 'manual':
				print(status_line('MANUAL', entry.name, f'{logo_path} is required'))
				outcome.manual += 1
				continue

			print(
				status_line(
					'FAILED', entry.name, f'missing or invalid logo at {logo_path}'
				)
			)
			outcome.failed += 1
			continue

		badge_path = badge_dir / entry.file
		if not force and not needs_rebuild(badge_path, logo_path, config_mtime):
			print(status_line('SKIP', entry.name, 'up to date'))
			outcome.skipped += 1
			continue

		try:
			logo_b64 = b64encode(logo_path.read_bytes()).decode('ascii')
			svg = build_badge_svg(
				entry.badge_label,
				entry.badge_background,
				entry.badge_text_color,
				logo_b64,
				entry.badge_logo_mode,
			)
			atomic_write(badge_path, svg)
		except OSError as exc:
			print(status_line('FAILED', entry.name, str(exc)))
			outcome.failed += 1
			continue

		print(status_line('BUILD', entry.name, ''))
		outcome.built += 1

	return outcome


def main(argv: list[str]) -> int:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument('--config', type=Path, default=Path('config/logos.yaml'))
	parser.add_argument('--logo-dir', type=Path, default=Path('assets/logos'))
	parser.add_argument('--badge-dir', type=Path, default=Path('assets/badges'))
	parser.add_argument('--force', action='store_true', help='rebuild every badge')
	args = parser.parse_args(argv)

	if not args.config.is_file():
		print(f'Config not found: {args.config}', file=sys.stderr)
		return 2

	args.badge_dir.mkdir(parents=True, exist_ok=True)
	entries = load_config(args.config)
	config_mtime = args.config.stat().st_mtime
	outcome = process(
		entries,
		args.logo_dir,
		args.badge_dir,
		config_mtime,
		args.force,
	)

	print()
	print(f'Built:          {outcome.built}')
	print(f'Skipped:        {outcome.skipped}')
	print(f'Manual missing: {outcome.manual}')
	print(f'Failed:         {outcome.failed}')
	return 1 if outcome.failed > 0 else 0


if __name__ == '__main__':
	raise SystemExit(main(sys.argv[1:]))
