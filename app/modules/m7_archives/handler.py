from fastapi import APIRouter, Body, Request

from . import service

router = APIRouter(tags=["M7-档案合库"])


@router.get("/archives")
def page(request: Request):
    data = service.page_data(request.app.state.archives)
    return request.app.state.templates.TemplateResponse(
        request, "m7_archives.html", {"title": "档案合库", "data": data})


@router.post("/api/archives/ingest")
def ingest(request: Request, payload: dict = Body(default={})):
    return service.ingest(request.app.state.archives, payload)


@router.get("/api/archives/checkpoint")
def checkpoint(request: Request, stream: str | None = None):
    return service.checkpoint(request.app.state.archives, stream)


@router.get("/api/archives/vouchers/{source_org}/{batch_no}/{voucher_no}")
def voucher(request: Request, source_org: str, batch_no: str, voucher_no: str):
    return service.voucher(request.app.state.archives, source_org, batch_no, voucher_no)


@router.get("/api/archives/attributions/{key}")
def attribution(request: Request, key: str):
    return service.attribution(request.app.state.archives, key)


@router.get("/api/archives/disputes")
def disputes(request: Request, status: str | None = None):
    return service.disputes(request.app.state.archives, status)


@router.post("/api/archives/adjudications")
def adjudicate(request: Request, payload: dict = Body(default={})):
    return service.adjudicate(request.app.state.archives, payload)


@router.post("/api/archives/objections")
def object_(request: Request, payload: dict = Body(default={})):
    return service.raise_objection(request.app.state.archives, payload)


@router.get("/api/archives/summary")
def summary(request: Request):
    return service.summary(request.app.state.archives)
