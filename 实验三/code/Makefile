PYTHON ?= $(firstword $(wildcard .runtime/*/bin/python3) python3)

.PHONY: test check lint init gui test-gui

test:
	$(PYTHON) -m unittest discover -s tests -v

check:
	$(PYTHON) -m compileall -q hotel_app tests

lint:
	uv tool run ruff check hotel_app tests

init:
	$(PYTHON) -m hotel_app init

gui:
	$(PYTHON) -m hotel_app gui

test-gui:
	HOTEL_GUI_TESTS=1 $(PYTHON) -m unittest discover -s tests -v
