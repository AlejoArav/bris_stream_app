.PHONY: install run scrape schedule docker-up

install:
	python -m pip install -r requirements.txt

run:
	streamlit run app.py

scrape:
	python -m housing_dashboard.scrapers.run_all --sources sources.yaml

schedule:
	python -m housing_dashboard.scrapers.scheduler --sources sources.yaml

docker-up:
	docker compose up --build
