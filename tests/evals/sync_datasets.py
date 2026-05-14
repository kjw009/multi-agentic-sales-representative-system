"""Seed/refresh LangSmith evaluation datasets.

Run via `make evals-sync` (or `python tests/evals/sync_datasets.py`).
Pass `--refresh` to wipe existing examples and re-upload — useful when
the schema below changes."""

import argparse

from langsmith import Client

from packages.config import configure_tracing

configure_tracing()


# Datasets keyed by name. Each example's `inputs` and `outputs` must line up
# with the matching `*_target` function in test_eval_*.py.
DATASETS: dict[str, list[dict]] = {
    # Intake records attributes via the `record_attribute` tool. Allowed
    # fields are: name, brand, category, subcategory, condition, age_months,
    # description, seller_floor_price. The eval only checks fields the agent
    # is actually capable of recording — anything else (size, storage, colour)
    # would be saved later as `record_item_specific` in the `attributes` JSON.
    "intake-evals": [
        {
            "inputs": {
                "message": "I have a vintage Levi's 501 jacket, size medium, blue denim. Used but in good condition."
            },
            "outputs": {
                "attributes": {"brand": "Levi's", "category": "Clothing", "condition": "good"}
            },
        },
        {
            "inputs": {
                "message": "Selling an Apple iPad Pro 11-inch 2021 model, 128GB. Like new, barely used."
            },
            "outputs": {
                "attributes": {"brand": "Apple", "category": "Tablets", "condition": "like_new"}
            },
        },
        {
            "inputs": {
                "message": "Got a pair of Adidas Ultraboost trainers, UK 9, worn a few times."
            },
            "outputs": {
                "attributes": {"brand": "Adidas", "category": "Trainers", "condition": "good"}
            },
        },
    ],
    "pricing-evals": [
        {
            "inputs": {
                "item_title": "Apple iPad Pro 11-inch 2021 128GB Wi-Fi",
                "item_description": "Apple iPad Pro 11-inch 2021 128GB Wi-Fi",
                "item_category": "Tablets",
                "item_brand": "Apple",
                "comparables": [
                    {
                        "id": "c1",
                        "title": "Apple iPad Pro 11-inch 2021 128GB Space Gray",
                        "price": 500,
                    },
                    {
                        "id": "c2",
                        "title": "Apple iPad Pro 12.9-inch 2021 256GB",
                        "price": 800,
                    },
                    {"id": "c3", "title": "Apple iPad Air 4th Gen 64GB", "price": 300},
                    {
                        "id": "c4",
                        "title": "Apple iPad Pro 11 2021 128GB Silver",
                        "price": 520,
                    },
                    {
                        "id": "c5",
                        "title": "Smart Folio Case for iPad Pro 11-inch",
                        "price": 35,
                    },
                ],
            },
            "outputs": {"relevant_ids": ["c1", "c4"]},
        },
        {
            "inputs": {
                "item_title": "Sony WH-1000XM4 Wireless Noise-Cancelling Headphones Black",
                "item_description": "Sony WH-1000XM4 over-ear wireless headphones, black",
                "item_category": "Headphones",
                "item_brand": "Sony",
                "comparables": [
                    {
                        "id": "h1",
                        "title": "Sony WH-1000XM4 Wireless Headphones Black",
                        "price": 220,
                    },
                    {
                        "id": "h2",
                        "title": "Sony WH-1000XM5 Wireless Headphones",
                        "price": 320,
                    },
                    {
                        "id": "h3",
                        "title": "Replacement ear pads for Sony WH-1000XM4",
                        "price": 18,
                    },
                    {
                        "id": "h4",
                        "title": "Sony WH-1000XM4 Headphones (For parts, not working)",
                        "price": 60,
                    },
                ],
            },
            "outputs": {"relevant_ids": ["h1"]},
        },
        {
            "inputs": {
                "item_title": "Nintendo Switch OLED Console White 64GB",
                "item_description": "Nintendo Switch OLED model, white Joy-Cons, 64GB",
                "item_category": "Gaming Consoles",
                "item_brand": "Nintendo",
                "comparables": [
                    {
                        "id": "n1",
                        "title": "Nintendo Switch OLED Console White",
                        "price": 280,
                    },
                    {
                        "id": "n2",
                        "title": "Nintendo Switch Lite Turquoise",
                        "price": 160,
                    },
                    {
                        "id": "n3",
                        "title": "Carrying Case for Nintendo Switch OLED",
                        "price": 20,
                    },
                ],
            },
            "outputs": {"relevant_ids": ["n1"]},
        },
    ],
    # Publisher's `infer_specifics` reads the description and fills aspect
    # values. Aspects come from the dataset so we can mix categories.
    "publisher-evals": [
        {
            "inputs": {
                "name": "Adidas Ultraboost Running Shoes",
                "category": "Trainers",
                "description": "Men's blue Adidas Ultraboost running shoes, US size 10, worn a few times.",
                "aspects": [
                    {
                        "name": "Brand",
                        "required": True,
                        "cardinality": "SINGLE",
                        "enum_values": ["Nike", "Adidas", "Puma"],
                    },
                    {
                        "name": "Color",
                        "required": False,
                        "cardinality": "SINGLE",
                        "enum_values": ["Red", "Blue", "Black", "White"],
                    },
                    {
                        "name": "US Shoe Size",
                        "required": True,
                        "cardinality": "SINGLE",
                        "enum_values": ["8", "9", "10", "11", "12"],
                    },
                    {
                        "name": "Model",
                        "required": False,
                        "cardinality": "SINGLE",
                        "enum_values": ["Ultraboost", "Air Max", "Suede"],
                    },
                ],
            },
            "outputs": {
                "specifics": {
                    "Brand": "Adidas",
                    "Color": "Blue",
                    "US Shoe Size": "10",
                    "Model": "Ultraboost",
                }
            },
        },
        {
            "inputs": {
                "name": "Apple iPhone 13",
                "category": "Phones",
                "description": "Apple iPhone 13, 128GB, midnight black, unlocked. Very good condition.",
                "aspects": [
                    {
                        "name": "Brand",
                        "required": True,
                        "cardinality": "SINGLE",
                        "enum_values": ["Apple", "Samsung", "Google"],
                    },
                    {
                        "name": "Storage Capacity",
                        "required": True,
                        "cardinality": "SINGLE",
                        "enum_values": ["64 GB", "128 GB", "256 GB", "512 GB"],
                    },
                    {
                        "name": "Network",
                        "required": False,
                        "cardinality": "SINGLE",
                        "enum_values": ["Unlocked", "EE", "O2", "Vodafone"],
                    },
                ],
            },
            "outputs": {
                "specifics": {
                    "Brand": "Apple",
                    "Storage Capacity": "128 GB",
                    "Network": "Unlocked",
                }
            },
        },
    ],
    # Comms agent receives an NLP-pre-classified message + price floor and
    # must pick an action. The `intent` here matches the real INTENT_LABELS
    # produced by packages/agents/nlp/intent.py.
    "comms-evals": [
        {
            "inputs": {
                "message": "Will you take $50 for it?",
                "intent": "price_offer",
                "offer_amounts": [50.0],
                "price": 80.0,
                "walk_away_price": 60.0,
            },
            "outputs": {"action": "decline_offer"},
        },
        {
            "inputs": {
                "message": "I can do $65 right now.",
                "intent": "price_offer",
                "offer_amounts": [65.0],
                "price": 80.0,
                "walk_away_price": 60.0,
            },
            # Either accept or counter (>= walk_away) is acceptable.
            "outputs": {
                "action": "accept_offer",
                "allowed_actions": ["accept_offer", "counter_offer"],
            },
        },
        {
            "inputs": {
                "message": "Does this come with the original charger?",
                "intent": "question",
                "offer_amounts": [],
                "price": 80.0,
                "walk_away_price": 60.0,
            },
            "outputs": {"action": "send_info"},
        },
        {
            "inputs": {
                "message": "Hi! Thanks for listing this.",
                "intent": "greeting",
                "offer_amounts": [],
                "price": 80.0,
                "walk_away_price": 60.0,
            },
            "outputs": {"action": "send_info"},
        },
    ],
}


def _wipe_examples(client: Client, dataset_id: str) -> int:
    """Delete every example in a dataset. Returns count deleted."""
    examples = list(client.list_examples(dataset_id=dataset_id))
    for ex in examples:
        client.delete_example(example_id=ex.id)
    return len(examples)


def sync_datasets(refresh: bool = False) -> None:
    print("Syncing LangSmith datasets...")
    client = Client()

    for name, examples in DATASETS.items():
        try:
            if client.has_dataset(dataset_name=name):
                dataset = client.read_dataset(dataset_name=name)
                if refresh:
                    deleted = _wipe_examples(client, str(dataset.id))
                    print(f"Dataset '{name}': wiped {deleted} existing examples.")
                else:
                    existing = sum(1 for _ in client.list_examples(dataset_id=str(dataset.id)))
                    if existing >= len(examples):
                        print(
                            f"Dataset '{name}': {existing} examples already present "
                            "— pass --refresh to overwrite."
                        )
                        continue
                    print(
                        f"Dataset '{name}': only {existing} examples found, "
                        f"adding the missing {len(examples) - existing}."
                    )
                    examples = examples[existing:]
            else:
                dataset = client.create_dataset(
                    dataset_name=name,
                    description=f"Evaluation dataset for {name}",
                )
                print(f"Created dataset '{name}'.")

            print(f"Adding {len(examples)} examples to '{name}'...")
            for eg in examples:
                client.create_example(
                    inputs=eg["inputs"],
                    outputs=eg["outputs"],
                    dataset_id=dataset.id,
                )
        except Exception as e:
            print(f"Error syncing dataset '{name}': {e}")

    print("Finished syncing datasets.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Wipe existing examples and re-upload from this file.",
    )
    args = parser.parse_args()
    sync_datasets(refresh=args.refresh)
