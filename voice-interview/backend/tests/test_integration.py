"""Real PostgreSQL/API tests; model fixtures are never performance evidence."""
import asyncio
import json
import os
import unittest
import uuid
from dataclasses import replace
from unittest.mock import patch

import httpx

from app import main
from app.config import settings
from app.model import ModelError


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "requires isolated test PostgreSQL")
class ApiDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if settings.pg_database != "interview_test":
            raise RuntimeError("Refusing test reset outside interview_test database")
        self.lifespan = main.lifespan(main.app)
        await self.lifespan.__aenter__()
        async with main.app.state.pool.acquire() as conn:
            await conn.execute("TRUNCATE interviews CASCADE")
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test.local",
        )
        self.calls = []
        self.fail_model = False
        self.false_quote = False

        async def fixture_question(messages):
            self.calls.append(messages)
            await asyncio.sleep(0.02)
            if self.fail_model:
                raise ModelError("test fixture upstream failure")
            if "当前是第2题" in messages[1]["content"]:
                if self.false_quote:
                    yield "你提到使用行级锁保护时段记录，请说明实现？", 15.0
                    return
                data = next(json.loads(line) for line in messages[1]["content"].splitlines()
                            if line.startswith('{"target_role":'))
                yield data["required_prefix"], 15.0
                yield "请补充一个具体的项目实例？", None
                return
            yield "测试专用问题：", 15.0
            yield "请说明上次回答中提到的具体取舍？", None

        self.stream_patch = patch("app.main.stream_question", fixture_question)
        self.settings_patch = patch("app.main.settings", replace(main.settings, model_api_key="fixture"))
        self.stream_patch.start()
        self.settings_patch.start()
        await self.login("demo-alice", settings.demo_password_1)

    async def asyncTearDown(self):
        self.stream_patch.stop()
        self.settings_patch.stop()
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def login(self, username, password):
        result = await self.client.post("/api/login", json={"username": username, "password": password})
        self.assertEqual(result.status_code, 200)

    async def create(self):
        result = await self.client.post("/api/interviews", json={
            "role": "测试开发", "job_description": "接口权限与事务测试" * 10,
            "experience_summary": "虚构预约项目，负责行锁和并发测试" * 10,
        })
        self.assertEqual(result.status_code, 201)
        return result.json()["id"]

    async def generate(self, interview_id, turn):
        response = await self.client.get(f"/api/interviews/{interview_id}/question/stream?turn={turn}")
        self.assertEqual(response.status_code, 200)
        return response

    async def answer(self, interview_id, turn, text="用户校对后的行锁与取舍细节"):
        return await self.client.post(f"/api/interviews/{interview_id}/answers", json={"turn": turn, "text": text})

    async def test_three_rounds_followup_refresh_and_idempotence(self):
        interview_id = await self.create()
        for turn in range(1, 4):
            response = await self.generate(interview_id, turn)
            self.assertIn("event: done", response.text)
            recovered = (await self.client.get(f"/api/interviews/{interview_id}")).json()
            self.assertEqual(recovered["interview"]["current_turn"], turn)
            self.assertEqual(len(recovered["rounds"]), turn)
            saved, duplicate = await asyncio.gather(
                self.answer(interview_id, turn), self.answer(interview_id, turn),
            )
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(duplicate.status_code, 200)
            self.assertEqual(sum(r.json()["alreadySaved"] for r in (saved, duplicate)), 1)
            conflict = await self.answer(interview_id, turn, "另一份不同回答")
            self.assertEqual(conflict.status_code, 409)
        self.assertIn("用户校对后的行锁与取舍细节", self.calls[1][1]["content"])
        final = (await self.client.get(f"/api/interviews/{interview_id}")).json()
        self.assertEqual(final["interview"]["state"], "complete")
        self.assertEqual(final["interview"]["current_turn"], 4)
        self.assertTrue(all(row["answer_text"] for row in final["rounds"]))
        await self.generate(interview_id, 3)
        self.assertEqual(len(self.calls), 3)

    async def test_cross_account_denial_on_all_interview_routes(self):
        interview_id = await self.create()
        generated = await self.generate(interview_id, 1)
        meta = next(json.loads(line[6:]) for line in generated.text.splitlines() if line.startswith("data:"))
        await self.login("demo-bob", settings.demo_password_2)
        self.assertEqual((await self.client.get("/api/interviews")).json(), [])
        paths = [f"/api/interviews/{interview_id}", f"/api/interviews/{interview_id}/question/stream?turn=1"]
        for path in paths:
            self.assertEqual((await self.client.get(path)).status_code, 404)
        self.assertEqual((await self.answer(interview_id, 1)).status_code, 404)
        metric = await self.client.post("/api/performance/browser", json={"request_id": meta["requestId"], "first_char_ms": 12})
        self.assertEqual(metric.status_code, 404)
        self.assertEqual((await self.client.get("/api/performance")).json()["totalFinished"], 0)
        self.assertEqual(len(self.calls), 1)

    async def test_failure_retry_keeps_confirmed_rounds(self):
        interview_id = await self.create()
        await self.generate(interview_id, 1)
        await self.answer(interview_id, 1)
        self.fail_model = True
        response = await self.generate(interview_id, 2)
        self.assertIn("event: error", response.text)
        recovered = (await self.client.get(f"/api/interviews/{interview_id}")).json()
        self.assertEqual(len(recovered["rounds"]), 1)
        self.assertIsNotNone(recovered["rounds"][0]["answer_text"])
        self.fail_model = False
        await self.generate(interview_id, 2)
        metrics = (await self.client.get("/api/performance")).json()
        self.assertEqual((metrics["success"], metrics["failure"], metrics["retries"]), (2, 1, 1))
        self.assertFalse(metrics["thresholdsPassed"])

    async def test_false_followup_quote_is_not_saved_or_exposed_and_retry_is_recorded(self):
        interview_id = await self.create()
        await self.generate(interview_id, 1)
        await self.answer(interview_id, 1, "可以听见吗？")
        self.false_quote = True
        response = await self.generate(interview_id, 2)
        self.assertIn("event: error", response.text)
        self.assertNotIn("event: delta", response.text)
        recovered = (await self.client.get(f"/api/interviews/{interview_id}")).json()
        self.assertEqual(len(recovered["rounds"]), 1)
        self.assertEqual(recovered["rounds"][0]["answer_text"], "可以听见吗？")
        self.false_quote = False
        retry = await self.generate(interview_id, 2)
        self.assertIn("event: done", retry.text)
        recovered = (await self.client.get(f"/api/interviews/{interview_id}")).json()
        self.assertIn("可以听见吗？", recovered["rounds"][1]["question_text"])
        self.assertNotIn("行级锁", recovered["rounds"][1]["question_text"])
        metrics = (await self.client.get("/api/performance")).json()
        self.assertEqual((metrics["success"], metrics["failure"], metrics["retries"]), (2, 1, 1))

    async def test_duplicate_generation_calls_model_once_and_stale_turn_rejected(self):
        interview_id = await self.create()
        left, right = await asyncio.gather(self.generate(interview_id, 1), self.generate(interview_id, 1))
        self.assertEqual(len(self.calls), 1)
        self.assertTrue("event: existing" in left.text or "event: existing" in right.text)
        await self.answer(interview_id, 1)
        stale = await self.generate(interview_id, 1)
        self.assertIn("event: error", stale.text)
        self.assertEqual(len(self.calls), 1)

    async def test_missing_question_blank_answer_and_unauthenticated_access(self):
        interview_id = await self.create()
        self.assertEqual((await self.answer(interview_id, 1)).status_code, 409)
        await self.generate(interview_id, 1)
        self.assertEqual((await self.answer(interview_id, 1, "   ")).status_code, 422)
        self.assertEqual((await self.answer(interview_id, 2)).status_code, 409)
        await self.client.post("/api/logout")
        self.assertEqual((await self.client.get(f"/api/interviews/{interview_id}")).status_code, 401)

    async def test_missing_model_key_creates_no_question_or_performance_sample(self):
        interview_id = await self.create()
        with patch("app.main.settings", replace(main.settings, model_api_key="")):
            response = await self.generate(interview_id, 1)
        self.assertIn("event: error", response.text)
        self.assertIn("MODEL_API_KEY", response.text)
        self.assertEqual(self.calls, [])
        recovered = (await self.client.get(f"/api/interviews/{interview_id}")).json()
        self.assertEqual(recovered["rounds"], [])
        metrics = (await self.client.get("/api/performance")).json()
        self.assertEqual((metrics["totalFinished"], metrics["running"]), (0, 0))

    async def test_out_of_range_browser_latency_is_rejected(self):
        interview_id = await self.create()
        generated = await self.generate(interview_id, 1)
        meta = next(json.loads(line[6:]) for line in generated.text.splitlines() if line.startswith("data:"))
        for field in ("first_char_ms", "speech_start_ms"):
            for value in (-0.1, 120000.1):
                response = await self.client.post(
                    "/api/performance/browser",
                    json={"request_id": meta["requestId"], field: value},
                )
                self.assertEqual(response.status_code, 422)
        metrics = (await self.client.get("/api/performance")).json()
        self.assertEqual((metrics["firstCharSamples"], metrics["speechSamples"]), (0, 0))

    async def test_browser_metric_write_once_and_csv_export(self):
        interview_id = await self.create()
        generated = await self.generate(interview_id, 1)
        meta = next(json.loads(line[6:]) for line in generated.text.splitlines() if line.startswith("data:"))
        for value in (100, 1):
            result = await self.client.post("/api/performance/browser", json={
                "request_id": meta["requestId"], "first_char_ms": value, "speech_start_ms": value + 200,
            })
            self.assertEqual(result.status_code, 200)
        metrics = (await self.client.get("/api/performance")).json()
        self.assertEqual(metrics["firstCharP95Ms"], 100)
        self.assertEqual(metrics["speechStartP95Ms"], 300)
        csv = await self.client.get("/api/performance/export.csv")
        self.assertEqual(csv.status_code, 200)
        self.assertIn(meta["requestId"], csv.text)

    async def test_restart_preserves_data_and_marks_interrupted_generation(self):
        interview_id = await self.create()
        await self.generate(interview_id, 1)
        await self.answer(interview_id, 1)
        request_id = uuid.uuid4()
        async with main.app.state.pool.acquire() as conn:
            owner = await conn.fetchval("SELECT user_id FROM interviews WHERE id=$1", uuid.UUID(interview_id))
            await conn.execute(
                "INSERT INTO question_requests(id,user_id,interview_id,turn,status,model_name) VALUES($1,$2,$3,2,'running','fixture')",
                request_id, owner, uuid.UUID(interview_id),
            )
            await conn.execute(
                "INSERT INTO rounds(interview_id,turn,request_id,question_state) VALUES($1,2,$2,'generating')",
                uuid.UUID(interview_id), request_id,
            )
        await self.lifespan.__aexit__(None, None, None)
        self.lifespan = main.lifespan(main.app)
        await self.lifespan.__aenter__()
        recovered = (await self.client.get(f"/api/interviews/{interview_id}")).json()
        self.assertEqual(len(recovered["rounds"]), 1)
        self.assertIsNotNone(recovered["rounds"][0]["answer_text"])
        metrics = (await self.client.get("/api/performance")).json()
        self.assertEqual(metrics["failure"], 1)
        await self.generate(interview_id, 2)
        self.assertEqual((await self.client.get("/api/performance")).json()["retries"], 1)
