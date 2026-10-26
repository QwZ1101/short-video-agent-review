import io
import json
import unittest

from review_tutorial import ModelNetworkError, read_sse_response


def event(value):
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode("utf-8")


class StreamTests(unittest.TestCase):
    def test_content_joined_and_usage_only_chunk_not_used_as_answer(self):
        raw = b": heartbeat\n\n" + event({"id": "req", "choices": [{"index": 0, "delta": {"content": '{"说明":"'}}]})
        raw += event({"choices": [{"index": 0, "delta": {"content": '复制网址"}'}, "finish_reason": "stop"}]})
        raw += event({"choices": [], "usage": {"total_tokens": 12}}) + b"data: [DONE]\n\n"
        result = read_sse_response(io.BytesIO(raw))
        self.assertEqual(json.loads(result["choices"][0]["message"]["content"]), {"说明": "复制网址"})
        self.assertEqual(result["usage"]["total_tokens"], 12)
        self.assertEqual(result["transport"]["chunks"], 3)

    def test_empty_array_stays_invalid_not_fabricated_into_object(self):
        raw = event({"choices": [{"delta": {"content": "[]"}, "finish_reason": "stop"}]}) + b"data: [DONE]\n\n"
        self.assertEqual(read_sse_response(io.BytesIO(raw))["choices"][0]["message"]["content"], "[]")

    def test_eof_without_done_preserves_partial_and_fails(self):
        raw = event({"choices": [{"delta": {"content": "partial"}, "finish_reason": "stop"}]})
        with self.assertRaises(ModelNetworkError) as caught:
            read_sse_response(io.BytesIO(raw))
        self.assertEqual(caught.exception.partial_content, "partial")

    def test_missing_finish_reason_fails(self):
        with self.assertRaises(ModelNetworkError):
            read_sse_response(io.BytesIO(b"data: [DONE]\n\n"))

    def test_length_finish_is_preserved_for_caller_rejection(self):
        raw = event({"choices": [{"delta": {"content": "{"}, "finish_reason": "length"}]}) + b"data: [DONE]\n\n"
        self.assertEqual(read_sse_response(io.BytesIO(raw))["choices"][0]["finish_reason"], "length")

    def test_error_event_rejected(self):
        with self.assertRaisesRegex(ValueError, "错误事件"):
            read_sse_response(io.BytesIO(event({"error": {"code": "test"}})))


if __name__ == "__main__":
    unittest.main()
