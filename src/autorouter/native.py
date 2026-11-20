"""Replace the launcher with the isolated native Codex executable."""
import json
import os
from pathlib import Path
import sys


def package():
    return Path.home() / '.local/lib/autorouter'


def preference(name):
    config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return config / 'autorouter' / name


def code_mode_marker():
    return preference('code-mode')


def command(args):
    from .integrations import native_flags
    features = ['--no-daemon', '--enable', 'step_model_switching']
    if code_mode_marker().is_file():
        features += ['--enable', 'code_mode', '--enable', 'code_mode_tool_search']
    return [str(package() / 'codex'), *features, *native_flags(), *args]


def control(args, binary):
    if args == ["routing", "decisions"]:
        import sqlite3
        from .telemetry import routing_events
        try:
            print(json.dumps(routing_events(limit=60), indent=2))
        except (OSError, sqlite3.Error) as error:
            sys.exit(f"AutoRouter routing log unavailable: {error}")
    elif len(args) == 4 and args[:2] == ["routing", "usage"]:
        from .learning import turn_metrics
        try:
            print(json.dumps(turn_metrics(*args[2:]), indent=2))
        except (OSError, ValueError) as error:
            sys.exit(f"AutoRouter usage: {error}")
    elif args == ["capabilities"]:
        from .cli import capabilities, paths
        from . import graph
        state, _, _ = paths()
        print(json.dumps({
            "release": "3.2",
            "integrations": capabilities(),
            "graphify": {name: graph.status(*location) for name, location in graph.projects(state).items()},
            "models": "autorouter models status",
            "routing": "autorouter routing decisions",
            "code_mode_opt_in": code_mode_marker().is_file(),
            "knowledge": "autorouter-memory/knowledge_search",
            "roles": ["autorouter_explorer", "autorouter_reviewer", "autorouter_test_investigator"],
        }, indent=2))
    elif args[:1] == ["learning"]:
        import argparse
        import subprocess
        from .cli import paths
        from . import learning
        parser = argparse.ArgumentParser(prog="autorouter learning")
        commands = parser.add_subparsers(dest="operation", required=True)
        record = commands.add_parser("record")
        record.add_argument("session")
        record.add_argument("turn")
        record.add_argument("category", choices=learning.CATEGORIES)
        record.add_argument("--feedback", choices=learning.FEEDBACK, default="unspecified")
        commands.add_parser("status")
        for action in ("promote", "rollback"):
            commands.add_parser(action).add_argument("id")
        options = parser.parse_args(args[1:])
        state, _, _ = paths()
        store = learning.Store(state / "tasks.sqlite")
        try:
            if options.operation == "record":
                data = learning.record(store, options.session, options.turn, options.category, options.feedback)
            elif options.operation == "status":
                data = learning.patterns(store)
            else:
                data = learning.set_pattern(store, options.id, options.operation == "promote")
            print(json.dumps(data, indent=2))
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            sys.exit(f"AutoRouter learning: {error}")
    elif args == ["security", "audit"]:
        import subprocess
        from .security import audit
        try:
            print(json.dumps(audit(), indent=2))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            sys.exit(f"AutoRouter security audit: {error}")
    elif args[:1] in (['graph'], ['knowledge']):
        import argparse
        import subprocess
        from .cli import paths
        from . import graph
        parser = argparse.ArgumentParser(prog=f"autorouter {args[0]}")
        parser.add_argument("operation", choices=(
            ("status", "query", "refresh") if args[0] == "graph" else ("search",)
        ))
        parser.add_argument("query", nargs="?")
        parser.add_argument("--project", choices=("autorouter", "native"))
        parser.add_argument("--force", action="store_true")
        options = parser.parse_args(args[1:])
        state, _, conf = paths()
        projects = graph.projects(state)
        targets = {options.project: projects[options.project]} if options.project else projects
        try:
            if options.operation == "refresh":
                for root, directory in targets.values():
                    graph.refresh(root, directory, options.force)
                return True
            if options.operation == "status":
                data = {name: graph.status(*location) for name, location in targets.items()}
            elif not options.query:
                parser.error("a query is required")
            elif options.operation == "query":
                data = graph.query(*projects[options.project or "autorouter"], options.query[:240])
            else:
                from .mcp import knowledge_search
                from .memory import Memory
                memory = Memory(conf["vault"], state / "memory.sqlite", conf.get("embeddings"))
                data = knowledge_search(memory, state, options.query, options.project or "autorouter")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            sys.exit(f"AutoRouter {args[0]}: {error}")
        print(json.dumps(data, indent=2))
    elif len(args) == 2 and args[0] in ('routing', 'code-mode') and args[1] in ('status', 'on', 'off'):
        feature, action = args
        marker = preference('routing-off') if feature == 'routing' else code_mode_marker()
        if action != 'status':
            if (action == 'on') == (feature == 'code-mode'):
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
            else:
                marker.unlink(missing_ok=True)
        enabled = marker.is_file() == (feature == 'code-mode')
        status = 'on' if enabled else 'off'
        if feature == 'code-mode':
            print(f'AutoRouter Code Mode opt-in is {status}. Provider-defined tool modes still apply.')
        else:
            print(f'Automatic routing is {status} for new launches.')
    elif args[:1] == ['update'] and args[1:] in (
        ['check'], ['stage'], ['automatic'], ['activate'], ['rollback'],
    ):
        from .updater import command as update
        try:
            update(args[1:], binary, package(), preference('auto-activate'))
        except (OSError, RuntimeError) as error:
            sys.exit(f"AutoRouter update stopped: {error}")
    elif args[:2] == ['update', 'auto-activate'] and args[2:] in (['on'], ['off']):
        marker = preference('auto-activate')
        if args[2] == 'on':
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        else:
            marker.unlink(missing_ok=True)
        print(f"Automatic activation {args[2]}")
    elif args == ['artifacts', 'status']:
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        root = codex_home / "autorouter/tool-artifacts"
        unresolved = [
            str(item.parent) for item in root.glob("*/*/started.json")
            if not (item.parent / "result.json").is_file()
        ]
        print(json.dumps({
            "directory": str(root), "unresolvedCount": len(unresolved),
            "unresolved": sorted(unresolved)[-20:],
        }, indent=2))
    elif args == ['models', 'evaluate']:
        from .evaluation import run
        try:
            run(binary)
        except (RuntimeError, OSError) as error:
            sys.exit(f"AutoRouter evaluation stopped: {error}")
    elif len(args) == 3 and args[:2] == ['models', 'benchmark']:
        from .rpc import RPC
        import subprocess
        import time

        model = args[2]
        with RPC(timeout=20, binary=binary) as rpc:
            profile = next((entry for entry in rpc.models() if entry['model'] == model), None)
        if profile is None:
            sys.exit('Choose a visible provider model from autorouter models')
        started = time.monotonic()
        run = subprocess.run([
            binary, '--no-daemon', '--ask-for-approval', 'never', 'exec', '--sandbox', 'read-only', '--skip-git-repo-check',
            '--ephemeral', '--json', '--model', model, '--', 'Reply with exactly AUTOROUTER_SMOKE_OK.',
        ], text=True, capture_output=True, timeout=60)
        events = [json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')]
        usage = next((item.get('usage') for item in events if item.get('type') == 'turn.completed'), None)
        passed = run.returncode == 0 and usage is not None and any(
            item.get('type') == 'item.completed'
            and item.get('item', {}).get('type') == 'agent_message'
            and item['item'].get('text', '').strip() == 'AUTOROUTER_SMOKE_OK'
            for item in events
        )
        print(json.dumps({
            'model': model, 'passed': passed, 'exitCode': run.returncode,
            'elapsedSeconds': round(time.monotonic() - started, 3), 'usage': usage,
        }, indent=2))
        if not passed:
            sys.exit(1)
    elif args in (['models'], ['models', 'status'], ['models', 'refresh']):
        from .rpc import RPC
        from .routing import family_version
        env = dict(os.environ)
        if args[-1] == 'refresh':
            env['AUTOROUTER_FORCE_MODEL_REFRESH'] = '1'
        with RPC(timeout=20, binary=binary, env=env) as rpc:
            config = rpc.call('config/read')['config']
            models = rpc.models()
        families = {}
        for model in models:
            version = family_version(model['model'])
            if version:
                family, number = version
                if family not in families or number > families[family][0]:
                    families[family] = (number, model['model'])
        print(json.dumps({
            'source': 'Codex App Server model/list (can include cached or bundled entries)',
            'provider': config.get('model_provider') or 'openai',
            'catalogOverride': bool(config.get('model_catalog_json')),
            'refreshRequested': args[-1] == 'refresh',
            'familyAliases': {family + '.latest': value[1] for family, value in families.items()},
            'models': models,
        }, indent=2))
    elif args in (['doctor'], ['memory', 'doctor']):
        from .cli import main as admin
        sys.argv[1:] = ['doctor', '--live'] if args == ['doctor'] else args
        admin()
    else:
        return False
    return True


def main():
    args = sys.argv[1:]
    if args[:1] and args[0] in {"install", "uninstall", "hosts", "workspace", "serve", "session-context"}:
        from .hosts import main as host_main
        return host_main(args)
    binary = str(package() / "codex")
    if not Path(binary).is_file():
        sys.exit('The optional native AutoRouter Codex build is unavailable. Run autorouter hosts for portable integrations.')
    if control(args, binary):
        return
    argv = command(args)
    os.environ.pop("AUTOROUTER_RECOMMENDATIONS", None)
    if preference('routing-off').is_file():
        os.environ.pop('AUTOROUTER_ENABLED', None)
    else:
        os.environ['AUTOROUTER_ENABLED'] = '1'
        from .integrations import PROJECT
        if Path.cwd().is_relative_to(PROJECT):
            from .learning import recommendations
            import sqlite3
            state = Path(os.environ.get("AUTOROUTER_HOME") or Path.home() / ".local/state/autorouter")
            try:
                os.environ["AUTOROUTER_RECOMMENDATIONS"] = json.dumps(recommendations(state / "tasks.sqlite"))
            except (ValueError, OSError, sqlite3.DatabaseError):
                os.environ.pop("AUTOROUTER_RECOMMENDATIONS", None)
    flags = args[:args.index('--')] if '--' in args else args
    config_values = []
    for index, arg in enumerate(flags):
        if arg in ('-c', '--config') and index + 1 < len(flags):
            config_values.append(flags[index + 1])
        elif arg.startswith('--config='):
            config_values.append(arg[len('--config='):])
        elif arg.startswith('-c') and not arg.startswith('--'):
            config_values.append(arg[2:])
    keys = {value.split('=', 1)[0].strip() for value in config_values}
    if 'model' in keys or any(a == '--model' or a.startswith('--model=') or (a.startswith('-m') and not a.startswith('--')) for a in flags):
        os.environ['AUTOROUTER_PIN_MODEL'] = '1'
    if 'model_reasoning_effort' in keys:
        os.environ['AUTOROUTER_PIN_EFFORT'] = '1'
    import fcntl
    lease = os.open(package() / '.activation.lock', os.O_CREAT | os.O_RDONLY, 0o600)
    try:
        fcntl.flock(lease, fcntl.LOCK_SH)
        os.set_inheritable(lease, True)
        os.execv(argv[0], argv)
    finally:
        os.close(lease)


if __name__ == '__main__':
    main()
