.PHONY: setup run test bootstrap migrate demo clean

PYTHON ?= python

setup:
	@echo ">>> Cài đặt môi trường..."
	$(PYTHON) -m venv .venv
	./.venv/bin/pip install -r requirements/base.txt
	@if [ ! -f .env ]; then cp .env.example .env; fi
	cd site1 && ../.venv/bin/python manage.py bootstrap

run:
	cd site1 && ../.venv/bin/python manage.py runserver 127.0.0.1:8001

test:
	cd site1 && ../.venv/bin/python manage.py test training

bootstrap:
	cd site1 && ../.venv/bin/python manage.py bootstrap

demo:
	cd site1 && ../.venv/bin/python manage.py bootstrap --demo

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
