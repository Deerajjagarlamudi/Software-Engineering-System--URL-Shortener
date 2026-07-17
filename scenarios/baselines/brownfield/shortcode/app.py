from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

app = FastAPI()
links: dict[str, str] = {}
clicks: dict[str, int] = {}


class LinkIn(BaseModel):
    code: str
    target_url: str


@app.post("/links", status_code=201)
def create_link(body: LinkIn):
    links[body.code] = body.target_url
    clicks[body.code] = 0
    return body


@app.get("/{code}")
def resolve(code: str):
    if code not in links:
        raise HTTPException(404)
    clicks[code] += 1
    return RedirectResponse(links[code], status_code=307)


@app.get("/links/{code}/analytics")
def analytics(code: str):
    if code not in links:
        raise HTTPException(404)
    return {"code": code, "click_count": clicks[code]}


@app.delete("/links/{code}", status_code=204)
def delete(code: str):
    if code not in links:
        raise HTTPException(404)
    del links[code]
    del clicks[code]
