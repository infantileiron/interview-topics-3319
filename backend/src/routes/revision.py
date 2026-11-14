from fastapi import APIRouter,Depends,Query
from src.controllers import revision as controller, dataset as dataset_controller
from src.schemas.entities import RevisionBatchInput
from src.services.auth import current,editor
router=APIRouter(prefix="/revisions",tags=["revisions"])
@router.post("")
def submit(data:RevisionBatchInput,user=Depends(editor)):
 return controller.submit(data,user)
@router.get("")
def list_batches(family_id:str|None=Query(default=None),user=Depends(current)):
 return controller.list_batches(family_id)
@router.get("/{batch_id}")
def detail(batch_id:str,user=Depends(current)):
 return controller.detail(batch_id)
# 语料版本族：列出同一语料的全部历史版本（含修订发布的新版本）
versions_router=APIRouter(prefix="/datasets",tags=["datasets"])
@versions_router.get("/{family_id}/versions")
def dataset_versions(family_id:str,user=Depends(current)):
 from src.services.dataset import list_versions
 return list_versions(family_id)
