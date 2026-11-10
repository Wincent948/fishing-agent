.PHONY: data index test eval eval-test ablate api freeze

data:
	python scripts/make_synthetic_data.py

freeze:
	cd eval && python -m authoring.build_authored && python freeze_gold.py --synthetic-dir ../data/synthetic --version syn-v1

index:
	python -m app.kb.index

test:
	python -m pytest -q tests

eval:
	python -m eval.runner

eval-test:
	python -m eval.runner --split test

ablate:
	python -m eval.ablate_retrieval

api:
	uvicorn app.api.server:app --reload
