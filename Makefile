.PHONY: install data detect figures load test all

install:
	pip install -r requirements.txt

data:        ## regenerate the synthetic dataset (seed 42)
	python -m aml_graph.generate --out data

detect:      ## run the offline detections and write reports/
	python -m aml_graph.detect --data data --out reports

figures:     ## render README figures
	python -m aml_graph.visualize --data data --out docs/images

load:        ## load the dataset into a running Neo4j (docker compose up -d first)
	python -m aml_graph.load_neo4j --reset

test:
	python -m pytest -q

all: data detect figures test
