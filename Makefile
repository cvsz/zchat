VENV_PYTHON := $(wildcard $(CURDIR)/venv/bin/python $(CURDIR)/venv/Scripts/python.exe)
PYTHON ?= $(if $(VENV_PYTHON),$(firstword $(VENV_PYTHON)),python3)
RUFF := $(PYTHON) -m ruff
PYTEST := $(PYTHON) -m pytest

.PHONY: help venv setup validate validate-template format lint test test-zchat-client test-line build build-full build-full-signed build-all release release-unsigned security clean ci

help:
	@printf '%s\n' 'Project tasks:' \
		'  venv              Create virtual environment if it does not exist' \
		'  setup             Install the root and control-server development dependencies' \
		'  validate-template Run repository structure and Markdown link validation' \
		'  format            Check control-server formatting' \
		'  lint              Lint the control-server source and tests' \
		'  test              Run all Python test suites' \
		'  test-zchat-client Run ZChat client tests' \
		'  test-line         Run LINE feature tests' \
		'  build             Package Camfrog feature apps on Windows (build.bat)' \
		'  build-full        Full build: Camfrog + line-status-changer + zchat-client (full-build.bat)' \
		'  build-full-signed Full build + Authenticode signing (full-build-exe.bat)' \
		'  build-all         Alias for build-full-signed (default full build)' \
		'  release           Build full + sign + publish GitHub release (--release)' \
		'  release-unsigned  Build full + publish release without signing (--release)' \
		'  security          Audit root and installed Python dependencies' \
		'  clean             Remove cache directories' \
		'  ci                Run repository, format, lint, test, and security checks'

venv:
	@test -d $(CURDIR)/venv || python3 -m venv $(CURDIR)/venv

setup:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt
	$(PYTHON) -m pip install -e 'control-server[lint,test]'

validate-template:
	python3 scripts/validate_repo.py
	python3 -m unittest discover -s tests -p 'test_bootstrap.py' -v

validate: validate-template

format:
	$(RUFF) format --check control-server/src control-server/tests

lint:
	$(RUFF) check control-server/src control-server/tests

test:
ifeq ($(OS),Windows_NT)
	$(PYTEST) -q tests
else
	xvfb-run -a $(PYTEST) -q tests
endif
	cd control-server && $(PYTEST) -q
	$(MAKE) test-zchat-client
	$(MAKE) test-line

test-zchat-client:
ifeq ($(OS),Windows_NT)
	set "PYTHONPATH=zchat-client/src;control-server/src" && $(PYTEST) -q zchat-client/tests
else
	PYTHONPATH=zchat-client/src:control-server/src xvfb-run -a $(PYTEST) -q zchat-client/tests
endif

test-line:
	cd line && $(PYTHON) -m unittest discover -s tests -v

ifeq ($(OS),Windows_NT)
build:
	full-build.bat
else
build:
	@echo 'The desktop package build requires Windows; use the build-windows CI job.' >&2
	@exit 2
endif

# Full build: Camfrog feature apps + line-status-changer + zchat-client (unsigned)
ifeq ($(OS),Windows_NT)
build-full:
	full-build.bat
else
build-full:
	@echo 'The full desktop build requires Windows; use the build-windows CI job.' >&2
	@exit 2
endif

# Full build + Authenticode signing (SHA256 + RFC3161 timestamp)
# Requires: ZCODER_CERT_THUMBPRINT or ZCODER_PFX + ZCODER_PFX_PASSWORD env vars
ifeq ($(OS),Windows_NT)
build-full-signed:
	full-build-exe.bat
else
build-full-signed:
	@echo 'Authenticode signing requires Windows; use the build-windows CI job.' >&2
	@exit 2
endif

# Default full build = signed (production-ready)
build-all: build-full-signed

# Release: full build + sign + publish GitHub release
ifeq ($(OS),Windows_NT)
release:
	full-build-exe.bat --release
else
release:
	@echo 'Release requires Windows and gh auth login.' >&2
	@exit 2
endif

# Release without signing (for testing release flow)
ifeq ($(OS),Windows_NT)
release-unsigned:
	full-build.bat --release
else
release-unsigned:
	@echo 'Release requires Windows and gh auth login.' >&2
	@exit 2
endif

security:
	$(PYTHON) -m pip_audit -r requirements-dev.txt
	$(PYTHON) -m pip_audit --local --skip-editable

clean:
	find . \( -name .git -o -name .pytest_cache -o -name .ruff_cache -o -name .venv -o -name venv -o -name node_modules -o -name build -o -name dist \) -prune -o -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . \( -name .git -o -name .ruff_cache -o -name .venv -o -name venv -o -name node_modules -o -name build -o -name dist -o -name __pycache__ \) -prune -o -type d -name '.pytest_cache' -prune -exec rm -rf {} +
	find . \( -name .git -o -name .pytest_cache -o -name .venv -o -name venv -o -name node_modules -o -name build -o -name dist -o -name __pycache__ \) -prune -o -type d -name '.ruff_cache' -prune -exec rm -rf {} +

ci: validate-template format lint test security
