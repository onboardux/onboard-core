from airflow import DAG
from datetime import datetime

with DAG('daily_load', start_date=datetime(2026, 1, 1)) as dag:
    pass
