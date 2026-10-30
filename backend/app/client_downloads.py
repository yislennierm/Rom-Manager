"""Client-owned download operations, independent of any device transport/UI."""
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.client_downloads import DownloadError


@dataclass
class DownloadTarget:
    store: object
    system_guids: dict[str, str]
    prepare_entry: Callable
    entry_key: Callable
    preparer: object = None


class ClientQueueRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    target: str = Field(min_length=1, max_length=80)
    dataset: str = Field(min_length=1, max_length=200)
    entry_key: str = Field(pattern=r'^[0-9a-f]{64}$')


class ClientPreparedReceipt(BaseModel):
    model_config = {'extra': 'forbid'}
    lease: str = Field(min_length=1, max_length=100)
    filename: str = Field(min_length=1, max_length=200)
    size: int = Field(gt=0, le=64 * 1024 * 1024)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def invoke(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except DownloadError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


def prepare_catalog_job(dataset_id, key, load_dataset, target, authorize_guid):
    dataset = load_dataset(dataset_id)
    if not dataset:
        raise HTTPException(404, 'ROM dataset not found')
    module = dataset.get('module')
    guid = dataset.get('guid') or (module.get('guid') if isinstance(module, dict) else None)
    if not guid or str(guid).lower() not in {g.lower() for g in target.system_guids.values()} or not authorize_guid(guid):
        raise HTTPException(403, 'Console is not assigned to this client and download target')
    entries = dataset.get('entries') or dataset.get('roms') or []
    entry = next((e for e in entries if isinstance(e, dict) and target.entry_key(e) == key), None)
    if entry is None:
        raise HTTPException(404, 'ROM entry changed or no longer exists; refresh the catalog')
    return invoke(target.prepare_entry, entry, guid)


def make_client_download_router(current_user, guid_allowed, load_dataset, targets):
    router = APIRouter(prefix='/client/downloads', tags=['Client downloads'])

    def api_client(user: dict = Depends(current_user)):
        if user.get('admin') or user.get('_auth_method') != 'api_key' or not user.get('enabled', True):
            raise HTTPException(403, 'A non-admin client API key is required')
        return user

    def allowed_systems(target, user):
        return {system for system, guid in target.system_guids.items() if guid_allowed(guid, user)}

    def resolve_target(target_id, user):
        target = targets.get(target_id)
        if target is None:
            raise HTTPException(404, 'Download target not found')
        if target.store.bound_account_id() != user.get('id') or not allowed_systems(target, user):
            raise HTTPException(403, 'Download target is not assigned to this client or its consoles')
        return target

    @router.get('/targets')
    def list_targets(user: dict = Depends(api_client)):
        return {'targets': [{'id': target_id, 'system_guids': {
            system: guid for system, guid in target.system_guids.items() if guid_allowed(guid, user)
        }} for target_id, target in targets.items()
            if target.store.bound_account_id() == user.get('id') and allowed_systems(target, user)]}

    @router.get('')
    def list_jobs(target: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                  user: dict = Depends(api_client)):
        adapter = resolve_target(target, user)
        return {'target': target, **adapter.store.list_jobs(limit, offset, allowed_systems(adapter, user))}

    @router.post('')
    def enqueue(request: ClientQueueRequest, user: dict = Depends(api_client)):
        adapter = resolve_target(request.target, user)
        plan = prepare_catalog_job(request.dataset, request.entry_key, load_dataset, adapter,
                                   lambda guid: guid_allowed(guid, user))
        return {'job': invoke(adapter.store.enqueue, plan)}

    @router.post('/{job_id}/retry')
    def retry(job_id: str, target: str, user: dict = Depends(api_client)):
        adapter = resolve_target(target, user)
        job = adapter.store.get_job(job_id)
        if not job or job.get('system') not in allowed_systems(adapter, user):
            raise HTTPException(404, 'Download job not found')
        invoke(adapter.store.retry, job_id)
        if adapter.preparer:
            adapter.preparer.retry(adapter.store.get_plan(job_id))
        return {'queued': True}

    def owned_job(job_id, target, user):
        adapter = resolve_target(target, user)
        job = adapter.store.get_job(job_id)
        if not job or job.get('system') not in allowed_systems(adapter, user):
            raise HTTPException(404, 'Download job not found')
        return adapter, job

    @router.get('/{job_id}/recipe')
    def recipe(job_id: str, target: str, user: dict = Depends(api_client)):
        adapter, _ = owned_job(job_id, target, user)
        return {'plan': adapter.store.get_plan(job_id)}

    @router.post('/{job_id}/claim')
    def claim_job(job_id: str, target: str, user: dict = Depends(api_client)):
        adapter, _ = owned_job(job_id, target, user)
        return {'job': adapter.store.claim(allowed_systems(adapter, user), job_id=job_id)}

    @router.post('/{job_id}/prepared')
    def prepared_receipt(job_id: str, target: str, request: ClientPreparedReceipt, user: dict = Depends(api_client)):
        adapter, _ = owned_job(job_id, target, user)
        content = {'filename': request.filename, 'size': request.size, 'checksum': {'algorithm': 'sha256', 'value': request.sha256}}
        invoke(adapter.store.materialize, job_id, content, lease=request.lease)
        return {'job': adapter.store.get_job(job_id)}

    def resolve_content(job_id, target, user):
        adapter = resolve_target(target, user)
        job = adapter.store.get_job(job_id)
        if not job or job.get('system') not in allowed_systems(adapter, user) or not adapter.preparer:
            raise HTTPException(404, 'Prepared download not found')
        plan = adapter.store.get_plan(job_id)
        if not plan or not plan.get('_preparation'):
            raise HTTPException(404, 'This job does not require backend preparation')
        status = adapter.preparer.status(plan)
        if status['state'] == 'ready' and status.get('content'):
            invoke(adapter.store.materialize, job_id, status['content'])
        return adapter, status

    @router.get('/{job_id}/preparation')
    def preparation(job_id: str, target: str, user: dict = Depends(api_client)):
        adapter, status = resolve_content(job_id, target, user)
        if status['state'] == 'ready':
            return {'state': 'ready', 'job': adapter.store.get_job(job_id, source=True)}
        return {k: status[k] for k in ('state', 'downloaded', 'total', 'error') if k in status}

    @router.get('/{job_id}/content')
    def content(job_id: str, target: str, user: dict = Depends(api_client)):
        _, status = resolve_content(job_id, target, user)
        if status['state'] == 'ready':
            return FileResponse(status['path'], media_type='application/octet-stream',
                                headers={'ETag': status['etag'], 'Cache-Control': 'private, no-store'})
        if status['state'] == 'failed':
            return JSONResponse({'state': 'failed', 'error': status['error']}, status_code=502)
        return JSONResponse({k: status[k] for k in ('state', 'downloaded', 'total')},
                            status_code=202, headers={'Retry-After': '1', 'Cache-Control': 'no-store'})

    return router
