"""Acquire all SVG logo sources used by the local badge pipeline."""

import argparse
import contextlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
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

AUTO_SIMPLE_ICON_SLUGS = {
	'conventional-commits': 'conventionalcommits',
	'credly': 'credly',
	'dvc': 'dvc',
	'just': 'just',
	'langchain': 'langchain',
	'langgraph': 'langgraph',
	'mlflow': 'mlflow',
	'opencode': 'opencode',
	'platzi': 'platzi',
	'uml': 'uml',
	'warp': 'warp',
}

AUTO_LOOKUP_KEYS = frozenset(AUTO_SIMPLE_ICON_SLUGS) | {'delta-lake'}

ICONIFY_PREFIXES = (
	'logos',
	'simple-icons',
	'devicon',
	'skill-icons',
	'vscode-icons',
)

DELTA_PROFILE_URL = 'https://delta.io/profiles/delta-lake/'


@dataclass(frozen=True)
class LogoEntry:
	"""Describe one logo and its badge configuration."""

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
	"""Load and validate logo entries from a YAML configuration file."""
	data = yaml.safe_load(path.read_text(encoding='utf-8'))
	logos = (data or {}).get('logos') or {}

	if not isinstance(logos, dict):
		raise SystemExit(f"{path}: 'logos' must be a mapping")

	return [parse_entry(key, raw) for key, raw in logos.items()]


def parse_entry(key: str, raw: dict) -> LogoEntry:
	"""Validate one raw logo configuration entry."""
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
		allowed = sorted(SOURCE_TYPES)
		raise SystemExit(
			f'logos.{key}: source_type must be one of {allowed}, got {source_type!r}'
		)

	source = raw.get('source')
	if source_type == 'manual':
		source = None
	else:
		if not source or not str(source).startswith(('http://', 'https://')):
			raise SystemExit(
				f"logos.{key}: 'source' must be an http(s) URL for "
				f'source_type={source_type}'
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
			f'logos.{key}.badge.logo_mode: expected one of '
			f'{sorted(LOGO_MODES)}, got {logo_mode!r}'
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
	"""Return whether a path contains a non-HTML SVG document."""
	try:
		if not path.is_file() or path.stat().st_size == 0:
			return False

		head = (
			path.read_bytes()[:SVG_HEAD_BYTES].decode('utf-8', errors='ignore').lower()
		)
	except OSError:
		return False

	return '<svg' in head and '<html' not in head and '<!doctype html' not in head


def fetch_bytes(url: str) -> bytes:
	"""Download raw bytes from a URL using the configured timeout."""
	request = urllib.request.Request(
		url,
		headers={
			'User-Agent': USER_AGENT,
			'Accept': 'image/svg+xml,text/html,application/json,*/*',
		},
	)

	with urllib.request.urlopen(
		request,
		timeout=HTTP_TIMEOUT,
	) as response:
		return response.read()


def looks_like_svg(data: bytes) -> bool:
	"""Return whether downloaded bytes appear to contain SVG markup."""
	head = data[:SVG_HEAD_BYTES].decode('utf-8', errors='ignore').lower()

	return '<svg' in head and '<html' not in head and '<!doctype html' not in head


def atomic_write(path: Path, data: bytes) -> None:
	"""Replace a logo file atomically with downloaded bytes."""
	path.parent.mkdir(parents=True, exist_ok=True)

	tmp = path.with_suffix(path.suffix + '.tmp')
	tmp.write_bytes(data)
	os.replace(tmp, path)


def status_line(verb: str, name: str, note: str) -> str:
	"""Format one status row for command-line output."""
	return f'{verb:<9}{name:<24}{note}'


def _normalize(value: str) -> str:
	return re.sub(r'[^a-z0-9]', '', value.lower())


def _simple_icons_url(slug: str) -> str:
	return f'https://cdn.simpleicons.org/{slug}'


def _fetch_simple_icon(key: str) -> tuple[bytes, str] | None:
	slug = AUTO_SIMPLE_ICON_SLUGS.get(key)
	if slug is None:
		return None

	url = _simple_icons_url(slug)

	try:
		data = fetch_bytes(url)
	except (urllib.error.URLError, TimeoutError, OSError):
		return None

	if not looks_like_svg(data):
		return None

	return data, url


def _fetch_delta_lake() -> tuple[bytes, str] | None:
	try:
		page = fetch_bytes(DELTA_PROFILE_URL).decode(
			'utf-8',
			errors='ignore',
		)
	except (urllib.error.URLError, TimeoutError, OSError):
		return None

	page = html.unescape(page)
	patterns = (
		r'(?:src|href)=["\']([^"\']*delta-lake-logo[^"\']*\.svg[^"\']*)',
		r'(["\'][^"\']*delta-lake-logo[^"\']*\.svg[^"\']*["\'])',
	)

	for pattern in patterns:
		match = re.search(pattern, page, flags=re.IGNORECASE)
		if match is None:
			continue

		value = match.group(1).strip('"\'')
		url = urllib.parse.urljoin(DELTA_PROFILE_URL, value)

		try:
			data = fetch_bytes(url)
		except (urllib.error.URLError, TimeoutError, OSError):
			continue

		if looks_like_svg(data):
			return data, url

	return None


def _iconify_search(query: str) -> list[str]:
	params = urllib.parse.urlencode(
		{
			'query': query,
			'prefixes': ','.join(ICONIFY_PREFIXES),
			'limit': 64,
		}
	)
	url = f'https://api.iconify.design/search?{params}'

	try:
		payload = json.loads(fetch_bytes(url))
	except (
		urllib.error.URLError,
		TimeoutError,
		OSError,
		json.JSONDecodeError,
	):
		return []

	return list(payload.get('icons') or [])


def _candidate_score(candidate: str, key: str, name: str) -> int:
	try:
		prefix, icon_name = candidate.split(':', 1)
	except ValueError:
		return -1

	candidate_name = _normalize(icon_name)
	key_name = _normalize(key)
	display_name = _normalize(name)
	score = 0

	if candidate_name == key_name:
		score += 100
	if candidate_name == display_name:
		score += 100
	if key_name and key_name in candidate_name:
		score += 20
	if display_name and display_name in candidate_name:
		score += 20

	with contextlib.suppress(ValueError):
		score += len(ICONIFY_PREFIXES) - ICONIFY_PREFIXES.index(prefix)

	return score


def _select_iconify_candidate(key: str, name: str) -> str | None:
	queries = [name]

	if _normalize(key) != _normalize(name):
		queries.append(key.replace('-', ' '))

	candidates: set[str] = set()

	for query in queries:
		candidates.update(_iconify_search(query))

	if not candidates:
		return None

	ranked = sorted(
		(
			(_candidate_score(candidate, key, name), candidate)
			for candidate in candidates
		),
		reverse=True,
	)

	best_score, best = ranked[0]

	if best_score < 100:
		return None

	if len(ranked) > 1 and ranked[1][0] == best_score:
		return None

	return best


def _fetch_iconify(key: str, name: str) -> tuple[bytes, str] | None:
	candidate = _select_iconify_candidate(key, name)

	if candidate is None:
		return None

	prefix, icon_name = candidate.split(':', 1)
	url = (
		'https://api.iconify.design/'
		f'{urllib.parse.quote(prefix)}/'
		f'{urllib.parse.quote(icon_name)}.svg'
	)

	try:
		data = fetch_bytes(url)
	except (urllib.error.URLError, TimeoutError, OSError):
		return None

	if not looks_like_svg(data):
		return None

	return data, url


def _fetch_manual_fallback(entry: LogoEntry) -> tuple[bytes, str] | None:
	if entry.key not in AUTO_LOOKUP_KEYS:
		return None

	if entry.key == 'delta-lake':
		result = _fetch_delta_lake()
		if result is not None:
			return result

	result = _fetch_simple_icon(entry.key)
	if result is not None:
		return result

	return _fetch_iconify(entry.key, entry.name)


def _download_entry(entry: LogoEntry) -> tuple[bytes, str] | None:
	if entry.source_type == 'manual':
		return _fetch_manual_fallback(entry)

	if entry.source is None:
		return None

	data = fetch_bytes(entry.source)

	if not looks_like_svg(data):
		raise RuntimeError('response is not a valid SVG')

	return data, entry.source


def process(
	entries: Iterable[LogoEntry],
	logo_dir: Path,
	force: bool,
) -> dict[str, int]:
	"""Acquire logo assets and return operation counters."""
	counts = {
		'downloaded': 0,
		'skipped': 0,
		'manual': 0,
		'failed': 0,
	}

	for entry in entries:
		dest = logo_dir / entry.file
		local_valid = is_valid_svg(dest)
		auto_manual = entry.key in AUTO_LOOKUP_KEYS

		if local_valid and not force:
			print(status_line('SKIP', entry.name, 'valid local artifact'))
			counts['skipped'] += 1
			continue

		if entry.source_type == 'manual' and not auto_manual:
			if local_valid:
				print(status_line('SKIP', entry.name, 'manual local artifact'))
				counts['skipped'] += 1
			else:
				print(status_line('MANUAL', entry.name, f'{dest} is required'))
				counts['manual'] += 1
			continue

		try:
			result = _download_entry(entry)
		except (urllib.error.URLError, OSError, RuntimeError) as exc:
			print(status_line('FAILED', entry.name, str(exc)))
			counts['failed'] += 1
			continue

		if result is None:
			if local_valid:
				print(status_line('KEEP', entry.name, 'no replacement source found'))
				counts['skipped'] += 1
			else:
				print(
					status_line(
						'MANUAL',
						entry.name,
						f'{dest} is required; automatic source not found',
					)
				)
				counts['manual'] += 1
			continue

		data, source_url = result

		if not looks_like_svg(data):
			if local_valid:
				print(status_line('KEEP', entry.name, 'invalid refresh; kept local'))
				counts['skipped'] += 1
			else:
				print(status_line('FAILED', entry.name, 'download is not valid SVG'))
				counts['failed'] += 1
			continue

		try:
			atomic_write(dest, data)
		except OSError as exc:
			print(status_line('FAILED', entry.name, str(exc)))
			counts['failed'] += 1
			continue

		print(status_line('DOWNLOAD', entry.name, source_url))
		counts['downloaded'] += 1

	return counts


def print_summary(counts: dict[str, int]) -> None:
	"""Print downloader operation counters."""
	print()
	print(f'Downloaded:     {counts["downloaded"]}')
	print(f'Skipped:        {counts["skipped"]}')
	print(f'Manual missing: {counts["manual"]}')
	print(f'Failed:         {counts["failed"]}')


def main(argv: list[str]) -> int:
	"""Run the logo downloader CLI and return its exit status."""
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument('--config', type=Path, default=Path('config/logos.yaml'))
	parser.add_argument('--logo-dir', type=Path, default=Path('assets/logos'))
	parser.add_argument(
		'--force',
		action='store_true',
		help='refresh downloadable logos even when a valid local SVG exists',
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
