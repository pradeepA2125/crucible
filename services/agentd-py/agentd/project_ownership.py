"""Lifetime canonical-project reservation and independent host control capability.

Reservation inode is never removed: OS unlock on exit is the only recovery. The
bearer grants inspection; only the spawning host holds the mutation capability.
"""
from __future__ import annotations
import hmac
import json
import os
import secrets
import socket
import stat
from dataclasses import dataclass, field
from pathlib import Path

PROTOCOL = 1

class ProjectInUse(RuntimeError):
    pass

class ProjectReservation:
    def __init__(self, workspace: str | Path):
        self.root = Path(workspace).resolve(strict=True)
        self.fd: int | None = None
        if not self.root.is_dir():
            raise ProjectInUse("Choose an existing project directory.")
        for legacy in (".ai-editor",".agentd"):
            if (self.root/legacy).is_symlink():raise ProjectInUse("Linked legacy state cannot be migrated safely.")
        parent=self.root
        for part in (".crucible", "state"):
            parent=parent/part
            try: parent.mkdir(mode=0o700)
            except FileExistsError: pass
            info=parent.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or parent.resolve()!=parent:
                raise ProjectInUse(f"Project {self.root}: internal state path is linked or unavailable.")
        flags=os.O_RDWR|os.O_CREAT|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_CLOEXEC",0)
        try:
            fd=os.open(parent/"agentd.owner",flags,0o600)
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or (hasattr(os,"getuid") and info.st_uid!=os.getuid()):
                os.close(fd); raise ProjectInUse("Project reservation has unexpected ownership.")
            self.fd=fd
            if os.name=="nt":
                import msvcrt
                if info.st_size==0: os.write(fd,b"0")
                os.lseek(fd,0,os.SEEK_SET); msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            # Defeat replacement between open and lock. Never unlink this inode.
            named=(parent/"agentd.owner").lstat()
            if named.st_ino!=info.st_ino or named.st_dev!=info.st_dev or named.st_nlink!=1:
                raise ProjectInUse("Project reservation changed. Retry after checking the project.")
            for lock in (parent/"agentd.lock", self.root/".agentd/agentd.lock", self.root/".ai-editor/state/agentd.lock", self.root/".ai-editor/agentd.lock"):
                self._legacy(lock)
        except (OSError,ProjectInUse) as exc:
            self.close()
            raise ProjectInUse(f"Project {self.root} is already controlled or its ownership is uncertain. Close its current client, then retry; do not delete a live lock.") from exc
    def _legacy(self,path: Path):
        try:
            fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0))
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>4096 or (hasattr(os,"getuid") and info.st_uid!=os.getuid()):raise ProjectInUse("Invalid backend lock.")
                data=os.read(fd,4097)
                if len(data)>4096:raise ProjectInUse("Backend lock grew beyond its limit.")
                raw=json.loads(data)
                if not isinstance(raw,dict):raise ProjectInUse("Invalid backend lock.")
            finally:os.close(fd)
        except FileNotFoundError: return
        except (OSError,ValueError): raise ProjectInUse("Unreadable backend lock.") from None
        pid=raw.get("pid");port=raw.get("port")
        if type(pid) is not int or pid<1 or type(port) is not int or not 1<=port<=65535: raise ProjectInUse("Invalid backend identity.")
        try: os.kill(pid,0)
        except ProcessLookupError: pass
        except (PermissionError,OverflowError,ValueError): raise ProjectInUse("Backend ownership uncertain.") from None
        else: raise ProjectInUse(f"Backend PID {pid} is still alive.")
        try:
            with socket.create_connection(("127.0.0.1",port),timeout=0.25): raise ProjectInUse("Previous backend port is still in use.")
        except (ConnectionRefusedError,TimeoutError): pass
    def close(self):
        if self.fd is not None:
            os.close(self.fd);self.fd=None

@dataclass
class ProjectOwner:
    active: bool = False
    root: str = ""
    client_id: str = ""
    client_name: str = "Manual backend"
    process_id: str = field(default_factory=lambda: secrets.token_hex(16))
    key: str = field(default="",repr=False)
    index_key: str = field(default="",repr=False)
    trusted: bool = False
    def initialize(self,root: Path):
        self.active=True;self.root=str(root);self.trusted=False
        self.key=os.environ.pop("CRUCIBLE_OWNER_KEY","") or secrets.token_hex(32)
        self.index_key=os.environ.pop("CRUCIBLE_INDEX_KEY","")
        self.client_id=os.environ.pop("CRUCIBLE_OWNER_CLIENT","")[:128]
        self.client_name=os.environ.pop("CRUCIBLE_OWNER_NAME","Manual backend")[:64]
        # Managed shell policy is explicit after every restart, never inherited.
        os.environ["CRUCIBLE_SHELL_POLICY"]="ask"
        os.environ["CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT"]="false"
    def owns(self,headers: list[tuple[bytes,bytes]]) -> bool:
        values=[v for k,v in headers if k==b"x-crucible-owner"]
        return len(values)==1 and bool(self.key) and hmac.compare_digest(values[0],self.key.encode())
    def indexer(self,scope) -> bool:
        values=[v for k,v in scope["headers"] if k==b"x-crucible-indexer"]
        return scope["method"]=="POST" and scope["path"]=="/v1/index/build" and len(values)==1 and bool(self.index_key) and hmac.compare_digest(values[0],self.index_key.encode())
    def status(self,owner=False):
        return {"protocol":PROTOCOL,"workspace":self.root,"process_id":self.process_id,"pid":os.getpid(),"client_id":self.client_id,"client_name":self.client_name,"role":"owner" if owner else "observer","trusted":self.trusted if owner else False}

OWNER=ProjectOwner()
