# Installation

Wayfinder 0.2.0 requires Python 3.11 or newer and uses only the Python standard library plus browser-native JavaScript.

## Choose the artifact for your host

| Host and install style | Artifact | Invocation |
| --- | --- | --- |
| ChatGPT desktop or Codex standalone | `wayfinder-openai-skill-0.2.0.zip` | `@wayfinder` in ChatGPT; `$wayfinder` in Codex |
| Claude Code standalone | `wayfinder-claude-skill-0.2.0.zip` | `/wayfinder` |
| Codex plugin | `wayfinder-openai-plugin-0.2.0.zip` | `$wayfinder` |
| Claude Code plugin | `wayfinder-claude-plugin-0.2.0.zip` | `/wayfinder:wayfinder` |

Download artifacts and `SHA256SUMS.txt` from the [v0.2.0 release](https://github.com/Mohammed-Moniem/wayfinder/releases/tag/v0.2.0). Verify the archive before extracting it:

```bash
shasum -a 256 -c SHA256SUMS.txt
```

On Linux, `sha256sum -c SHA256SUMS.txt` provides the equivalent check.

## Standalone OpenAI skill

Personal installation:

```bash
mkdir -p "$HOME/.agents/skills"
unzip wayfinder-openai-skill-0.2.0.zip -d "$HOME/.agents/skills"
```

Repository-scoped installation:

```bash
mkdir -p /path/to/project/.agents/skills
unzip wayfinder-openai-skill-0.2.0.zip -d /path/to/project/.agents/skills
```

The resulting entrypoint is `wayfinder/SKILL.md`. Start a new task if the host loaded its skill catalog before installation.

OpenAI currently documents standalone skills in ChatGPT desktop, the Codex app/CLI, and the Codex IDE extension. The current support matrix does not list plugin installs for the IDE extension, so use the standalone artifact there. See the [OpenAI skill documentation](https://developers.openai.com/codex/build-skills).

## Standalone Claude Code skill

Personal installation:

```bash
mkdir -p "$HOME/.claude/skills"
unzip wayfinder-claude-skill-0.2.0.zip -d "$HOME/.claude/skills"
```

Repository-scoped installation:

```bash
mkdir -p /path/to/project/.claude/skills
unzip wayfinder-claude-skill-0.2.0.zip -d /path/to/project/.claude/skills
```

The generated Claude entrypoint includes `disable-model-invocation: true`, which preserves Wayfinder's explicit-only contract. See the [Claude Code skills documentation](https://code.claude.com/docs/en/slash-commands).

## Codex plugin

Extract the OpenAI plugin ZIP to a stable absolute directory:

```bash
mkdir -p /absolute/path/to/wayfinder-openai-plugin-0.2.0
unzip wayfinder-openai-plugin-0.2.0.zip -d /absolute/path/to/wayfinder-openai-plugin-0.2.0
codex plugin marketplace add /absolute/path/to/wayfinder-openai-plugin-0.2.0
codex plugin add wayfinder@wayfinder-local
```

Keep the extracted directory at that path while the marketplace remains configured. Begin a new Codex task after installation. See the [OpenAI plugin documentation](https://developers.openai.com/plugins/build/plugins).

## Claude Code plugin

Claude Code 2.1.128 or newer can load an extracted directory or the ZIP directly:

```bash
claude --plugin-dir ./wayfinder-claude-plugin-0.2.0.zip
```

Claude namespaces plugin skills by plugin name, so the invocation becomes `/wayfinder:wayfinder`. Use the standalone skill when the shorter `/wayfinder` selector is preferred. See the [Claude Code plugin documentation](https://code.claude.com/docs/en/plugins).

## Build locally

```bash
git clone https://github.com/Mohammed-Moniem/wayfinder.git
cd wayfinder
python3 scripts/package_wayfinder.py build --output-dir build/wayfinder
python3 scripts/package_wayfinder.py verify build/wayfinder/*.zip
```

The source checkout is itself an OpenAI-valid plugin root. Claude users should install a generated Claude artifact because Claude's explicit-only frontmatter is intentionally derived rather than duplicated in source.

## Confirm the installation

- ChatGPT desktop: type `@wayfinder`.
- Codex app, CLI, or IDE: type `$wayfinder` or inspect `/skills`.
- Claude standalone: type `/wayfinder`.
- Claude plugin: type `/wayfinder:wayfinder`.

Wayfinder should ask for the intended outcome or resume the selected project's active effort. It should not start implicitly.

## Uninstall

For standalone skills, remove only the installed `wayfinder` directory from the host's skills folder. For the Codex plugin, remove the installed plugin and marketplace before deleting the extracted directory. Removing the installed skill does not delete project-local `.codex/wayfinder/` planning state.
