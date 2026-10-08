.PHONY: api agent install test

install:
	python -m pip install -r requirements.txt

api:
	python -m src.api_server

agent:
	python -m src.agent_worker dev

test:
	python -m pytest -q
