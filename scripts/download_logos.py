"""
Acquire original SVG logos from config/logos.yaml.

Invariants:
- A valid local SVG short-circuits ALL remote work for that entry.
- Manual entries (source_type: manual) never touch the network.
- One failed entry does not stop the rest.

Stdlib-only HTTP. PyYAML is the only third-party dependency.
"""

import argparse
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

USER_AGENT = 'github-profile-assets/1.0'
HTTP_TIMEOUT = 20
SVG_HEAD_BYTES = 2048
SOURCE_TYPES = frozenset({'official', 'github', 'iconify', 'devicon', 'manual'})
LOGO_MODES = frozenset({'icon', 'wordmark'})
HEX_COLOR_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')


@dataclass(frozen=True)
class LogoEntry:
	key: str
	name: str
	file: str
	source: str | None
	source_type: str
	badge_label: str
	badge_background: str
	badge_text_color: str
	badge_enabled: bool
	badge_logo_mode: str = 'icon'


def load_config(path: Path) -> list[LogoEntry]:
	data = yaml.safe_load(path.read_text(encoding='utf-8'))
	logos = (data or {}).get('logos') or {}
	if not isinstance(logos, dict):
		raise SystemExit(f"{path}: 'logos' must be a mapping")

	entries: list[LogoEntry] = []
	for key, raw in logos.items():
		entries.append(parse_entry(key, raw))
	return entries


def parse_entry(key: str, raw: dict) -> LogoEntry:
	if not isinstance(raw, dict):
		raise SystemExit(f'logos.{key}: entry must be a mapping')

	name = str(raw.get('name') or '').strip()
	file = str(raw.get('file') or '').strip()
	source_type = str(raw.get('source_type') or '').strip()

	if not name:
		raise SystemExit(f"logos.{key}: 'name' is required")
	if not file:
		raise SystemExit(f"logos.{key}: 'file' is required")
	if source_type not in SOURCE_TYPES:
		raise SystemExit(
			f'logos.{key}: source_type must be one of {sorted(SOURCE_TYPES)}, got {source_type!r}'
		)

	source = raw.get('source')
	if source_type == 'manual':
		source = None
	else:
		if not source or not str(source).startswith(('http://', 'https://')):
			raise SystemExit(
				f"logos.{key}: 'source' must be an http(s) URL for source_type={source_type}"
			)
		source = str(source)

	badge = raw.get('badge') or {}
	if not isinstance(badge, dict):
		raise SystemExit(f'logos.{key}.badge: must be a mapping')

	label = str(badge.get('label') or name).strip()
	background = str(badge.get('background') or '#100000').strip()
	text_color = str(badge.get('text_color') or '#FFFFFF').strip()
	enabled = bool(badge.get('enabled', True))
	logo_mode = str(badge.get('logo_mode') or 'icon').strip()

	if logo_mode not in LOGO_MODES:
		raise SystemExit(
			f'logos.{key}.badge.logo_mode: expected one of {sorted(LOGO_MODES)}, '
			f'got {logo_mode!r}'
		)

	if not HEX_COLOR_RE.fullmatch(background):
		raise SystemExit(
			f'logos.{key}.badge.background: expected #RRGGBB, got {background!r}'
		)
	if not HEX_COLOR_RE.fullmatch(text_color):
		raise SystemExit(
			f'logos.{key}.badge.text_color: expected #RRGGBB, got {text_color!r}'
		)

	return LogoEntry(
		key=key,
		name=name,
		file=file,
		source=source,
		source_type=source_type,
		badge_label=label,
		badge_background=background,
		badge_text_color=text_color,
		badge_enabled=enabled,
		badge_logo_mode=logo_mode,
	)


def is_valid_svg(path: Path) -> bool:
	try:
		if not path.is_file() or path.stat().st_size == 0:
			return False
		head = (
			path.read_bytes()[:SVG_HEAD_BYTES].decode('utf-8', errors='ignore').lower()
		)
	except OSError:
		return False
	if '<svg' not in head:
		return False
	return not ('<html' in head or '<!doctype html' in head)


def fetch_bytes(url: str) -> bytes:
	request = urllib.request.Request(
		url,
		headers={'User-Agent': USER_AGENT, 'Accept': '*/*'},
	)
	with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
		return response.read()


def looks_like_svg(data: bytes) -> bool:
	head = data[:SVG_HEAD_BYTES].decode('utf-8', errors='ignore').lower()
	return '<svg' in head and '<html' not in head and '<!doctype html' not in head


def atomic_write(path: Path, data: bytes) -> None:
	tmp = path.with_suffix(path.suffix + '.tmp')
	tmp.write_bytes(data)
	os.replace(tmp, path)


def status_line(verb: str, name: str, note: str) -> str:
	return f'{verb:<9}{name:<24}{note}'


def process(
	entries: Iterable[LogoEntry], logo_dir: Path, force: bool
) -> dict[str, int]:
	counts = {'downloaded': 0, 'skipped': 0, 'manual': 0, 'failed': 0}

	for entry in entries:
		dest = logo_dir / entry.file

		local_valid = is_valid_svg(dest)

		# Manual assets can never be refreshed from the network. A valid local
		# artifact therefore always wins, even when --force is requested.
		if local_valid and (not force or entry.source_type == 'manual'):
			print(status_line('SKIP', entry.name, 'valid local artifact'))
			counts['skipped'] += 1
			continue

		if entry.source_type == 'manual':
			print(
				status_line(
					'MANUAL',
					entry.name,
					f'{dest} is required',
				)
			)
			counts['manual'] += 1
			continue

		try:
			data = fetch_bytes(entry.source)
			if not looks_like_svg(data):
				raise RuntimeError('response is not a valid SVG')
			atomic_write(dest, data)
		except (urllib.error.URLError, OSError, RuntimeError) as exc:
			print(status_line('FAILED', entry.name, str(exc)))
			counts['failed'] += 1
			continue

		print(status_line('DOWNLOAD', entry.name, entry.source_type))
		counts['downloaded'] += 1

	return counts


def print_summary(counts: dict[str, int]) -> None:
	print()
	print(f'Downloaded:     {counts["downloaded"]}')
	print(f'Skipped:        {counts["skipped"]}')
	print(f'Manual missing: {counts["manual"]}')
	print(f'Failed:         {counts["failed"]}')


def main(argv: list[str]) -> int:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument('--config', type=Path, default=Path('config/logos.yaml'))
	parser.add_argument('--logo-dir', type=Path, default=Path('assets/logos'))
	parser.add_argument(
		'--force', action='store_true', help='re-download even if local SVG is valid'
	)
	args = parser.parse_args(argv)

	if not args.config.is_file():
		print(f'Config not found: {args.config}', file=sys.stderr)
		return 2

	args.logo_dir.mkdir(parents=True, exist_ok=True)
	entries = load_config(args.config)
	counts = process(entries, args.logo_dir, args.force)
	print_summary(counts)
	return 1 if counts['failed'] > 0 else 0


if __name__ == '__main__':
	raise SystemExit(main(sys.argv[1:]))
