from langsmith import Client


def sync_datasets():
    print("Syncing LangSmith Datasets...")
    client = Client()

    # Define datasets and their seed examples
    datasets = {
        "intake-evals": [
            {
                "inputs": {"message": "I have a vintage Levi's 501 jacket, size medium, blue denim."},
                "outputs": {"attributes": {"brand": "Levi's", "size": "Medium", "color": "Blue", "category": "jacket"}}
            },
            {
                "inputs": {"message": "Selling an Apple iPad Pro 11-inch 2021 model, 128GB."},
                "outputs": {"attributes": {"brand": "Apple", "model": "iPad Pro 11-inch 2021", "storage": "128GB", "category": "tablet"}}
            }
        ],
        "pricing-evals": [
            {
                "inputs": {
                    "item_description": "Apple iPad Pro 11-inch 2021 128GB Wi-Fi",
                    "comparables": [
                        {"id": "c1", "title": "Apple iPad Pro 11-inch 2021 128GB Space Gray", "price": 500},
                        {"id": "c2", "title": "Apple iPad Pro 12.9-inch 2021 256GB", "price": 800}, # Different size/storage
                        {"id": "c3", "title": "Apple iPad Air 4th Gen 64GB", "price": 300}, # Not Pro
                        {"id": "c4", "title": "Apple iPad Pro 11 2021 128GB Silver", "price": 520}
                    ]
                },
                "outputs": {"relevant_ids": ["c1", "c4"]}
            }
        ],
        "publisher-evals": [
            {
                "inputs": {"description": "Men's blue Adidas running shoes, Ultraboost, size 10 US, slightly worn."},
                "outputs": {"specifics": {"Brand": "Adidas", "Color": "Blue", "US Shoe Size": "10", "Model": "Ultraboost"}}
            }
        ],
        "comms-evals": [
            {
                "inputs": {"message": "Will you take $50 for it?", "price": 80, "walk_away_price": 60},
                "outputs": {"action": "decline_offer"} # Or counter_offer > 60
            },
            {
                "inputs": {"message": "I can do $65 right now.", "price": 80, "walk_away_price": 60},
                "outputs": {"action": "accept_offer"} # Or counter_offer > 65
            },
            {
                "inputs": {"message": "Does this come with the original charger?", "price": 80, "walk_away_price": 60},
                "outputs": {"action": "send_info"}
            }
        ]
    }

    for name, examples in datasets.items():
        try:
            # Check if dataset exists
            if client.has_dataset(dataset_name=name):
                print(f"Dataset '{name}' already exists. Skipping creation.")
            else:
                dataset = client.create_dataset(
                    dataset_name=name,
                    description=f"Evaluation dataset for {name}"
                )
                print(f"Created dataset '{name}'. Adding {len(examples)} examples...")

                # Add examples
                for eg in examples:
                    client.create_example(
                        inputs=eg["inputs"],
                        outputs=eg["outputs"],
                        dataset_id=dataset.id
                    )
        except Exception as e:
            print(f"Error syncing dataset '{name}': {e}")

    print("Finished syncing datasets.")

if __name__ == "__main__":
    sync_datasets()
