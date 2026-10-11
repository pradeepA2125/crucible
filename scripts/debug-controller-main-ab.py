"""Bounded live-provider prompt/adapter comparison; only local read tools execute.

Run each variant in a fresh process to import its actual provider implementation.
This isolates prompt/native-history behavior, not VS Code or full middleware latency.
"""
import argparse
import asyncio
import copy
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--variant', choices=['main', 'current', 'current-main-guidance'], required=True)
parser.add_argument('--task', choices=['hello', 'repo'], required=True)
parser.add_argument('--repeat', type=int, default=0)
parser.add_argument('--prepare-only', action='store_true')
args = parser.parse_args()
baseline = args.variant == 'main'
source = Path('/private/tmp/crucible-main-ab') if baseline else ROOT
sys.path.insert(0, str(source / 'services/agentd-py'))
for flag in ['TOOL_BATCHING', 'ADAPTIVE_READS', 'COMPACT_CONTEXT']:
    os.environ[f'CRUCIBLE_{flag}_ENABLED'] = 'false' if baseline else 'true'

from agentd.chat.controller_prompts import (build_controller_step_payload,
    controller_response_schema, format_controller_system_prompt)
from agentd.chatgpt_auth.service import chatgpt_auth_service
from agentd.memory.embedder import Embedder
from agentd.memory.recall import RecallEngine
from agentd.memory.store import MemoryStore
from agentd.memory.tool_source import MemoryToolSource
from agentd.providers.openai_transport import OpenAIJsonTransport
from agentd.providers.reasoning_effort import ReasoningEffort
from agentd.tools.registry import ToolRegistry

ARTIFACT = ROOT / '.crucible/state/artifacts/chat/chat-ed1a4c98a919/9b1e265d4b984bda85b4dc2d6069d688/controller-turn-00.json'
captured = json.loads(ARTIFACT.read_text())
catalog = next(json.loads(line) for line in captured['system_instructions'].splitlines()
               if line.startswith('[{"description"'))
registry = ToolRegistry(ROOT, ROOT)
builtins = {d.name: d.model_dump() for d in registry.definitions()}
catalog = [builtins.get(d['name'], d) for d in catalog]
system = format_controller_system_prompt(catalog, task_subsystem_enabled=False, memory_enabled=True)
goal = 'hello' if args.task == 'hello' else 'what is this repo doing answer fast.'
outdir = ROOT / '.crucible/state/debug/main-current-ab' / f'{args.variant}-{args.task}-{args.repeat}'
outdir.mkdir(parents=True, exist_ok=True)
memory_path = outdir / 'memory.sqlite3'
shutil.copyfile('/private/tmp/crucible-ab-memory.sqlite3', memory_path)
store = MemoryStore(memory_path)
recall = RecallEngine(store, Embedder(), weights=(0.5, 0.3, 0.2))
memory = MemoryToolSource(None, 'workspace', str(ROOT), recall_engine=recall, store=store, allow_remember=False)
context = {'workspace_path': str(ROOT), 'retrieval_seed': copy.deepcopy(captured['user_payload']['retrieval_seed']),
           'goal': goal, 'max_iters': 500, 'active_entry': True, 'dispatch_available': True}
history = [{'role': 'user', 'content': goal}]
rows = []

async def main():
    if args.prepare_only:
        from agentd.providers.openai_native import native_actions, to_native_input
        payload = build_controller_step_payload(context, history, catalog, phase='ACTIVE')
        schema = controller_response_schema(phase='ACTIVE', tight=False, anyof=True)
        actions = native_actions(schema, 'ControllerStepResponse')
        request = {'instructions': system + actions.instructions, 'input': to_native_input(payload, actions),
                   'tools': actions.tools, 'tool_choice': actions.tool_choice,
                   'parallel_tool_calls': getattr(actions, 'multicall', False)}
        (outdir / 'prepared-request.json').write_text(json.dumps(request, indent=2)+'\n')
        print(json.dumps({'variant':args.variant, 'task':args.task,
                          'instructions_chars':len(request['instructions']), 'offline':True}))
        return
    service = chatgpt_auth_service()
    registration = service.store.list()[-1].registration_id
    transport = OpenAIJsonTransport(bearer=service.bearer(registration), plan_route=True)
    transport.set_reasoning_effort(ReasoningEffort.MEDIUM)
    if args.variant == 'current-main-guidance':
        import agentd.providers.openai_transport as module
        from agentd.providers.openai_native import UNION_INSTRUCTIONS
        native = module.native_actions
        def actions(schema, name):
            result = native(schema, name)
            result.instructions = UNION_INSTRUCTIONS
            return result
        module.native_actions = actions
    # Warm local memory dependencies before measuring the turn.
    await memory.execute('recall', {'query': goal})
    original_run = transport._run
    original_stream = transport._stream_once
    wire = []
    replies = []
    async def run(body, *a, **kw):
        wire.append(copy.deepcopy(body))  # request body only; authorization headers are excluded
        return await original_run(body, *a, **kw)
    async def stream(*a, **kw):
        reply = await original_stream(*a, **kw)
        replies.append({'text': reply.text, 'call': reply.call, 'calls': getattr(reply, 'calls', [])})
        return reply
    transport._run = run
    transport._stream_once = stream
    start = time.perf_counter()
    terminal = 'step_limit'
    for step in range(10):
        context['iteration'] = step
        payload = build_controller_step_payload(context, history, catalog, phase='ACTIVE')
        schema = controller_response_schema(phase='ACTIVE', tight=False, anyof=True)
        thinking = []
        before = time.perf_counter()
        try:
            async with asyncio.timeout(60):
                response = await transport.generate_json(model='gpt-5.6-sol', schema_name='ControllerStepResponse',
                    schema=schema, system_instructions=system, user_payload=payload,
                    on_thinking=thinking.append)
        except Exception as exc:
            terminal = 'provider_error'
            rows.append({'step': step, 'error': type(exc).__name__, 'message': str(exc)[:500]})
            break
        row = {'step': step, 'seconds': time.perf_counter()-before, 'response': response,
               'reasoning_summary': ''.join(thinking)}
        rows.append(row)
        print(json.dumps({'variant': args.variant, 'task': args.task, 'repeat': args.repeat,
                          'step': step, 'seconds': round(row['seconds'], 3), 'response': response}), flush=True)
        if response.get('type') in {'answer', 'clarify'}:
            terminal = str(response['type'])
            break
        calls = response.get('calls') if response.get('type') == 'tool_calls' else [response]
        if not isinstance(calls, list) or any(c.get('tool') not in {'recall','read_file','list_directory','search_code'} for c in calls):
            terminal = 'unsupported_action'
            break
        async def execute(call):
            tool = call['tool']
            output = await (memory.execute(tool, call['args']) if tool == 'recall'
                            else registry.execute(tool, call['args']))
            return {'role': 'tool_result', 'tool': tool, 'content': output.output,
                    'is_error': output.is_error, 'provider_call_id': call.get('provider_call_id'),
                    **({'delivered_read': asdict(output.delivered_read)}
                       if is_dataclass(getattr(output, 'delivered_read', None)) else {})}
        if response.get('type') == 'tool_calls':
            response = dict(response, batch_id=f'debug-{step}')
        history.append({'role': 'assistant', 'content': json.dumps(response)})
        results = await asyncio.gather(*(execute(c) for c in calls))
        for call, result in zip(calls, results):
            if response.get('type') == 'tool_calls':
                result.update(batch_id=response['batch_id'], id=call['id'],
                              call_id=f"{response['batch_id']}_{call['id']}",
                              status='error' if result['is_error'] else 'success')
            history.append(result)
        row['tool_results'] = results
    result = {'variant': args.variant, 'task': args.task, 'repeat': args.repeat,
              'model': 'gpt-5.6-sol', 'effort': 'medium', 'terminal': terminal,
              'seconds': time.perf_counter()-start, 'steps': len(rows), 'rows': rows,
              'limitations': 'Frozen captured retrieval seed; fresh user history; actual read/recall tools. '
              'No full controller middleware, UI, dedup, automatic recall or indexing. Unsupported tools stop the trial. '
              'Step limit is external: prompt retains 500-step budget to avoid a changed stopping instruction.'}
    (outdir / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
    (outdir / 'wire.json').write_text(json.dumps({'requests':wire, 'replies':replies}, indent=2)+'\n')
    print(json.dumps({k: result[k] for k in ['variant','task','repeat','terminal','seconds','steps']}), flush=True)

asyncio.run(main())
