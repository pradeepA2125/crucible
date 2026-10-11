#!/usr/bin/env python3
"""Replay the three-prompt read/description scenario through the live backend.

Exports timing spans, reduced SSE events and call arguments, without credentials,
source results or model reasoning. Measures HTTP/SSE, excluding webview rendering.
"""
import argparse
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/agentd-py'))
from agentd.performance import summarize  # noqa: E402

PROMPTS = [
    'Read lines 1-700 of services/agentd-py/agentd/chat/controller_prompts.py and all '
    'of services/agentd-py/agentd/chat/batch_history.py. These reads are independent; '
    'issue both read_file calls in one tool_calls batch. The 700-line range is '
    'intentional to test adaptive read delivery beyond the old 500-line ceiling. '
    'Then briefly explain where the tool_calls action description is defined and '
    'what interrupted_history does. Do not modify files.',
    'does all actions have descriptions in spec?',
    'does all tools have descriptions in spec?',
]


def actions(directory):
    result = []
    for path in sorted(directory.glob('*/controller-turn-*.json')):
        try:
            artifact = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        raw = artifact.get('raw_result', {})
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = {}
        result.append({'artifact': str(path), 'type': raw.get('type'),
                       'calls': [{k: c[k] for k in ('id', 'tool', 'args') if k in c}
                                 for c in raw.get('calls', [])]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--timeout', type=float, default=0,
                        help='Optional turn deadline in seconds; zero waits for completion')
    parser.add_argument('--max-actions', type=int, default=0,
                        help='Optional action guard; zero waits for completion')
    parser.add_argument('--thread-id', help='Continue an existing chat instead of creating one')
    parser.add_argument('--prompt-file', type=Path,
                        help='Send this one prompt instead of the default three-prompt scenario')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    state = workspace / '.crucible/state'
    source = state / 'performance.jsonl'
    marker = state / 'performance.enabled'
    previous_marker = marker.read_bytes() if marker.exists() else None
    token = (Path.home() / f'.crucible/run/agentd-{args.port}.token').read_text()
    base = f'http://127.0.0.1:{args.port}/v1'
    report = {'boundary': 'HTTP POST through SSE chat_done; webview rendering excluded',
              'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'timeout_per_turn_seconds': args.timeout, 'max_actions_per_turn': args.max_actions,
              'turns': [], 'configuration': 'Current backend settings unchanged',
              'profiling_note': 'Wall spans and synchronous cProfile; includes profiler overhead'}

    def request(path, payload):
        return urllib.request.urlopen(urllib.request.Request(
            base + path, data=json.dumps(payload).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}),
            timeout=args.timeout + 10 if args.timeout > 0 else None)

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')

    marker.write_text('enabled\n')
    try:
        thread = args.thread_id
        if thread is None:
            with request('/chat/threads', {'workspace': str(workspace),
                                         'title': 'Profile: batch reads and descriptions'}) as response:
                thread = json.load(response)['thread_id']
        report['thread_id'] = thread
        directory = state / 'artifacts/chat' / thread
        print(f'Thread {thread}', flush=True)
        prompts = [args.prompt_file.read_text().strip()] if args.prompt_file else PROMPTS
        for index, prompt in enumerate(prompts, 1):
            offset = source.stat().st_size if source.exists() else 0
            before = {a['artifact'] for a in actions(directory)}
            turn = {'number': index, 'prompt': prompt, 'events': [], 'response': ''}
            report['turns'].append(turn)
            start = time.perf_counter_ns()
            turn['capture_start_ns'] = start
            completed = threading.Event()

            def stop(reason):
                turn['stop_reason'] = reason
                try:
                    with request(f'/chat/threads/{thread}/stop', {}) as response:
                        response.read()
                    turn['stop_requested'] = True
                except Exception as exc:
                    turn['stop_error'] = type(exc).__name__

            def expire():
                while not completed.wait(.25):
                    current = [a for a in actions(directory) if a['artifact'] not in before]
                    if args.max_actions > 0 and len(current) >= args.max_actions and current[-1]['type'] not in (
                            'answer', 'clarify', 'submit_changes', 'report'):
                        stop('action limit')
                        return
                    if args.timeout > 0 and (time.perf_counter_ns() - start) / 1e9 >= args.timeout:
                        stop('time limit')
                        return

            watcher = None
            if args.timeout > 0 or args.max_actions > 0:
                watcher = threading.Thread(target=expire, daemon=True)
                watcher.start()
            print(f'Turn {index}: sending prompt', flush=True)
            try:
                with request(f'/chat/threads/{thread}/message', {
                        'content': prompt, 'plan_mode': False, 'step_review': False}) as response:
                    turn['http_headers_seconds'] = (time.perf_counter_ns() - start) / 1e9
                    for line in response:
                        if not line.startswith(b'data: '):
                            continue
                        event = json.loads(line[6:])
                        kind, payload = event.get('type'), event.get('payload') or {}
                        record = {'type': kind, 'seconds': (time.perf_counter_ns() - start) / 1e9}
                        if isinstance(payload, dict):
                            for key in ('tool', 'tool_name', 'status', 'call_index', 'is_error'):
                                if isinstance(payload.get(key), (str, int, bool)):
                                    record[key] = payload[key]
                        turn['events'].append(record)
                        if kind == 'chat_response':
                            turn.setdefault('first_response_seconds', record['seconds'])
                            turn['response'] += str(payload.get('chunk', ''))
                        if kind in ('tool_call', 'tool_result', 'chat_response', 'chat_done', 'done'):
                            print(f"Turn {index}: {record['seconds']:.3f}s {record}", flush=True)
                        if kind in ('chat_done', 'done'):
                            turn['done_seconds'] = record['seconds']
                            break
            except Exception as exc:
                turn['client_error'] = type(exc).__name__
                stop('client error')
            finally:
                completed.set()
                if watcher is not None:
                    watcher.join(timeout=2)
                records = []
                # Allow the detached backend task to close its root timing span.
                for _ in range(50):
                    if source.exists():
                        with source.open() as stream:
                            stream.seek(offset)
                            records = [json.loads(line) for line in stream if line.strip()]
                    roots = [r for r in records if r.get('kind') == 'span'
                             and r.get('name') == 'controller.backend_turn']
                    if roots:
                        trace = roots[-1]['trace']
                        records = [r for r in records if r.get('trace') == trace]
                        break
                    time.sleep(.1)
                turn['backend_records'] = records
                turn['backend'] = summarize(records)
                turn['actions'] = [a for a in actions(directory) if a['artifact'] not in before]
                save()
            print(f"Turn {index}: {len(turn['actions'])} model actions; saved {args.output}", flush=True)
            if turn.get('client_error') or turn.get('stop_reason') or 'done_seconds' not in turn:
                break
    finally:
        if previous_marker is None:
            marker.unlink(missing_ok=True)
        else:
            marker.write_bytes(previous_marker)
        save()


if __name__ == '__main__':
    main()
