from airflow.models import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
import pendulum
import datetime

with DAG(
    dag_id="launch_rocket",
    start_date=pendulum.today('UTC').add(days=-90),
    description="This DAG will launch our rocket.",
    schedule=datetime.timedelta(days=3)
):

    procure_rocket_material = EmptyOperator(task_id="procure_rocket_material")
    procure_fuel = EmptyOperator(task_id="procure_fuel")
    build = [BashOperator(task_id=f"build_stage_{i}", bash_command="echo '{{ task }} is running in the {{ dag }} pipeline'") for i in range(1, 4)]

    def _print_exec_date(**context):
        print(f"This script was executed at {context['execution_date']}")
        print(f"Three days after execution is {context['next_execution_date']}")
        print(f"This script run date is {context['ds']}")

    launch = PythonOperator(task_id="launch", python_callable=_print_exec_date, provide_context=True)

    procure_rocket_material >> build
    [procure_rocket_material, procure_fuel] >> build[-1]
    build >> launch