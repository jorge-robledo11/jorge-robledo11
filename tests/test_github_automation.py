import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'opencommit.yaml'
ENV_FILE = ROOT / '.github' / 'opencomit.env'
CHECK_SCRIPT = ROOT / '.github' / 'scripts' / 'check-environment.sh'
UPDATE_SCRIPT = ROOT / '.github' / 'scripts' / 'update-readme.sh'
OPENCOMMIT_IGNORE = ROOT / '.opencommitignore'
REPOMIX_IGNORE = ROOT / '.repomixignore'


def _workflow() -> dict:
	return yaml.load(WORKFLOW.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


def _run(
	script: Path,
	cwd: Path,
	env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
	bash = shutil.which('bash')
	assert bash is not None
	return subprocess.run(
		[bash, str(script)],
		cwd=cwd,
		env=env,
		text=True,
		capture_output=True,
		check=False,
	)


def _patterns(path: Path) -> set[str]:
	return {
		line.strip()
		for line in path.read_text(encoding='utf-8').splitlines()
		if line.strip() and not line.lstrip().startswith('#')
	}


def test_workflow_targets_main_and_uses_existing_paths() -> None:
	workflow = _workflow()
	assert set(workflow['on']) == {'workflow_dispatch'}
	assert workflow['permissions']['contents'] == 'write'
	assert workflow['concurrency']['group'] == 'opencommit-main'
	assert workflow['concurrency']['cancel-in-progress'] == 'false'

	job = workflow['jobs']['readme']
	assert job['runs-on'] == 'self-hosted'

	steps = job['steps']
	checkout = next(step for step in steps if step.get('name') == 'Checkout main')
	assert checkout['with']['ref'] == 'main'

	commands = '\n'.join(step.get('run', '') for step in steps)
	assert '.github/opencomit.env' in commands
	assert 'bash .github/scripts/check-environment.sh' in commands
	assert 'bash .github/scripts/update-readme.sh' in commands
	assert 'git pull --rebase origin main' in commands
	assert 'git push origin HEAD:main' in commands

	assert ENV_FILE.is_file()
	assert CHECK_SCRIPT.is_file()
	assert UPDATE_SCRIPT.is_file()


def test_opencommit_env_has_required_local_ollama_settings() -> None:
	settings = {}
	for raw_line in ENV_FILE.read_text(encoding='utf-8').splitlines():
		line = raw_line.strip()
		if not line or line.startswith('#'):
			continue
		key, value = line.split('=', 1)
		settings[key] = value

	required = {
		'OCO_AI_PROVIDER',
		'OCO_API_URL',
		'OCO_MODEL',
		'OCO_TOKENS_MAX_INPUT',
		'OCO_TOKENS_MAX_OUTPUT',
		'OCO_LANGUAGE',
		'OCO_ONE_LINE_COMMIT',
		'OCO_OLLAMA_THINK',
		'OCO_GITPUSH',
	}
	assert required <= settings.keys()
	assert settings['OCO_AI_PROVIDER'] == 'ollama'
	assert settings['OCO_API_URL'].startswith('http://127.0.0.1:')
	assert settings['OCO_MODEL']
	assert settings['OCO_GITPUSH'] == 'false'


def test_github_scripts_are_valid_bash() -> None:
	bash = shutil.which('bash')
	assert bash is not None

	for script in (CHECK_SCRIPT, UPDATE_SCRIPT):
		result = subprocess.run(
			[bash, '-n', str(script)],
			text=True,
			capture_output=True,
			check=False,
		)
		assert result.returncode == 0, result.stderr


def test_update_readme_keeps_one_marker_and_stages_only_readme() -> None:
	with tempfile.TemporaryDirectory() as tmp:
		repository = Path(tmp)
		subprocess.run(['git', 'init', '-q'], cwd=repository, check=True)
		(repository / 'README.md').write_text('# Profile\n', encoding='utf-8')
		subprocess.run(['git', 'add', 'README.md'], cwd=repository, check=True)
		subprocess.run(
			[
				'git',
				'-c',
				'user.name=Test',
				'-c',
				'user.email=test@example.com',
				'commit',
				'-qm',
				'initial',
			],
			cwd=repository,
			check=True,
		)

		env = os.environ | {'GITHUB_RUN_ID': '123'}
		first = _run(UPDATE_SCRIPT, repository, env)
		assert first.returncode == 0, first.stderr

		env['GITHUB_RUN_ID'] = '456'
		second = _run(UPDATE_SCRIPT, repository, env)
		assert second.returncode == 0, second.stderr

		content = (repository / 'README.md').read_text(encoding='utf-8')
		assert content.count('<!-- opencommit-run:') == 1
		assert '<!-- opencommit-run: 456 -->' in content

		staged = subprocess.run(
			['git', 'diff', '--cached', '--name-only'],
			cwd=repository,
			text=True,
			capture_output=True,
			check=True,
		).stdout.splitlines()
		assert staged == ['README.md']


def test_update_readme_fails_without_readme() -> None:
	with tempfile.TemporaryDirectory() as tmp:
		result = _run(
			UPDATE_SCRIPT,
			Path(tmp),
			os.environ | {'GITHUB_RUN_ID': '123'},
		)

	assert result.returncode != 0
	assert 'README.md no existe' in result.stdout


def test_check_environment_works_without_network_using_fake_commands() -> None:
	with tempfile.TemporaryDirectory() as tmp:
		fake_bin = Path(tmp)
		for name in ('oco', 'curl'):
			executable = fake_bin / name
			executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
			executable.chmod(0o755)

		env = os.environ | {
			'PATH': f"{fake_bin}:{os.environ.get('PATH', '')}",
			'OCO_API_URL': 'http://127.0.0.1:11434',
		}
		result = _run(CHECK_SCRIPT, ROOT, env)

	assert result.returncode == 0, result.stderr
	assert 'OpenCommit disponible' in result.stdout
	assert 'Ollama disponible' in result.stdout


def test_opencommit_ignore_excludes_generated_assets() -> None:
	patterns = _patterns(OPENCOMMIT_IGNORE)

	assert 'assets/badges/**' in patterns
	assert 'assets/logos/**' in patterns
	assert '*.xml' in patterns


def test_opencommit_env_is_not_ignored_by_git() -> None:
	with tempfile.TemporaryDirectory() as tmp:
		repository = Path(tmp)
		subprocess.run(['git', 'init', '-q'], cwd=repository, check=True)
		shutil.copy(ROOT / '.gitignore', repository / '.gitignore')
		env_file = repository / '.github' / 'opencomit.env'
		env_file.parent.mkdir()
		env_file.touch()

		result = subprocess.run(
			['git', 'check-ignore', '-q', '--no-index', '.github/opencomit.env'],
			cwd=repository,
			check=False,
		)

	assert result.returncode == 1


def test_repomix_ignore_keeps_only_useful_llm_context() -> None:
	patterns = _patterns(REPOMIX_IGNORE)

	assert '/*' in patterns
	assert '!/.github/' in patterns
	assert '!/config/' in patterns
	assert '!/scripts/' in patterns
	assert '!/tests/' in patterns
	assert '!/.opencommitignore' in patterns
	assert '!/.repomixignore' in patterns
	assert '!/pyproject.toml' in patterns
	assert '!/README.md' in patterns
	assert not any(pattern.startswith('!/assets') for pattern in patterns)
