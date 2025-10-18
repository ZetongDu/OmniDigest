.PHONY: dev digest-ai digest-finance

PYTHONPATH?=src
DEV_CMD=uvicorn omnidigest.main:app --reload --host 0.0.0.0 --port 8000
DIGEST_CMD=python -m omnidigest.pipeline.run_digest

dev:
	PYTHONPATH=$(PYTHONPATH) $(DEV_CMD)

digest-ai:
	PYTHONPATH=$(PYTHONPATH) $(DIGEST_CMD) --domain ai

digest-finance:
	PYTHONPATH=$(PYTHONPATH) $(DIGEST_CMD) --domain finance
