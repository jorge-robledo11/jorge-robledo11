"""Download missing SVG logo sources used by the local badge pipeline."""

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from download_logos import is_valid_svg, load_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config' / 'logos.yaml'
LOGO_DIR = ROOT / 'assets' / 'logos'

USER_AGENT = 'github-profile-assets/1.0'
HTTP_TIMEOUT = 20

SIMPLE_ICON_SLUGS = {
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

ICONIFY_PREFIXES = (
	'logos',
	'simple-icons',
	'devicon',
	'skill-icons',
	'vscode-icons',
)

DELTA_PROFILE_URL = 'https://delta.io/profiles/delta-lake/'


def fetch_bytes(url: str) -> bytes:
	"""Download bytes from a URL."""
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
	"""Return whether downloaded bytes look like an SVG document."""
	head = data[:4096].decode('utf-8', errors='ignore').lower()

	return (
		'<svg' in head
		and '<html' not in head
		and '<!doctype html' not in head
	)


def atomic_write(path: Path, data: bytes) -> None:
	"""Write downloaded data atomically."""
	path.parent.mkdir(parents=True, exist_ok=True)

	tmp = path.with_suffix(path.suffix + '.tmp')
	tmp.write_bytes(data)
	os.replace(tmp, path)


def normalize(value: str) -> str:
	"""Normalize names for icon-search comparison."""
	return re.sub(r'[^a-z0-9]', '', value.lower())


def simple_icons_url(slug: str) -> str:
	"""Return the Simple Icons CDN URL for a brand slug."""
	return f'https://cdn.simpleicons.org/{slug}'


def try_simple_icons(key: str) -> tuple[bytes, str] | None:
	"""Try downloading a known brand from Simple Icons."""
	slug = SIMPLE_ICON_SLUGS.get(key)

	if slug is None:
		return None

	url = simple_icons_url(slug)

	try:
		data = fetch_bytes(url)
	except (urllib.error.URLError, TimeoutError, OSError):
		return None

	if not looks_like_svg(data):
		return None

	return data, url


def fetch_delta_lake() -> tuple[bytes, str] | None:
	"""Discover and download the SVG used by the official Delta Lake website."""
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


def iconify_search(query: str) -> list[str]:
	"""Search Iconify for brand-like icons."""
	params = urllib.parse.urlencode(
		{
			'query': query,
			'prefixes': ','.join(ICONIFY_PREFIXES),
			'limit': 64,
		}
	)

	url = f'https://api.iconify.design/search?{params}'

	try:
		data = json.loads(fetch_bytes(url))
	except (
		urllib.error.URLError,
		TimeoutError,
		OSError,
		json.JSONDecodeError,
	):
		return []

	return list(data.get('icons') or [])


def candidate_score(
	candidate: str,
	key: str,
	name: str,
) -> int:
	"""Score an Iconify result for automatic brand selection."""
	try:
		prefix, icon_name = candidate.split(':', 1)
	except ValueError:
		return -1

	candidate_name = normalize(icon_name)

	key_name = normalize(key)
	display_name = normalize(name)

	score = 0

	if candidate_name == key_name:
		score += 100

	if candidate_name == display_name:
		score += 100

	if key_name and key_name in candidate_name:
		score += 20

	if display_name and display_name in candidate_name:
		score += 20

	try:
		score += len(ICONIFY_PREFIXES) - ICONIFY_PREFIXES.index(prefix)
	except ValueError:
		pass

	return score


def select_iconify_candidate(
	key: str,
	name: str,
) -> str | None:
	"""Select an unambiguous Iconify search result."""
	queries = [name]

	if normalize(key) != normalize(name):
		queries.append(key.replace('-', ' '))

	candidates: set[str] = set()

	for query in queries:
		candidates.update(iconify_search(query))

	if not candidates:
		return None

	ranked = sorted(
		(
			(candidate_score(candidate, key, name), candidate)
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


def fetch_iconify(
	key: str,
	name: str,
) -> tuple[bytes, str] | None:
	"""Find and download an SVG from Iconify."""
	candidate = select_iconify_candidate(key, name)

	if candidate is None:
		return None

	prefix, icon_name = candidate.split(':', 1)

	url = (
		f'https://api.iconify.design/'
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


def fetch_logo(key: str, name: str) -> tuple[bytes, str] | None:
	"""Try known providers in priority order."""
	if key == 'delta-lake':
		delta = fetch_delta_lake()

		if delta is not None:
			return delta

	simple_icon = try_simple_icons(key)

	if simple_icon is not None:
		return simple_icon

	return fetch_iconify(key, name)


def main(argv: list[str]) -> int:
	"""Download all configured SVG sources that are currently missing."""
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		'--config',
		type=Path,
		default=CONFIG,
	)
	parser.add_argument(
		'--logo-dir',
		type=Path,
		default=LOGO_DIR,
	)
	parser.add_argument(
		'--force',
		action='store_true',
		help='replace existing valid SVG files too',
	)
	args = parser.parse_args(argv)

	entries = load_config(args.config)

	downloaded: list[str] = []
	skipped: list[str] = []
	failed: list[str] = []

	for entry in entries:
		destination = args.logo_dir / entry.file

		if is_valid_svg(destination) and not args.force:
			skipped.append(entry.key)
			continue

		print(f'FETCH    {entry.name:<24}', end='', flush=True)

		result = fetch_logo(entry.key, entry.name)

		if result is None:
			print('NOT FOUND')
			failed.append(entry.key)
			continue

		data, source_url = result

		try:
			atomic_write(destination, data)
		except OSError as exc:
			print(f'FAILED ({exc})')
			failed.append(entry.key)
			continue

		if not is_valid_svg(destination):
			destination.unlink(missing_ok=True)
			print('INVALID SVG')
			failed.append(entry.key)
			continue

		print(source_url)
		downloaded.append(entry.key)

	print()
	print(f'Downloaded: {len(downloaded)}')
	print(f'Skipped:    {len(skipped)}')
	print(f'Failed:     {len(failed)}')

	if downloaded:
		print()
		print('Downloaded logos:')
		for key in downloaded:
			print(f'  + {key}')

	if failed:
		print()
		print('Still unresolved:')
		for key in failed:
			print(f'  ! {key}')

		return 1

	return 0


if __name__ == '__main__':
	raise SystemExit(main(sys.argv[1:]))