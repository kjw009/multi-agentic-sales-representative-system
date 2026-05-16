import json
import uuid
from types import SimpleNamespace

import pytest

from packages.agents.intake import vision
from packages.db.models import Item, ItemCondition, ItemImage


def _image(position: int) -> ItemImage:
    return ItemImage(
        id=uuid.uuid4(),
        item_id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        s3_key=f"items/{position}.jpg",
        url=f"https://example.com/{position}.jpg",
        position=position,
    )


def test_parse_visual_condition_response_handles_fenced_json():
    raw = """```json
{"condition_grade":"good","confidence":0.82,"visible_defects":[{"type":"scuff","location":"heel","severity":"minor","evidence":"light wear"}],"visual_descriptors":[{"name":"metal colour","value":"silver-tone","confidence":0.7}],"photo_quality":"clear","description_addendum":"Minor heel scuffing is visible.","descriptor_addendum":"Silver-tone band with clear stones visible.","pricing_signals":["minor_scuffs"],"comparable_include_terms":["used good"],"comparable_exclude_terms":["new","sealed"]}
```"""

    report = vision.parse_visual_condition_response(raw)

    assert report["condition_grade"] == "good"
    assert report["confidence"] == pytest.approx(0.82)
    assert report["visible_defects"][0]["type"] == "scuff"
    assert report["visual_descriptors"][0]["value"] == "silver-tone"
    assert report["descriptor_addendum"] == "Silver-tone band with clear stones visible."
    assert report["comparable_exclude_terms"] == ["new", "sealed"]


def test_image_urls_for_analysis_caps_and_orders_images():
    images = [_image(i) for i in [8, 2, 4, 1, 7, 3, 5, 6]]

    urls = vision.image_urls_for_analysis(images)

    assert urls == [f"https://example.com/{i}.jpg" for i in [1, 2, 3, 4, 5, 6]]


@pytest.mark.asyncio
async def test_analyse_item_images_handles_invalid_json(monkeypatch):
    class FakeResponses:
        async def create(self, **kwargs):
            return SimpleNamespace(output_text="not json")

    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

    monkeypatch.setattr("packages.agents.intake.vision.openai.AsyncOpenAI", FakeClient)

    item = Item(
        id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        name="Nike trainers",
        category="Trainers",
        condition=ItemCondition.good,
        description="Good condition.",
    )

    report = await vision.analyse_item_images(item, [_image(0)])

    assert report["confidence"] == 0.0
    assert report["analysis_error"] == "JSONDecodeError"


@pytest.mark.asyncio
async def test_analyse_item_images_sends_ordered_capped_urls(monkeypatch):
    captured = {}

    class FakeResponses:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_text=json.dumps(
                    {
                        "condition_grade": "good",
                        "confidence": 0.8,
                        "visible_defects": [],
                        "photo_quality": "usable",
                        "description_addendum": "",
                        "pricing_signals": [],
                        "comparable_include_terms": [],
                        "comparable_exclude_terms": [],
                    }
                )
            )

    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

    monkeypatch.setattr("packages.agents.intake.vision.openai.AsyncOpenAI", FakeClient)

    item = Item(
        id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        name="Nike trainers",
        category="Trainers",
        condition=ItemCondition.good,
        description="Good condition.",
    )
    images = [_image(i) for i in [9, 0, 2, 4, 1, 3, 5]]

    await vision.analyse_item_images(item, images)

    user_content = captured["input"][1]["content"]
    image_urls = [part["image_url"] for part in user_content if part["type"] == "input_image"]
    assert image_urls == [f"https://example.com/{i}.jpg" for i in [0, 1, 2, 3, 4, 5]]


def test_apply_visual_report_to_item_stores_descriptors_and_appends_notes():
    item = Item(
        id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        name="Unbranded ring",
        category="Jewellery",
        condition=ItemCondition.good,
        description="Unbranded ring.",
    )
    report = {
        "condition_grade": "good",
        "confidence": 0.84,
        "visible_defects": [],
        "visual_descriptors": [
            {"name": "metal colour", "value": "silver-tone", "confidence": 0.75}
        ],
        "photo_quality": "clear",
        "description_addendum": "Light surface wear is visible.",
        "descriptor_addendum": "Silver-tone band with clear stones visible.",
        "pricing_signals": ["silver_tone"],
        "comparable_include_terms": ["silver-tone ring"],
        "comparable_exclude_terms": ["gold ring"],
    }

    vision.apply_visual_report_to_item(item, report)

    assert item.visual_condition_report == report
    assert item.attributes["visual_descriptors"][0]["value"] == "silver-tone"
    assert item.attributes["visual_condition"]["pricing_signals"] == ["silver_tone"]
    assert "Light surface wear" in item.description
    assert "Silver-tone band" in item.description
