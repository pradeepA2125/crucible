"""Offline request preparation and bounded live controller-contract diagnosis.

Live probes send repository context to the configured provider; never execute tools.
"""
import argparse
import asyncio
import copy
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--live', action='store_true', help='Send up to six approved diagnostic requests')
parser.add_argument('--registration', help='Explicit editor ChatGPT registration; required with --live')
parser.add_argument('--model', default='gpt-5.6-sol', help='The model selected in Crucible')
parser.add_argument('--artifact', type=Path, help='Replay this fresh controller artifact exactly')
parser.add_argument('--repetitions', type=int, choices=(1, 2), default=2)
args = parser.parse_args()
if args.live and not args.registration:
    parser.error('--live requires --registration from the editor configuration')
async def main():
    sys.path.insert(0, str(ROOT / 'services/agentd-py'))
    os.environ['CRUCIBLE_TOOL_BATCHING_ENABLED'] = 'false'
    os.environ['CRUCIBLE_ADAPTIVE_READS_ENABLED'] = 'false'
    os.environ['CRUCIBLE_COMPACT_CONTEXT_ENABLED'] = 'false'
    from agentd.chat.controller_prompts import controller_response_schema, _BATCH_GUIDANCE
    from agentd.chatgpt_auth.service import chatgpt_auth_service
    from agentd.providers.openai_native import (
        SessionInputs, forced_instructions, native_actions, session_id_for, to_native_input,
    )
    from agentd.providers.openai_strict_schema import encode_strict_schema
    from agentd.providers.openai_transport import OpenAIJsonTransport
    from agentd.providers.reasoning_effort import ReasoningEffort
    from agentd.providers.plan_access import find_access_stop

    trace = json.loads((ROOT / '.crucible/state/debug/array-only-hello-2026-10-10.json').read_text())
    artifact = args.artifact or Path(trace['turns'][0]['artifact'])
    captured = json.loads(artifact.read_text())
    system = captured['system_instructions']
    if not args.artifact:
        system = system.replace(_BATCH_GUIDANCE, '')
        system += '\nTOOL ARRAY: use tool_calls with exactly one calls member. Multiple tools in one step are disabled; choose the next tool after receiving its result.\n'
    payload = captured['user_payload']
    schema = captured['schema'] if args.artifact else controller_response_schema(
        phase='ACTIVE', anyof=True)
    out = ROOT / '.crucible/state/debug/controller-contract-probe.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    if any(entry.get('role') == 'assistant' for entry in payload['conversation_history']):
        raise RuntimeError('This comparison requires a fresh-turn artifact with no assistant history')

    service = chatgpt_auth_service() if args.live else None
    rows = []
    prepared = []
    for repetition in range(args.repetitions if args.live else 1):
        for variant in ['original_descriptions', 'response_envelope', 'explicit_descriptions']:
            transport = OpenAIJsonTransport(
                bearer=service.bearer(args.registration) if service else None,
                responses_client=None if args.live else object(),
                plan_route=True, max_retries=0)
            transport.set_reasoning_effort(ReasoningEffort.MEDIUM)
            actions = native_actions(schema, 'controller_step_response')
            body = transport._body(args.model, system + actions.instructions, payload)
            body.update(tools=copy.deepcopy(actions.tools), tool_choice=actions.tool_choice,
                        parallel_tool_calls=False, input=to_native_input(payload, actions))
            codec = None
            if variant == 'original_descriptions':
                # Keep the pre-repair baseline reproducible after installing the fix.
                for fn in body['tools'][0]['tools']:
                    if fn['name'] in {'answer', 'tool_calls'}:
                        fn['description'] = f"Take the `{fn['name']}` action."
            elif variant == 'response_envelope':
                codec = encode_strict_schema(schema)
                body['tools'] = [{'type':'function', 'name':'controller_step_response', 'description':'Choose the next controller action, including a final answer when the request is satisfied.', 'parameters':codec.schema, 'strict':True}]
                body['tool_choice'] = {'type':'function', 'name':'controller_step_response'}
                body['instructions'] = system + forced_instructions('controller_step_response')
            elif variant == 'explicit_descriptions':
                for fn in body['tools'][0]['tools']:
                    if fn['name'] == 'answer':
                        fn['description'] = 'Finish the user turn with your final response. Use directly for greetings and conversational requests that need no repository tools.'
                    elif fn['name'] == 'tool_calls':
                        fn['description'] = 'Execute workspace tools only when their results are needed to fulfill the user request. A greeting needs no tools.'
            # Match the production plan route, including its labelled current-step tail.
            items = body['input']
            body['input'] = SessionInputs().build('fresh-probe', items)
            session = session_id_for(
                f'thread:{artifact.parent.parent.name}', body['instructions'], items[0])
            if not args.live:
                prepared.append({'variant':variant, 'request':body})
                continue
            start = time.perf_counter()
            row = {'variant':variant, 'repetition':repetition}
            try:
                async with asyncio.timeout(55):
                    reply = await transport._run(
                        body, None, None, None, None, native=True,
                        headers={'session_id':session})
                row['response'] = codec.decode(json.loads(reply.call[1])) if codec else actions.decode_call(*reply.call)
                row['function'] = reply.call[0]
            except Exception as exc:
                row['error'] = str(exc)[:500]
                row['access_stop'] = find_access_stop(exc) is not None
            row['seconds'] = round(time.perf_counter()-start, 3)
            rows.append(row)
            out.write_text(json.dumps({'method':'Identical captured fresh hello context; independent transport per request; native current-step tail and session header; no tools executed. Explicit editor registration.', 'artifact':str(artifact), 'exact_capture':bool(args.artifact), 'model':args.model, 'effort':'medium', 'rows':rows}, indent=2)+'\n')
            print(json.dumps(row), flush=True)
            if row.get('access_stop'):
                break
        if rows and rows[-1].get('access_stop'):
            break
    if not args.live:
        destination = out.with_name('controller-contract-prepared.json')
        destination.write_text(json.dumps(prepared, indent=2)+'\n')
        assert all(p['request']['input'] == prepared[0]['request']['input'] for p in prepared)
        print(json.dumps({'offline':True, 'variants':len(prepared),
                          'identical_inputs':True, 'path':str(destination)}))

asyncio.run(main())
