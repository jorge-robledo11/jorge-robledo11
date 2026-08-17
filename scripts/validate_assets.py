"""Validate that configured logos and generated badges are README-ready."""

from base64 import b64encode
from collections import Counter
from pathlib import Path

import build_logos
from download_logos import is_valid_svg, load_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config' / 'logos.yaml'
LOGO_DIR = ROOT / 'assets' / 'logos'
BADGE_DIR = ROOT / 'assets' / 'badges'


def _print_group(title: str, items: list[str]) -> None:
	"""Print a titled group of validation issues."""
	if not items:
		return

	print()
	print(f'{title} ({len(items)}):')
	for item in items:
		print(f'  - {item}')


def main() -> int:
	"""Validate the complete configured logo-to-badge inventory."""
	entries = [entry for entry in load_config(CONFIG) if entry.badge_enabled]

	configured_files = [entry.file for entry in entries]
	counts = Counter(configured_files)

	duplicates = sorted(name for name, count in counts.items() if count > 1)

	missing_logos: list[str] = []
	missing_badges: list[str] = []
	stale_badges: list[str] = []

	for entry in entries:
		logo = LOGO_DIR / entry.file
		badge = BADGE_DIR / entry.file

		logo_valid = is_valid_svg(logo)
		badge_valid = is_valid_svg(badge)

		if not logo_valid:
			missing_logos.append(entry.key)

		if not badge_valid:
			missing_badges.append(entry.key)

		if not logo_valid or not badge_valid:
			continue

		logo_b64 = b64encode(logo.read_bytes()).decode('ascii')
		expected_badge = build_logos.build_badge_svg(
			entry.name,
			logo_b64,
			entry.badge_logo_mode,
			entry.badge_tone,
		)

		actual_badge = badge.read_text(encoding='utf-8')

		if actual_badge != expected_badge:
			stale_badges.append(entry.key)

	expected_files = set(configured_files)

	logo_files = (
		{path.name for path in LOGO_DIR.glob('*.svg')} if LOGO_DIR.is_dir() else set()
	)

	badge_files = (
		{path.name for path in BADGE_DIR.glob('*.svg')} if BADGE_DIR.is_dir() else set()
	)

	orphan_logos = sorted(logo_files - expected_files)
	orphan_badges = sorted(badge_files - expected_files)

	problems = (
		duplicates
		or missing_logos
		or missing_badges
		or stale_badges
		or orphan_logos
		or orphan_badges
	)

	print(f'Configured badges: {len(entries)}')
	print(f'Valid logos:       {len(entries) - len(missing_logos)}')
	print(f'Valid badges:      {len(entries) - len(missing_badges)}')

	_print_group('Duplicate configured files', duplicates)
	_print_group('Missing or invalid logos', missing_logos)
	_print_group('Missing or invalid badges', missing_badges)
	_print_group('Outdated badges', stale_badges)
	_print_group('Orphan logo files', orphan_logos)
	_print_group('Orphan badge files', orphan_badges)

	if problems:
		print()
		print('Assets are NOT ready for README publication.')
		return 1

	print()
	print('All configured logos and badges are README-ready.')
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
