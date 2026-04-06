SHELL := /bin/bash

.PHONY: help lumin nanoclaw desktop-agent stack nanoclaw-image test

help:
	@echo "Available commands:"
	@echo "  make lumin           Start the Lumin FastAPI server"
	@echo "  make nanoclaw        Start NanoClaw in dev mode"
	@echo "  make desktop-agent   Start the local desktop NanoClaw worker"
	@echo "  make stack           Start Lumin + NanoClaw + desktop worker together"
	@echo "  make nanoclaw-image  Build the NanoClaw agent Docker image"
	@echo "  make test            Run the Lumin Python test suite"

lumin:
	@bash ./scripts/dev-lumin.sh

nanoclaw:
	@bash ./scripts/dev-nanoclaw.sh

desktop-agent:
	@bash ./scripts/dev-desktop-agent.sh

stack:
	@bash ./scripts/dev-stack.sh

nanoclaw-image:
	@bash ./scripts/build-nanoclaw-image.sh

test:
	@python3 -m unittest discover -s tests -p 'test_*.py'
