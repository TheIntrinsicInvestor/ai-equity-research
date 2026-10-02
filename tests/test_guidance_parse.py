from pipeline.analysis.claude_analyzer import _parse_guidance_json


def test_plain_json_array():
    assert _parse_guidance_json('[{"metric": "revenue"}]') == [{"metric": "revenue"}]


def test_fenced_json():
    raw = '```json\n[{"metric": "eps"}]\n```'
    assert _parse_guidance_json(raw) == [{"metric": "eps"}]


def test_prose_wrapped_json():
    raw = 'Here is the JSON you asked for:\n```json\n[{"metric": "capex"}]\n```\nLet me know!'
    assert _parse_guidance_json(raw) == [{"metric": "capex"}]


def test_garbage_returns_empty():
    assert _parse_guidance_json("no json here") == []
    assert _parse_guidance_json("``` ```") == []
