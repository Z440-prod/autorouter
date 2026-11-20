#!/usr/bin/env python3
"""Live PTY harness. Saves real terminal output; never supplies mocked responses."""
import argparse
import json
import os
from pathlib import Path
import time
import pexpect

p = argparse.ArgumentParser()
p.add_argument('executable')
p.add_argument('--prompt', action='append', default=[])
p.add_argument('--resume', action='store_true')
p.add_argument('--sandbox')
p.add_argument('--deny-approval', action='store_true')
p.add_argument('--bare', action='store_true')
p.add_argument('--autorouter', action='store_true', help='Activate routing on a staged native binary')
p.add_argument('--model')
p.add_argument('--effort')
p.add_argument('--cancel-after', type=float)
p.add_argument('--cwd', default=str(Path(__file__).resolve().parents[1] / 'examples/ledger'))
p.add_argument('--log', required=True)
a = p.parse_args()
runtime = Path(a.log).resolve().with_suffix('.runtime')
args = ['--no-alt-screen', '-C', a.cwd, '-c', 'log_dir=' + json.dumps(str(runtime))]
if a.bare:
    args = []
if a.sandbox:
    args += ['-s', a.sandbox, '-a', 'on-request']
if a.model:
    args += ['-m', a.model]
if a.effort:
    args += ['-c', 'model_reasoning_effort=' + a.effort]
if a.resume:
    args += ['resume', '--last']
env = dict(os.environ, TERM='xterm-256color', RUST_LOG='autorouter=info')
if a.autorouter:
    args = ['--no-daemon', '--enable', 'step_model_switching', *args]
    env['AUTOROUTER_ENABLED'] = '1'
    if a.model:
        env['AUTOROUTER_PIN_MODEL'] = '1'
    if a.effort:
        env['AUTOROUTER_PIN_EFFORT'] = '1'
with open(a.log, 'w') as log:
    child = pexpect.spawn(a.executable, args, cwd=a.cwd, env=env, encoding='utf-8', timeout=120, dimensions=(38, 120))
    child.logfile_read = log

    def wait_for(pattern, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            match = child.expect([pattern, '\x1b\\[6n', 'Do you trust|Trust this folder', 'Press enter to continue', 'github.com/openai/codex/releases/latest', pexpect.EOF], timeout=max(1, deadline-time.monotonic()))
            if match == 0:
                return
            if match == 1:
                child.send('\x1b[1;1R')
            elif match in (2, 3):
                child.send('\r')
            elif match == 4:
                child.send('\x1b')
            else:
                raise RuntimeError('Codex exited unexpectedly')
        raise TimeoutError(pattern)

    wait_for('state=idle:app=codex')
    time.sleep(3)
    child.setwinsize(42, 130)
    for prompt in a.prompt:
        child.send('\x1b[200~' + prompt + '\x1b[201~')
        time.sleep(0.5)
        child.send('\r')
        wait_for('state=working:app=codex', timeout=30)
        if a.deny_approval:
            wait_for('Would you like|Do you want to|approve this|Approve|Allow once', timeout=120)
            child.send('\x1b')
            wait_for('state=done:app=codex|state=idle:app=codex', timeout=120)
        elif a.cancel_after is not None:
            time.sleep(a.cancel_after)
            child.sendcontrol('c')
            wait_for('state=idle:app=codex|state=done:app=codex|Interrupted', timeout=30)
        else:
            wait_for('state=done:app=codex', timeout=300)
        time.sleep(1)
    if not a.bare:
        child.send('/status')
        time.sleep(0.5)
        child.send('\r')
        time.sleep(1)
        child.send('/diff')
        time.sleep(0.5)
        child.send('\r')
        time.sleep(1)
        child.send('q')
        time.sleep(0.3)
        child.send('\x1b')
        child.sendcontrol('u')
    child.sendcontrol('c')
    time.sleep(0.5)
    child.sendcontrol('c')
    try:
        child.expect(pexpect.EOF, timeout=15)
    except pexpect.TIMEOUT:
        child.send('\x1b')
        child.sendcontrol('u')
        child.sendcontrol('d')
        child.expect(pexpect.EOF, timeout=15)
    child.close()
    print({'exitstatus': child.exitstatus, 'signalstatus': child.signalstatus, 'log': a.log})
    assert child.exitstatus == 0, 'Expected native Codex to exit normally'
text = Path(a.log).read_text()
assert 'AutoRouter >' not in text
assert len(text) > 500, 'Expected a real terminal UI transcript'
