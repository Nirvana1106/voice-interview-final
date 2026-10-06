import unittest
import uuid
import asyncio
from dataclasses import replace
from unittest.mock import patch

from pydantic import ValidationError

from app.main import BrowserMetricInput
from app.metrics import nearest_rank, summarize
from app.model import build_messages, first_usable_content, followup_prefix, guard_followup_stream
from app.security import hash_password, verify_password
from app.model import ModelError, stream_question
from app import model


class CoreLogicTests(unittest.TestCase):
    def test_nearest_rank_uses_success_sample_order(self):
        self.assertEqual(nearest_rank([10, 40, 20, 30], 0.5), 20)
        self.assertEqual(nearest_rank([10, 40, 20, 30], 0.95), 40)
        self.assertIsNone(nearest_rank([], 0.95))

    def test_empty_records_cannot_pass(self):
        result = summarize([])
        self.assertIsNone(result["successRate"])
        self.assertFalse(result["thresholdsPassed"])

    def test_performance_rejects_missing_browser_measurements(self):
        rows = [
            {
                "id": uuid.uuid4(), "status": "success", "turn": 1 if i % 3 == 0 else 2,
                "is_cold_start": i == 0, "is_retry": False,
                "model_ttft_ms": 900.0, "first_char_ms": 1800.0,
                "speech_start_ms": 2800.0,
            }
            for i in range(20)
        ]
        complete = summarize(rows)
        self.assertTrue(complete["evidenceComplete"])
        self.assertTrue(complete["thresholdsPassed"])
        rows[0]["speech_start_ms"] = None
        incomplete = summarize(rows)
        self.assertFalse(incomplete["evidenceComplete"])
        self.assertFalse(incomplete["thresholdsPassed"])
        self.assertIsNone(incomplete["speechStartP95Ms"])
        rows[0]["speech_start_ms"] = 2800
        rows.append({**rows[0], "id": uuid.uuid4(), "status": "running"})
        self.assertFalse(summarize(rows)["evidenceComplete"])

    def test_nineteen_successes_in_twenty_requests_meet_success_rate(self):
        rows = [
            {
                "id": uuid.uuid4(), "status": "success" if i < 19 else "failure",
                "turn": 1 if i % 3 == 0 else 2,
                "is_cold_start": i == 0, "is_retry": i == 19,
                "model_ttft_ms": 900.0, "first_char_ms": 1800.0,
                "speech_start_ms": 2800.0,
            }
            for i in range(20)
        ]
        result = summarize(rows)
        self.assertEqual(result["successRate"], 0.95)
        self.assertTrue(result["thresholdsPassed"])
        rows[0]["model_ttft_ms"] = 5000.001
        self.assertFalse(summarize(rows)["thresholdsPassed"])

    def test_browser_speech_error_counts_as_failed_attempt(self):
        rows = [
            {
                "id": uuid.uuid4(), "status": "success", "turn": 1 if i % 3 == 0 else 2,
                "is_cold_start": i == 0, "is_retry": False,
                "model_ttft_ms": 900.0, "first_char_ms": 1800.0,
                "speech_start_ms": 2800.0, "browser_error": None,
            }
            for i in range(20)
        ]
        rows[0]["browser_error"] = "语音播报失败"
        report = summarize(rows)
        self.assertEqual((report["success"], report["failure"]), (19, 1))
        self.assertEqual(report["successRate"], 0.95)
        self.assertTrue(report["thresholdsPassed"])
        rows[1]["browser_error"] = "首字呈现时页面不可见"
        self.assertFalse(summarize(rows)["thresholdsPassed"])

    def test_followup_uses_confirmed_answer(self):
        interview = {
            "role": "后端开发工程师", "job_description": "开发订单接口",
            "experience_summary": "做过预约系统",
        }
        messages = build_messages(interview, [{
            "turn": 1, "question_text": "怎样避免超额预约？",
            "answer_text": "我使用了行级锁，并验证并发请求。",
        }], 2)
        self.assertIn("行级锁", messages[1]["content"])
        self.assertIn("引用上一条已确认回答", messages[1]["content"])

    def test_followup_does_not_attribute_resume_or_question_to_answer(self):
        messages = build_messages({
            "role": "后端开发", "job_description": "岗位中需要数据库事务",
            "experience_summary": "使用行级锁保护时段记录",
        }, [{"turn": 1, "question_text": "怎样使用行级锁？", "answer_text": "可以听见吗？"}], 2)
        content = messages[1]["content"]
        self.assertIn(followup_prefix("可以听见吗？"), content)
        self.assertNotIn("行级锁", content)
        self.assertNotIn("数据库事务", content)
        self.assertIn("试音", content)

    def test_followup_guard_handles_split_quote_and_preserves_raw_ttft(self):
        prefix = followup_prefix("我使用行级锁避免超额预约。")

        async def source():
            yield prefix[:3], 123.0
            yield prefix[3:], None
            yield "请说明怎样验证并发安全？", None
            yield "用了哪些测试？", None

        async def collect():
            return [item async for item in guard_followup_stream(source(), prefix)]

        values = asyncio.run(collect())
        self.assertEqual(values, [(prefix + "请说明怎样验证并发安全？", 123.0), ("用了哪些测试？", None)])

    def test_followup_guard_rejects_false_quote_before_display_and_closes_source(self):
        visible, closed = [], []

        async def source():
            try:
                yield "你提到使用行级锁", 123.0
                yield "请说明实现？", None
            finally:
                closed.append(True)

        async def collect():
            async for item in guard_followup_stream(source(), followup_prefix("可以听见吗？")):
                visible.append(item)

        with self.assertRaises(ModelError):
            asyncio.run(collect())
        self.assertEqual(visible, [])
        self.assertEqual(closed, [True])

    def test_followup_guard_rejects_quote_without_question(self):
        prefix = followup_prefix("我使用行级锁。")

        async def source():
            yield prefix, 123.0
            yield "  ", None

        async def collect():
            return [item async for item in guard_followup_stream(source(), prefix)]

        with self.assertRaises(ModelError):
            asyncio.run(collect())

    def test_other_rounds_stream_without_quote_buffer(self):
        async def source():
            yield "第一题", 123.0
            yield "正文", None

        async def collect():
            return [item async for item in guard_followup_stream(source(), None)]

        self.assertEqual(asyncio.run(collect()), [("第一题", 123.0), ("正文", None)])

    def test_thinking_placeholder_is_not_a_question_delta(self):
        self.assertIsNone(first_usable_content("正"))
        self.assertIsNone(first_usable_content("正在思考…"))
        self.assertEqual(first_usable_content("正在思考…\n请说明取舍？"), "请说明取舍？")
        self.assertEqual(first_usable_content("  问题正文"), "  问题正文")
        self.assertEqual(first_usable_content("正在思考能力方面，你如何提升？"),
                         "正在思考能力方面，你如何提升？")

    def test_password_hash_is_salted_and_verifiable(self):
        first = hash_password("example-password")
        second = hash_password("example-password")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("example-password", first))
        self.assertFalse(verify_password("wrong-password", first))

    def test_browser_metric_rejects_nonfinite_latency(self):
        for field in ("first_char_ms", "speech_start_ms"):
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        BrowserMetricInput(request_id=uuid.uuid4(), **{field: value})

    def test_stream_parser_counts_first_nonempty_content_and_requires_done(self):
        class FakeResponse:
            status_code = 200

            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None

            async def aiter_lines(self):
                for line in (
                    "data: {\"choices\":[{\"delta\":{\"role\":\"assistant\"}}]}",
                    "data: {\"choices\":[{\"delta\":{\"content\":\"  \"}}]}",
                    "data: {\"choices\":[{\"delta\":{\"content\":\"正在\"}}]}",
                    "data: {\"choices\":[{\"delta\":{\"content\":\"思考…\\n\"}}]}",
                    "data: {\"choices\":[{\"delta\":{\"content\":\"问题正文\"}}]}",
                    "data: [DONE]",
                ):
                    yield line

        class FakeClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            def stream(self, *args, **kwargs): return FakeResponse()

        async def collect():
            values = []
            async for value in stream_question([{"role": "user", "content": "问"}]): values.append(value)
            return values

        with patch("app.model.httpx.AsyncClient", FakeClient), patch(
            "app.model.settings", replace(model.settings, model_api_key="test-key"),
        ):
            values = asyncio.run(collect())
        self.assertEqual(values[0][0], "问题正文")
        self.assertIsNotNone(values[0][1])

    def test_stream_parser_rejects_missing_done(self):
        class FakeResponse:
            status_code = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def aiter_lines(self):
                yield "data: {\"choices\":[{\"delta\":{\"content\":\"问题\"}}]}"

        class FakeClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            def stream(self, *args, **kwargs): return FakeResponse()

        async def collect():
            result = []
            async for value in stream_question([{"role": "user", "content": "问"}]): result.append(value)
            return result

        with patch("app.model.httpx.AsyncClient", FakeClient), patch(
            "app.model.settings", replace(model.settings, model_api_key="test-key"),
        ):
            with self.assertRaises(ModelError): asyncio.run(collect())


if __name__ == "__main__":
    unittest.main()
