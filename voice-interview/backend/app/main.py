import asyncio
import csv
import io
import json
import uuid
import weakref
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import anyio
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config import settings
from .db import open_pool
from .metrics import summarize
from .model import ModelError, build_messages, followup_prefix, guard_followup_stream, stream_question
from .security import new_session_token, token_hash, verify_password


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await open_pool()
    yield
    await app.state.pool.close()


app = FastAPI(title="语音模拟面试 API", lifespan=lifespan)
generation_locks = weakref.WeakValueDictionary()


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class InterviewInput(BaseModel):
    role: str = Field(min_length=2, max_length=100)
    job_description: str = Field(min_length=30, max_length=10000)
    experience_summary: str = Field(min_length=30, max_length=10000)


class AnswerInput(BaseModel):
    turn: int = Field(ge=1, le=3)
    text: str = Field(min_length=1, max_length=10000)


class BrowserMetricInput(BaseModel):
    request_id: uuid.UUID
    first_char_ms: float | None = Field(default=None, ge=0, le=120000, allow_inf_nan=False)
    speech_start_ms: float | None = Field(default=None, ge=0, le=120000, allow_inf_nan=False)
    browser_error: str | None = Field(default=None, max_length=500)
    browser_info: str | None = Field(default=None, max_length=200)


def db_pool(request: Request):
    return request.app.state.pool


async def current_user(request: Request) -> dict:
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(status_code=401, detail="请先登录。")
    async with db_pool(request).acquire() as conn:
        row = await conn.fetchrow(
            """SELECT u.id, u.username FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = $1 AND s.expires_at > now()""",
            token_hash(token),
        )
    if row is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录。")
    return dict(row)


async def owned_interview(conn, interview_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    row = await conn.fetchrow(
        "SELECT * FROM interviews WHERE id = $1 AND user_id = $2", interview_id, user_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="找不到这场面试。")
    return dict(row)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.get("/api/health")
async def health(request: Request):
    async with db_pool(request).acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"ok": True}


@app.post("/api/login")
async def login(body: LoginInput, response: Response, request: Request):
    async with db_pool(request).acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, password_hash FROM users WHERE username = $1",
            body.username.strip(),
        )
        if row is None or not await run_in_threadpool(verify_password, body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="账号或密码错误。")
        token = new_session_token()
        await conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ($1, $2, $3)",
            token_hash(token), row["id"], datetime.now(timezone.utc) + timedelta(days=7),
        )
    response.set_cookie(
        "session", token, httponly=True, secure=settings.cookie_secure,
        samesite="lax", max_age=7 * 24 * 3600, path="/",
    )
    return {"id": row["id"], "username": row["username"]}


@app.post("/api/logout")
async def logout(response: Response, request: Request):
    token = request.cookies.get("session")
    if token:
        async with db_pool(request).acquire() as conn:
            await conn.execute("DELETE FROM sessions WHERE token_hash = $1", token_hash(token))
    response.delete_cookie("session", path="/")
    return {"ok": True}


@app.get("/api/me")
async def me(user: dict = Depends(current_user)):
    return user


@app.post("/api/interviews", status_code=201)
async def create_interview(body: InterviewInput, request: Request, user: dict = Depends(current_user)):
    role = body.role.strip()
    job_description = body.job_description.strip()
    experience_summary = body.experience_summary.strip()
    if len(role) < 2 or len(job_description) < 30 or len(experience_summary) < 30:
        raise HTTPException(status_code=422, detail="请填写完整的岗位、岗位描述和经历摘要。")
    interview_id = uuid.uuid4()
    async with db_pool(request).acquire() as conn:
        await conn.execute(
            """INSERT INTO interviews (id, user_id, role, job_description, experience_summary)
               VALUES ($1, $2, $3, $4, $5)""",
            interview_id, user["id"], role, job_description, experience_summary,
        )
        return await owned_interview(conn, interview_id, user["id"])


@app.get("/api/interviews")
async def list_interviews(request: Request, user: dict = Depends(current_user)):
    async with db_pool(request).acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, role, state, current_turn, created_at, updated_at
               FROM interviews WHERE user_id = $1 ORDER BY created_at DESC""",
            user["id"],
        )
    return [dict(row) for row in rows]


@app.get("/api/interviews/{interview_id}")
async def get_interview(
    interview_id: uuid.UUID, request: Request, user: dict = Depends(current_user)
):
    async with db_pool(request).acquire() as conn:
        interview = await owned_interview(conn, interview_id, user["id"])
        rounds = await conn.fetch(
            """SELECT turn, question_state, question_text, answer_text, generated_at, answered_at
               FROM rounds WHERE interview_id = $1 ORDER BY turn""",
            interview_id,
        )
    return {"interview": interview, "rounds": [dict(row) for row in rounds]}


async def mark_failed(pool, interview_id: uuid.UUID, request_id: uuid.UUID, reason: str) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """DELETE FROM rounds WHERE interview_id = $1 AND request_id = $2
                   AND question_state = 'generating'""",
                interview_id, request_id,
            )
            await conn.execute(
                """UPDATE question_requests SET status = 'failure', failure_reason = $2,
                   finished_at = now() WHERE id = $1 AND status = 'running'""",
                request_id, reason[:500],
            )


@app.get("/api/interviews/{interview_id}/question/stream")
async def question_stream(
    interview_id: uuid.UUID, request: Request, turn: int = Query(ge=1, le=3),
    user: dict = Depends(current_user),
):
    pool = db_pool(request)
    async with pool.acquire() as conn:
        await owned_interview(conn, interview_id, user["id"])
    lock = generation_locks.setdefault(interview_id, asyncio.Lock())
    expected_turn = turn

    async def events():
        request_id = None
        finished = False
        failure_reason = "生成失败或连接中断"
        async with lock:
            try:
                async with pool.acquire() as conn:
                    interview = await owned_interview(conn, interview_id, user["id"])
                    if interview["state"] == "complete":
                        yield sse("error", {"message": "这场面试已完成。"})
                        return
                    turn = interview["current_turn"]
                    if turn != expected_turn:
                        yield sse("error", {"message": "面试轮次已变化，请刷新后继续。"})
                        return
                    existing = await conn.fetchrow(
                        "SELECT * FROM rounds WHERE interview_id = $1 AND turn = $2",
                        interview_id, turn,
                    )
                    if existing and existing["question_state"] == "ready":
                        yield sse("existing", {
                            "turn": turn, "question": existing["question_text"],
                            "requestId": str(existing["request_id"]),
                        })
                        return
                    previous = await conn.fetch(
                        """SELECT turn, question_text, answer_text FROM rounds
                           WHERE interview_id = $1 AND turn < $2 AND answer_text IS NOT NULL
                           ORDER BY turn""",
                        interview_id, turn,
                    )
                    if turn > 1 and len(previous) != turn - 1:
                        yield sse("error", {"message": "上一轮回答尚未确认，无法生成下一题。"})
                        return
                    if not settings.model_api_key or not settings.model_name:
                        yield sse("error", {
                            "message": "尚未配置 MODEL_API_KEY 或 MODEL_NAME；请在服务端环境变量中设置。"
                        })
                        return
                    messages = build_messages(interview, [dict(row) for row in previous], turn)
                    request_id = uuid.uuid4()
                    async with conn.transaction():
                        if existing:
                            await conn.execute(
                                """UPDATE question_requests SET status = 'failure',
                                   failure_reason = '旧的生成已中断', finished_at = now()
                                   WHERE id = $1 AND status = 'running'""",
                                existing["request_id"],
                            )
                            await conn.execute(
                                "DELETE FROM rounds WHERE interview_id = $1 AND turn = $2",
                                interview_id, turn,
                            )
                        cold_start = await conn.fetchval(
                            "SELECT NOT EXISTS (SELECT 1 FROM question_requests WHERE user_id = $1)",
                            user["id"],
                        )
                        is_retry = await conn.fetchval(
                            """SELECT EXISTS (SELECT 1 FROM question_requests
                               WHERE interview_id = $1 AND turn = $2)""",
                            interview_id, turn,
                        )
                        await conn.execute(
                            """INSERT INTO question_requests
                               (id, user_id, interview_id, turn, status, model_name,
                                is_cold_start, is_retry)
                               VALUES ($1, $2, $3, $4, 'running', $5, $6, $7)""",
                            request_id, user["id"], interview_id, turn,
                            settings.model_name, cold_start, is_retry,
                        )
                        await conn.execute(
                            """INSERT INTO rounds
                               (interview_id, turn, request_id, question_state)
                               VALUES ($1, $2, $3, 'generating')""",
                            interview_id, turn, request_id,
                        )
                yield sse("meta", {
                    "turn": turn, "requestId": str(request_id), "isFollowUp": turn == 2,
                })
                full = ""
                ttft_ms = None
                prefix = followup_prefix(previous[-1]["answer_text"]) if turn == 2 else None
                async for delta, first_ttft in guard_followup_stream(stream_question(messages), prefix):
                    full += delta
                    if first_ttft is not None:
                        ttft_ms = first_ttft
                    yield sse("delta", {"text": delta})
                if not full.strip():
                    raise ModelError("模型未返回可展示的问题正文，请重试。")
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            """UPDATE rounds SET question_state = 'ready', question_text = $3,
                               generated_at = now()
                               WHERE interview_id = $1 AND turn = $2 AND request_id = $4""",
                            interview_id, turn, full.strip(), request_id,
                        )
                        await conn.execute(
                            """UPDATE question_requests SET status = 'success',
                               model_ttft_ms = $2, finished_at = now() WHERE id = $1""",
                            request_id, ttft_ms,
                        )
                        await conn.execute(
                            "UPDATE interviews SET updated_at = now() WHERE id = $1",
                            interview_id,
                        )
                finished = True
                yield sse("done", {
                    "turn": turn, "question": full.strip(),
                    "requestId": str(request_id), "modelTtftMs": ttft_ms,
                })
            except ModelError as exc:
                failure_reason = str(exc)
                yield sse("error", {"message": str(exc), "requestId": str(request_id)})
            except asyncio.CancelledError:
                raise
            except Exception:
                yield sse("error", {
                    "message": "生成问题时出现服务错误；可重试，已完成的问答不会丢失。",
                    "requestId": str(request_id),
                })
            finally:
                if request_id and not finished:
                    with anyio.CancelScope(shield=True):
                        await mark_failed(pool, interview_id, request_id, failure_reason)

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/api/interviews/{interview_id}/answers")
async def save_answer(
    interview_id: uuid.UUID, body: AnswerInput, request: Request,
    user: dict = Depends(current_user),
):
    answer = body.text.strip()
    if not answer:
        raise HTTPException(status_code=422, detail="请先确认回答文字。")
    async with db_pool(request).acquire() as conn:
        async with conn.transaction():
            interview = await conn.fetchrow(
                """SELECT * FROM interviews WHERE id = $1 AND user_id = $2 FOR UPDATE""",
                interview_id, user["id"],
            )
            if interview is None:
                raise HTTPException(status_code=404, detail="找不到这场面试。")
            round_row = await conn.fetchrow(
                """SELECT question_state, answer_text FROM rounds
                   WHERE interview_id = $1 AND turn = $2""",
                interview_id, body.turn,
            )
            if round_row and round_row["answer_text"] is not None:
                if round_row["answer_text"] != answer:
                    raise HTTPException(status_code=409, detail="这一轮已确认过另一份回答。")
                return {"saved": True, "alreadySaved": True,
                        "currentTurn": interview["current_turn"], "state": interview["state"]}
            if interview["state"] != "active" or interview["current_turn"] != body.turn:
                raise HTTPException(status_code=409, detail="轮次已变化，请刷新后继续。")
            if round_row is None or round_row["question_state"] != "ready":
                raise HTTPException(status_code=409, detail="当前问题尚未生成完成。")
            await conn.execute(
                """UPDATE rounds SET answer_text = $3, answered_at = now()
                   WHERE interview_id = $1 AND turn = $2""",
                interview_id, body.turn, answer,
            )
            new_state = "complete" if body.turn == 3 else "active"
            await conn.execute(
                """UPDATE interviews SET current_turn = $2, state = $3,
                   updated_at = now() WHERE id = $1""",
                interview_id, body.turn + 1, new_state,
            )
    return {"saved": True, "alreadySaved": False,
            "currentTurn": body.turn + 1, "state": new_state}


@app.post("/api/performance/browser")
async def save_browser_metric(
    body: BrowserMetricInput, request: Request, user: dict = Depends(current_user)
):
    async with db_pool(request).acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE question_requests SET
                 first_char_ms = COALESCE(first_char_ms, $3),
                 speech_start_ms = COALESCE(speech_start_ms, $4),
                 browser_error = COALESCE($5, browser_error),
                 browser_info = COALESCE(browser_info, $6)
               WHERE id = $1 AND user_id = $2 RETURNING id""",
            body.request_id, user["id"], body.first_char_ms,
            body.speech_start_ms, body.browser_error, body.browser_info,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="找不到这次提问记录。")
    return {"saved": True}


@app.get("/api/performance")
async def performance(request: Request, user: dict = Depends(current_user)):
    async with db_pool(request).acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM question_requests WHERE user_id = $1 ORDER BY started_at",
            user["id"],
        )
    return summarize([dict(row) for row in rows])


@app.get("/api/performance/export.csv")
async def performance_csv(request: Request, user: dict = Depends(current_user)):
    async with db_pool(request).acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, interview_id, turn, status, model_name, is_cold_start, is_retry,
                      model_ttft_ms, first_char_ms, speech_start_ms, browser_error,
                      browser_info, failure_reason, started_at, finished_at
               FROM question_requests WHERE user_id = $1 ORDER BY started_at""",
            user["id"],
        )
    fields = [
        "id", "interview_id", "turn", "status", "model_name", "is_cold_start", "is_retry",
        "model_ttft_ms", "first_char_ms", "speech_start_ms", "browser_error",
        "browser_info", "failure_reason", "started_at", "finished_at",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row[key] if row[key] is not None else "" for key in fields})
    return Response(
        content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="interview-performance.csv"'},
    )
