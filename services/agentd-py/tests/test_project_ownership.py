import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport,AsyncClient
from agentd.project_ownership import ProjectReservation,ProjectOwner,ProjectInUse,OWNER
from agentd.auth import AuthMiddleware,AuthState
from tests.test_approval_mode import app_for
from tests.test_background_dispatch import _setup


def test_two_actual_processes_reserve_canonical_alias_and_exit_recovers(tmp_path):
    root=tmp_path/"project";root.mkdir();alias=tmp_path/"alias";alias.symlink_to(root,target_is_directory=True)
    code="from agentd.project_ownership import ProjectReservation; import sys; r=ProjectReservation(sys.argv[1]);print('reserved',flush=True);input()"
    first=subprocess.Popen([sys.executable,"-c",code,str(root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert first.stdout.readline().strip()=="reserved"
        blocked=subprocess.run([sys.executable,"-c",code,str(alias)],input="",capture_output=True,text=True,timeout=5)
        assert blocked.returncode!=0 and "already controlled" in blocked.stderr
        assert not list(root.rglob("*.sqlite3"))
    finally:first.communicate("\n",timeout=5)
    inode=(root/".crucible/state/agentd.owner").stat().st_ino
    with_reservation=ProjectReservation(alias);assert with_reservation.root==root.resolve();with_reservation.close()
    assert (root/".crucible/state/agentd.owner").stat().st_ino==inode


def test_refuses_linked_state_foreign_live_pid_and_corrupt_lock_without_deletion(tmp_path):
    root=tmp_path/"project";root.mkdir();outside=tmp_path/"outside";outside.mkdir();(root/".crucible").symlink_to(outside,target_is_directory=True)
    with pytest.raises(ProjectInUse):ProjectReservation(root)
    assert not list(outside.iterdir());(root/".crucible").unlink()
    r=ProjectReservation(root);r.close();lock=root/".crucible/state/agentd.lock"
    for body in ('{broken',json.dumps({"pid":os.getpid(),"port":12345,"started_at":1})):
        lock.write_text(body)
        with pytest.raises(ProjectInUse):ProjectReservation(root)
        assert lock.read_text()==body


def test_control_secrets_removed_before_any_tool_child_and_status_never_contains_them(tmp_path,monkeypatch):
    monkeypatch.setenv("CRUCIBLE_OWNER_KEY","owner-test-secret");monkeypatch.setenv("CRUCIBLE_INDEX_KEY","index-test-secret");monkeypatch.setenv("CRUCIBLE_SHELL_POLICY","allow_all")
    owner=ProjectOwner();owner.initialize(tmp_path)
    assert "CRUCIBLE_OWNER_KEY" not in os.environ and "CRUCIBLE_INDEX_KEY" not in os.environ
    assert os.environ["CRUCIBLE_SHELL_POLICY"]=="ask"
    child=subprocess.check_output([sys.executable,"-c","import os;print(os.environ.get('CRUCIBLE_OWNER_KEY'),os.environ.get('CRUCIBLE_INDEX_KEY'))"],text=True)
    assert child.strip()=="None None"
    assert "owner-test-secret" not in repr(owner) and "index-test-secret" not in repr(owner)
    assert "owner-test-secret" not in json.dumps(owner.status(True)) and "index-test-secret" not in json.dumps(owner.status(True))


@pytest.mark.asyncio
async def test_real_auth_boundary_refuses_nonowner_every_mutation_and_indexer_is_not_owner(tmp_path,monkeypatch):
    monkeypatch.setattr(OWNER,"active",True);monkeypatch.setattr(OWNER,"root",str(tmp_path));monkeypatch.setattr(OWNER,"key","owner-secret");monkeypatch.setattr(OWNER,"index_key","index-secret")
    calls=[]
    async def downstream(scope,receive,send):
        calls.append(scope["path"]);await send({"type":"http.response.start","status":200,"headers":[]});await send({"type":"http.response.body","body":b"ok"})
    auth=AuthMiddleware(downstream,AuthState(port=8123,token=b"bearer"))
    async with AsyncClient(transport=ASGITransport(app=auth),base_url="http://127.0.0.1:8123",headers={"authorization":"Bearer bearer"}) as client:
        for method,path in [("POST","/v1/chat/threads/a/message"),("POST","/v1/chat/threads/a/command-decision"),("POST","/v1/tasks/a/accept"),("PUT","/v1/config"),("PUT","/v1/project/trust"),("DELETE","/v1/agents/a")]:
            assert (await client.request(method,path,json={})).status_code==409
        assert not calls
        assert (await client.get("/v1/config")).status_code==200
        assert (await client.post("/v1/index/build",headers={"x-crucible-indexer":"index-secret"},json={})).status_code==200
        assert (await client.put("/v1/config",headers={"x-crucible-indexer":"index-secret"},json={})).status_code==409
        assert (await client.put("/v1/config",headers={"x-crucible-owner":"owner-secret"},json={})).status_code==200
        assert (await client.put("/v1/config",headers=[("x-crucible-owner","owner-secret"),("x-crucible-owner","owner-secret")],json={})).status_code==409
        assert (await client.put("/v1/config",headers={"x-crucible-owner":"owner-secret","origin":"http://evil"},json={})).status_code==403


@pytest.mark.asyncio
async def test_actual_controller_trust_never_promotes_full_access_and_revoke_fences_policy(tmp_path,monkeypatch):
    ctrl,store,tid=_setup(tmp_path,monkeypatch,[],{})
    monkeypatch.setattr(OWNER,"active",True);monkeypatch.setattr(OWNER,"root",str((tmp_path/"ws").resolve()));monkeypatch.setattr(OWNER,"trusted",False)
    # Test uses the actual router/controller; middleware ownership denial is covered above.
    async with AsyncClient(transport=ASGITransport(app=app_for(ctrl)),base_url="http://test") as client:
        initial=ctrl.approval_state()
        full={"auto_accept":True,"shell_policy":"allow_all","resolve_waiting":False,"expected_process_id":initial.process_id,"expected_version":initial.version}
        assert (await client.put("/v1/chat/approval-mode",json=full)).status_code==409
        assert (await client.put("/v1/project/trust",json={"trusted":True,"process_id":"stale"})).status_code==409
        assert (await client.put("/v1/project/trust",json={"trusted":True,"process_id":OWNER.process_id})).status_code==200
        state=ctrl.approval_state();assert not state.auto_accept and state.shell_policy=="ask"
        full["expected_version"]=state.version
        assert (await client.put("/v1/chat/approval-mode",json=full)).status_code==200
        assert (await client.put("/v1/project/trust",json={"trusted":False,"process_id":OWNER.process_id})).status_code==200
        assert ctrl.approval_state().shell_policy=="ask" and not ctrl.approval_state().auto_accept
        assert (await client.put("/v1/chat/approval-mode",json=full)).status_code==409
        assert (await client.put(f"/v1/chat/threads/{tid}/title",json={"title":"Shared title"})).status_code==200
        assert store.get_thread(tid).title=="Shared title"
        foreign = store.create_thread(str(tmp_path/"other"), title="Foreign")
        assert (await client.put(f"/v1/chat/threads/{foreign.thread_id}/title", json={"title":"No"})).status_code==409
        assert (await client.post(f"/v1/chat/threads/{foreign.thread_id}/message", json={"message":"Do not start"})).status_code==409
        assert (await client.post("/v1/workspaces/env-profile", params={"workspace":str(tmp_path)})).status_code==409
        assert (await client.post("/v1/chat/threads",json={"workspace":"/outside"})).status_code==409


def test_actual_server_owner_observer_and_competing_start(tmp_path):
    from tests.test_serve import _start, _handshake, _get, _stop
    import urllib.request
    import urllib.error
    root = tmp_path / "ws"
    first, home, _ = _start(tmp_path, "--port", "0", "--workspace-lock", str(root), env_extra={"CRUCIBLE_REASONING_BACKEND":"ollama", "CRUCIBLE_LOCAL_MODELS":"0", "CRUCIBLE_OWNER_KEY":"test-control-key", "CRUCIBLE_OWNER_CLIENT":"first-client", "CRUCIBLE_CHAT_CONTROLLER":"1", "CRUCIBLE_TASK_SUBSYSTEM":"0"})
    try:
        port = _handshake(first)["port"]
        token = (home/".crucible/run"/f"agentd-{port}.token").read_text()
        assert _get(port, "/v1/config", token)[0] == 200
        def request(path, body=None, owner=False):
            headers={"Authorization":f"Bearer {token}"}
            if owner: headers["x-crucible-owner"]="test-control-key"
            data = None if body is None else json.dumps(body).encode()
            if data: headers["Content-Type"]="application/json"
            req=urllib.request.Request(f"http://127.0.0.1:{port}{path}",data=data,headers=headers)
            try:
                with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.loads(response.read())
            except urllib.error.HTTPError as exc:return exc.code,json.loads(exc.read())
        status, observer=request("/v1/project/session")
        assert status==200 and observer["role"]=="observer" and not observer["trusted"]
        status, owner=request("/v1/project/session",owner=True)
        assert status==200 and owner["role"]=="owner" and owner["pid"]==first.pid
        assert "test-control-key" not in json.dumps(owner)
        assert request("/v1/chat/threads",{"workspace":str(root)})[0]==409
        assert request("/v1/chat/threads",{"workspace":str(root)},owner=True)[0]==200
        lock=(root/".crucible/state/agentd.lock").read_bytes()
        second, _, _ = _start(tmp_path,"--port","0","--workspace-lock",str(root))
        assert second.wait(10)==3
        assert "already controlled" in second.stderr.read()
        assert (root/".crucible/state/agentd.lock").read_bytes()==lock
        assert first.poll() is None and request("/v1/project/session",owner=True)[0]==200
    finally:
        assert _stop(first)==0


from tests.test_approval_mode import write
@pytest.mark.asyncio
async def test_trust_toggle_invalidates_stale_pre_toggle_fullaccess(tmp_path,monkeypatch):
 ctrl,store,tid=_setup(tmp_path,monkeypatch,[],{})
 monkeypatch.setattr(OWNER,'active',True);monkeypatch.setattr(OWNER,'root',str((tmp_path/'ws').resolve()));monkeypatch.setattr(OWNER,'trusted',True)
 write(ctrl,auto=False,shell='ask');before=ctrl.approval_state();pending={'auto_accept':True,'shell_policy':'allow_all','resolve_waiting':False,'expected_process_id':before.process_id,'expected_version':before.version}
 async with AsyncClient(transport=ASGITransport(app=app_for(ctrl)),base_url='http://test') as c:
  assert (await c.put('/v1/project/trust',json={'trusted':False,'process_id':OWNER.process_id})).status_code==200
  assert (await c.put('/v1/project/trust',json={'trusted':True,'process_id':OWNER.process_id})).status_code==200
  response=await c.put('/v1/chat/approval-mode',json=pending)
  assert response.status_code==409, f'stale Full access accepted: {response.json()}'
  assert ctrl.approval_state().shell_policy=='ask'

@pytest.mark.asyncio
async def test_inline_manifest_cannot_promote_to_another_project(tmp_path,monkeypatch):
 ctrl,store,tid=_setup(tmp_path,monkeypatch,[],{})
 root=(tmp_path/'ws').resolve();foreign=tmp_path/'foreign';foreign.mkdir();(foreign/'kept.txt').write_text('original foreign bytes')
 monkeypatch.setattr(OWNER,'active',True);monkeypatch.setattr(OWNER,'root',str(root));monkeypatch.setattr(OWNER,'trusted',False)
 orch=ctrl._orchestrator;shadow=orch._workspace_manager._resolve_shadow_path('inline-foreign');shadow.mkdir(parents=True);(shadow/'kept.txt').write_text('would overwrite foreign')
 (shadow/'.inline-manifest.json').write_text(json.dumps({'workspace_path':str(foreign),'touched_files':['kept.txt']}))
 async with AsyncClient(transport=ASGITransport(app=app_for(ctrl)),base_url='http://test') as c:
  response=await c.post('/v1/chat/inline-changes/inline-foreign/promote')
  assert response.status_code==409,f'foreign promotion accepted: {response.status_code} {response.text}'
  assert (foreign/'kept.txt').read_text()=='original foreign bytes'


def test_live_legacy_backend_refuses_before_migrating_or_creating_database(tmp_path):
    legacy = tmp_path / ".agentd"
    legacy.mkdir()
    lock = legacy / "agentd.lock"
    lock.write_text(json.dumps({"pid":os.getpid(),"port":12345,"started_at":1}))
    with pytest.raises(ProjectInUse):
        ProjectReservation(tmp_path)
    assert lock.exists() and not list(tmp_path.rglob("*.sqlite3"))


@pytest.mark.asyncio
async def test_owned_inline_manifest_valid_path_and_foreign_discard_refusal(tmp_path,monkeypatch):
    ctrl,store,tid=_setup(tmp_path,monkeypatch,[],{})
    root=(tmp_path/"ws").resolve()
    monkeypatch.setattr(OWNER,"active",True);monkeypatch.setattr(OWNER,"root",str(root))
    orch=ctrl._orchestrator
    def manifest(identity,workspace,relative):
        shadow=orch._workspace_manager._resolve_shadow_path(identity);shadow.mkdir(parents=True)
        (shadow/"kept.txt").write_text("updated")
        (shadow/".inline-manifest.json").write_text(json.dumps({"workspace_path":str(workspace),"touched_files":[relative]}))
        return shadow
    good=manifest("inline-good",root,"kept.txt")
    await orch.promote_inline_change("inline-good")
    assert (root/"kept.txt").read_text()=="updated"
    foreign=manifest("inline-foreign-discard",tmp_path/"foreign","kept.txt")
    async with AsyncClient(transport=ASGITransport(app=app_for(ctrl)),base_url="http://test") as client:
        assert (await client.delete("/v1/chat/inline-changes/inline-foreign-discard")).status_code==409
        assert foreign.exists()
        unsafe=manifest("inline-unsafe",root,"../outside.txt")
        assert (await client.post("/v1/chat/inline-changes/inline-unsafe/promote")).status_code==409
        assert unsafe.exists()
