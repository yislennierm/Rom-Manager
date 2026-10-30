"""MiSTer jobs use the same client accounts and API keys as other clients."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from typing import Literal

from core.mister import MisterError, SYSTEM_GUIDS, entry_key, prepare_rom
from mister.demo import build_demo_rom, build_nes_demo_rom, build_demo_zip
from backend.app.client_downloads import DownloadTarget, prepare_catalog_job


class ProvisionRequest(BaseModel):
    name: str = Field(default="MiSTer", max_length=80)


class QueueRequest(BaseModel):
    dataset: str = Field(min_length=1, max_length=200)
    entry_key: str = Field(pattern=r"^[0-9a-f]{64}$")


class StorageStatus(BaseModel):
    games_root: str | None = Field(default=None, max_length=1024)
    free_bytes: int | None = Field(default=None, ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    core_present: bool
    error: str | None = Field(default=None, max_length=500)


class HeartbeatRequest(BaseModel):
    games_root: str = Field(max_length=1024)
    free_bytes: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    core_present: bool
    architecture: str = Field(max_length=100)
    storage_device: int
    system: Literal['snes', 'nes', 'genesis'] = 'snes'
    systems: dict[str, StorageStatus] = Field(default_factory=dict)


class ProgressRequest(BaseModel):
    lease: str = Field(min_length=1, max_length=100)
    state: str
    downloaded: int = Field(ge=0)
    error: str | None = Field(default=None, max_length=500)


def make_router(store, require_admin, load_dataset, current_user, get_account, provision_account, disable_account, preparer=None):
    router = APIRouter(prefix="/mister", tags=["MiSTer"])

    def invoke(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except MisterError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    def allowed_systems(user):
        allowed = {str(guid).lower() for guid in user.get('allowed_console_guids') or []}
        if user.get('admin') or not user.get('enabled', True):
            return set()
        return {system for system, guid in SYSTEM_GUIDS.items() if '*' in allowed or guid in allowed}

    def bound_account():
        account_id = store.bound_account_id()
        account = get_account(account_id) if account_id else None
        if not account or not allowed_systems(account) or not account.get('has_api_key'):
            raise HTTPException(403, 'Enable the MiSTer client account and assign a supported console in Users & access')
        return account

    def device_auth(user: dict = Depends(current_user)):
        account = bound_account()
        if user.get('id') != account['id'] or user.get('_auth_method') != 'api_key' or not allowed_systems(user):
            raise HTTPException(403, 'This account is not the assigned MiSTer client')
        return user

    def snapshot():
        state = store.snapshot()
        if preparer:
            for job in state['jobs']:
                progress = preparer.progress(store.get_plan(job['id']))
                if progress:
                    job['preparation'] = progress
        if state['device']:
            account = get_account(state['device']['account_id'])
            enabled = bool(account and account.get('has_api_key') and allowed_systems(account))
            state['device'].update(account=account, enabled=enabled,
                                   assigned_systems=sorted(allowed_systems(account)) if account else [],
                                   online=state['device']['online'] and enabled)
        return state

    @router.get("/admin")
    def status(_=Depends(require_admin)):
        return snapshot()

    @router.get("/demo.sfc")
    def demo():
        # An original, generated smoke-test ROM; never serves user files.
        return Response(build_demo_rom(), media_type="application/octet-stream")

    @router.get('/demo.nes')
    def nes_demo():
        return Response(build_nes_demo_rom(), media_type='application/octet-stream')

    @router.get('/demo.nes.zip')
    def nes_zip_demo():
        return Response(build_demo_zip('nes'), media_type='application/zip')

    @router.get('/demo.sfc.zip')
    def snes_zip_demo():
        return Response(build_demo_zip('snes'), media_type='application/zip')

    @router.post("/admin/device")
    def provision(request: ProvisionRequest, _=Depends(require_admin)):
        result = provision_account(request.name, store.bound_account_id())
        store.bind_account(result['user']['id'], request.name)
        # Keep the existing config field compatible; this is a normal client API key.
        return {"device_token": result['api_key'], "account": result['user'], "device": snapshot()["device"]}

    @router.delete("/admin/device/credential")
    def revoke(_=Depends(require_admin)):
        account_id = store.bound_account_id()
        if account_id:
            disable_account(account_id)
        return {"revoked": True}

    @router.post("/admin/jobs")
    def enqueue(request: QueueRequest, _=Depends(require_admin)):
        account = bound_account()
        target = DownloadTarget(store, SYSTEM_GUIDS, prepare_rom, entry_key)
        authorized = {SYSTEM_GUIDS[system] for system in allowed_systems(account)}
        plan = prepare_catalog_job(request.dataset, request.entry_key, load_dataset, target, lambda guid: str(guid).lower() in authorized)
        return {"job": invoke(store.enqueue, plan)}

    @router.post("/admin/jobs/{job_id}/retry")
    def retry(job_id: str, _=Depends(require_admin)):
        account = bound_account()
        job = store.get_job(job_id)
        if not job or job['system'] not in allowed_systems(account):
            raise HTTPException(404, 'Download job not found for assigned consoles')
        invoke(store.retry, job_id)
        if preparer:
            preparer.retry(store.get_plan(job_id))
        return {"queued": True}

    @router.post("/client/heartbeat")
    def heartbeat(request: HeartbeatRequest, user=Depends(device_auth)):
        telemetry = request.model_dump(exclude_none=True)
        telemetry['systems'] = {system: status for system, status in telemetry['systems'].items() if system in allowed_systems(user)}
        store.heartbeat(telemetry)
        return {"ok": True}

    @router.post("/client/claim")
    def claim(user=Depends(device_auth)):
        return {"job": store.claim(allowed_systems(user))}

    @router.post("/client/jobs/{job_id}/progress")
    def progress(job_id: str, request: ProgressRequest, user=Depends(device_auth)):
        job = store.get_job(job_id)
        if not job or job['system'] not in allowed_systems(user):
            raise HTTPException(404, 'Download job not found for assigned consoles')
        return {"job": invoke(store.report, job_id, request.lease, request.state, request.downloaded, request.error)}

    return router
