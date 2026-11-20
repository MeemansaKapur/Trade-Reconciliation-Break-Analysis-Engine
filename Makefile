PY := .venv/bin/python

setup:            ## create venv and install dependencies
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test:             ## run the automated UAT suite
	$(PY) -m pytest -q

run:              ## reconcile one business day (50K trades)
	$(PY) -m recon.engine --date 2026-11-20 --n 50000 --seed 42

evaluate:         ## precision / recall vs ground truth, 5 seeds x 4 rule versions
	$(PY) -m recon.evaluate

parity:           ## regenerate the three release parity reports
	$(PY) -m recon.parity --base v1.0 --candidate v1.1
	$(PY) -m recon.parity --base v1.1 --candidate v1.2
	$(PY) -m recon.parity --base v1.2 --candidate v1.3

workflow:         ## 15 business days with carry-forward, ageing, maker-checker
	$(PY) -m recon.workflow --start 2026-11-20 --days 15 --n 50000 --seed 42

export: workflow  ## dashboard CSVs + Excel break report
	$(PY) -m recon.export --workflow-dir "data/output/workflow_seed=42_n=50000_days=15_v1.3"

benchmark:        ## runtime at 50K / 250K / 1M trades
	$(PY) -m recon.benchmark

all: test run evaluate parity export benchmark
