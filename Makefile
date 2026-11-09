PYTHON ?= python3
.PHONY: check test ng-test compile selfcheck compat sbom checksums
check: test ng-test compile compat selfcheck
test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest tests -q
ng-test:
	cd ../Crowdgpt-ng && PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest tests -q
compile:
	$(PYTHON) -m py_compile server.py orchestrator/*.py tools/*.py krum_stream.py
selfcheck:
	$(PYTHON) tools/operator_selfcheck.py
compat:
	$(PYTHON) tools/compat_check.py
sbom:
	$(PYTHON) tools/sbom.py > sbom.json
checksums:
	$(PYTHON) tools/checksums.py write dist/*
