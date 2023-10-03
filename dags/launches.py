from airflow.models import DAG
from airflow.operators.python import PythonOperator
from airflow.sensors.http_sensor import HttpSensor
from airflow.operators.http_operator import SimpleHttpOperator
from airflow.exceptions import AirflowSkipException
from airflow.providers.google.cloud.operators.bigquery import BigQueryCreateEmptyDatasetOperator, BigQueryCreateEmptyTableOperator
from airflow.providers.google.cloud.transfers.local_to_gcs import LocalFilesystemToGCSOperator
from airflow.providers.google.cloud.transfers.gcs_to_bigquery import GCSToBigQueryOperator
from airflow.providers.postgres.operators.postgres import PostgresOperator
import pendulum
import json
import pandas as pd

GOOGLE_CLOUD_CONNECTION = "google_cloud_connection"
GOOGLE_CLOUD_PROJECT_ID = "aflow-training-rabo-2023-10-02"
GOOGLE_CLOUD_DATASET_ID = "ws_dataset"
GOOGLE_CLOUD_TABLE_ID = "daily_rocket_launches"

with DAG(
    dag_id="launches_getter",
    start_date=pendulum.today('UTC').add(days=-10),
    description="This DAG gets today's launches.",
    schedule="@daily"
) as dag:

    check_api_status = HttpSensor(
        task_id="check_api_status",
        http_conn_id="thespacedevs_dev",
        endpoint="",
        request_params={},
        response_check=lambda response: response.status_code == 200,
        poke_interval=5,
        timeout=200,
        dag=dag,
    )

    pull_data_from_api = SimpleHttpOperator(
        task_id="pull_data_from_api",
        http_conn_id="thespacedevs_dev",
        endpoint="",
        method="GET",
        data={
            "net__gte": "{{ ds }}T00:00:00Z",
            "net__lt": "{{ next_ds }}T00:00:00Z"
        },
        response_check=lambda response: response.json(),
        log_response=True,
        dag=dag,
    )

    def _check_if_data_in_request(task_instance, **context):
        response = task_instance.xcom_pull(task_ids="pull_data_from_api", key="return_value")
        response_dict = json.loads(response)
    
        if response_dict["count"] == 0:
            raise AirflowSkipException(f"No data found on date {context['ds']}")
    
    check_data_in_a_day = PythonOperator(
        task_id="check_data_in_a_day",
        python_callable=_check_if_data_in_request,
        provide_context=True,
        dag=dag,
    )

    def _extract_relevant_data(x: dict):
        return {
            "id": x.get("id"),
            "name": x.get("name"),
            "status": x.get("status"),
            "country_code": x.get("pad").get("country_code"),
            "service_provider_name": x.get("launch_service_provider").get("name"),
            "service_provider_type": x.get("launch_service_provider").get("type"),
        }


    def _preprocess_data(task_instance, **context):
        response = task_instance.xcom_pull(task_ids="pull_data_from_api")
        response_dict = json.loads(response)
        response_results = response_dict["results"]
        df_results = pd.DataFrame([_extract_relevant_data(i) for i in response_results])
        df_results.to_parquet(path="/tmp/todays_results.parquet")

    preprocess_data = PythonOperator(
        task_id="preprocess_data",
        python_callable=_preprocess_data
    )

    create_empty_bigquery_dataset = BigQueryCreateEmptyDatasetOperator(
        task_id="create_empty_bigquery_dataset",
        gcp_conn_id = GOOGLE_CLOUD_CONNECTION,
        project_id=GOOGLE_CLOUD_PROJECT_ID,
        dataset_id=GOOGLE_CLOUD_DATASET_ID,
    )

    create_empty_bigquery_table = BigQueryCreateEmptyTableOperator(
        task_id="create_empty_bigquery_table",
        gcp_conn_id=GOOGLE_CLOUD_CONNECTION,
        project_id=GOOGLE_CLOUD_PROJECT_ID,
        dataset_id=GOOGLE_CLOUD_DATASET_ID,
        table_id=GOOGLE_CLOUD_TABLE_ID,
        schema_fields=[
            {"name": "id", "type": "STRING", "mode": "REQUIRED"},
            {"name": "name", "type": "STRING", "mode": "REQUIRED"},
            {"name": "status", "type": "STRING", "mode": "REQUIRED"},
            {"name": "country_code", "type": "STRING", "mode": "REQUIRED"},
            {"name": "service_provider_name", "type": "STRING", "mode": "REQUIRED"},
            {"name": "service_provider_type", "type": "STRING", "mode": "REQUIRED"},
        ],
    )

    upload_parquet_to_gcs = LocalFilesystemToGCSOperator(
        tastk_id="upload_parquet_to_gcs",
        gcp_conn_id=GOOGLE_CLOUD_CONNECTION,
        src="/tmp/todays_results.parquet",
        dst="wouter/{{ ds }}.parquet",
        bucket=GOOGLE_CLOUD_PROJECT_ID,
    )

    write_parquet_to_bq = GCSToBigQueryOperator(
        task_id="write_parquet_to_bq",
        gcp_conn_id=GOOGLE_CLOUD_CONNECTION,
        bucket=GOOGLE_CLOUD_PROJECT_ID,
        source_objects="wouter/{{ ds }}.parquet",
        source_format="parquet",
        destination_project_dataset_table=f"{GOOGLE_CLOUD_DATASET_ID}.{GOOGLE_CLOUD_TABLE_ID}"
        write_disposition="WRITE_APPEND"
    )

    create_postgres_table = PostgresOperator(
        task_id="create_postgres_table",
        postgres_comm_id="postgres",
        sql="""
        CREATE TABLE IF NOT EXISTS rocket_launches (
            id SERIAL PRIMARY KEY,
            name VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            country_code VARCHAR NOT NULL,
            service_provider_name VARCHAR NOT NULL,
            service_provider_type VARCHAR NOT NULL
        );
        """
    )

    bigquery_to_postgres = BigQueryToPostgresOperator(
        task_id="bigquery_to_postgres",
        postgres_conn_id="postgres"
        dataset_table=f"{GOOGLE_CLOUD_DATASET_ID}.{GOOGLE_CLOUD_TABLE_ID}",
        target_table_name="rocket_launches",
        replace=False,
    )

    (
        check_api_status >>
        pull_data_from_api >>
        check_data_in_a_day >>
        preprocess_data >>
        create_empty_bigquery_dataset >>
        create_empty_bigquery_table >>
        upload_parquet_to_gcs >>
        write_parquet_to_bq >>
        create_postgres_table
    )